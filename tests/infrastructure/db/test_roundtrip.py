"""实体经仓储写入、读出后应与原值相等。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gelinspect.domain.entities import AuditEvent, TubeResult, User
from gelinspect.domain.enums import (
    AuditAction,
    ExperimentType,
    GelState,
    Interpretation,
    ReviewStatus,
    Role,
    TubeAnalysisStatus,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.infrastructure.db import Database
from gelinspect.infrastructure.db.passwords import hash_password, verify_password
from gelinspect.interfaces.storage import (
    DeviceLogEntry,
    DuplicateError,
    MediaFile,
    MediaKind,
    NotFoundError,
    PageRequest,
    ReportRecord,
    TemperatureSample,
)

from .conftest import T0, make_event, make_experiment, make_result

SHA = "a" * 64


def test_user_roundtrip_and_credentials(database: Database) -> None:
    user = User("u1", "alice", "爱丽丝", Role.ANALYST)
    database.users.add(user, password_hash=hash_password("secret-pass"), must_change_password=True)
    assert database.users.get("u1") == user
    assert database.users.get_by_username("alice") == user
    credentials = database.users.get_credentials("alice")
    assert credentials is not None
    assert credentials.must_change_password is True
    assert verify_password(credentials.password_hash, "secret-pass")
    assert not verify_password(credentials.password_hash, "wrong")
    assert database.users.get_credentials("nobody") is None


def test_user_duplicate_update_and_password(database: Database) -> None:
    user = User("u1", "alice", "爱丽丝", Role.ANALYST)
    database.users.add(user, password_hash="h", must_change_password=False)
    with pytest.raises(DuplicateError):
        database.users.add(
            User("u2", "alice", "另一个", Role.ADMIN), password_hash="h", must_change_password=False
        )
    changed = User("u1", "alice", "新名字", Role.ADMIN, active=False)
    database.users.update(changed)
    assert database.users.get("u1") == changed
    database.users.set_password_hash("u1", "h2", must_change_password=True)
    credentials = database.users.get_credentials("alice")
    assert credentials is not None
    assert (credentials.password_hash, credentials.must_change_password) == ("h2", True)
    with pytest.raises(NotFoundError):
        database.users.update(User("zz", "zz", "zz", Role.ADMIN))
    assert [u.username for u in database.users.list_all()] == ["alice"]


def test_all_builtin_templates_roundtrip(database: Database) -> None:
    for template in BUILTIN_TEMPLATES.values():
        database.templates.save(template, builtin=True)
    for template in BUILTIN_TEMPLATES.values():
        assert database.templates.get(template.template_id) == template
        assert database.templates.is_builtin(template.template_id)
    assert len(database.templates.list_all()) == 5
    limit = database.templates.list_all(ExperimentType.LIMIT)
    assert [t.template_id for t in limit] == ["builtin.limit"]
    assert database.templates.get("missing") is None
    assert not database.templates.is_builtin("missing")


def test_template_overwrite_replaces_items(database: Database) -> None:
    original = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    database.templates.save(original)
    smaller = BUILTIN_TEMPLATES[ExperimentType.LIMIT]
    renamed = type(smaller)(
        template_id=original.template_id,
        name="改名",
        experiment_type=smaller.experiment_type,
        items=smaller.items,
    )
    database.templates.save(renamed)
    assert database.templates.get(original.template_id) == renamed


def test_experiment_roundtrip_with_results_and_review(database: Database) -> None:
    experiment = make_experiment()
    experiment.results[2] = experiment.results[2].with_review(
        interpretation=Interpretation.NEGATIVE,
        reviewed_by="bob",
        reviewed_at=T0 + timedelta(hours=1),
        note="人工确认",
    )
    experiment.results[3] = make_result(3, state=None, status=TubeAnalysisStatus.FAILED)
    experiment.measured_sensitivity = 0.125
    experiment.mvd = 100.0
    database.experiments.save(experiment)

    loaded = database.experiments.get("E1")
    assert loaded == experiment
    assert loaded is not None
    assert loaded.results[1].features["centroid_before"] == (10.5, 20.25)
    assert loaded.results[1].elapsed_ms == 812.5
    assert loaded.results[2].review_status is ReviewStatus.REVIEWED
    assert loaded.results[3].review_status is ReviewStatus.PENDING
    assert loaded.results[3].gel_state is None
    assert database.experiments.get("nope") is None


def test_experiment_resave_overwrites_children(database: Database) -> None:
    experiment = make_experiment(tube_count=4)
    database.experiments.save(experiment)
    smaller = make_experiment(tube_count=2, batch_no="LAL-002")
    smaller.state = ExperimentState.READY
    database.experiments.save(smaller)
    assert database.experiments.get("E1") == smaller


def test_save_results_incrementally(database: Database) -> None:
    experiment = make_experiment(with_results=False)
    database.experiments.save(experiment)
    first = make_result(1)
    database.experiments.save_results("E1", [first, make_result(2)])
    replacement = make_result(1, state=GelState.NOT_GELLED)
    database.experiments.save_results("E1", [replacement])
    loaded = database.experiments.get("E1")
    assert loaded is not None
    assert loaded.results[1] == replacement
    assert set(loaded.results) == {1, 2}
    with pytest.raises(NotFoundError):
        database.experiments.save_results("missing", [first])


def test_result_with_unreviewable_fields_survives(database: Database) -> None:
    result = TubeResult.from_analysis(
        tube_no=5,
        status=TubeAnalysisStatus.GEL_MISSING,
        state=GelState.NOT_GELLED,
        mapping=GelStateMapping(),
        message="翻转前凝胶缺失",
    )
    experiment = make_experiment(with_results=False)
    experiment.results = {5: result}
    database.experiments.save(experiment)
    loaded = database.experiments.get("E1")
    assert loaded is not None
    assert loaded.results[5] == result


def test_timezone_normalised_to_utc(database: Database) -> None:
    local = datetime(2026, 3, 1, 16, 0, tzinfo=timezone(timedelta(hours=8)))
    stored = database.audit.append(make_event(at=local))
    loaded = database.audit.get(stored.seq)
    assert loaded is not None
    assert loaded.event.occurred_at == local
    assert loaded.event.occurred_at.utcoffset() == timedelta(0)
    with pytest.raises(ValueError, match="时区"):
        database.experiments.save(make_experiment(created_at=datetime(2026, 1, 1)))


def test_media_and_report_roundtrip(database: Database) -> None:
    database.experiments.save(make_experiment())
    video = MediaFile("f1", "E1", MediaKind.VIDEO, "data/E1/row01.mp4", SHA, T0, 1, 1024)
    image = MediaFile("f2", "E1", MediaKind.IMAGE, "data/E1/row02_before.png", "b" * 64, T0, 2)
    plain = MediaFile("f3", "E1", MediaKind.VIDEO, "data/E1/full.mp4", "c" * 64, T0)
    for media in (video, image, plain):
        database.media.add(media)
    assert database.media.get("f1") == video
    assert database.media.get("zz") is None
    assert database.media.list_for_experiment("E1") == [plain, video, image]
    assert database.media.list_for_experiment("E1", kind=MediaKind.IMAGE) == [image]
    assert database.media.list_for_experiment("E1", row_index=1) == [video]

    with pytest.raises(DuplicateError):
        database.media.add(video)
    with pytest.raises(NotFoundError):
        database.media.add(MediaFile("f9", "missing", MediaKind.IMAGE, "p", SHA, T0))
    with pytest.raises(ValueError, match="sha256"):
        database.media.add(MediaFile("f8", "E1", MediaKind.IMAGE, "p", "XYZ", T0))
    with pytest.raises(ValueError, match="排号"):
        database.media.add(MediaFile("f7", "E1", MediaKind.IMAGE, "p", SHA, T0, 11))

    report = ReportRecord("r1", "E1", "reports/E1.pdf", SHA, "bob", T0, signed=True)
    database.reports.add(report)
    assert database.reports.get("r1") == report
    assert database.reports.list_for_experiment("E1") == [report]
    with pytest.raises(DuplicateError):
        database.reports.add(report)
    with pytest.raises(NotFoundError):
        database.reports.add(ReportRecord("r2", "missing", "p", SHA, "bob", T0))


def test_temperature_and_device_log_roundtrip(database: Database) -> None:
    database.experiments.save(make_experiment())
    samples = [
        TemperatureSample(T0 + timedelta(seconds=i), "incubator", 37.0 + i / 10, "E1")
        for i in range(5)
    ] + [TemperatureSample(T0, "ambient", 22.5)]
    assert database.telemetry.add_temperatures(samples) == 6
    page = database.telemetry.query_temperatures(
        experiment_id="E1", page=PageRequest(offset=1, limit=2)
    )
    assert page.total == 5
    assert list(page.items) == samples[1:3]
    ranged = database.telemetry.query_temperatures(
        start=T0 + timedelta(seconds=2), end=T0 + timedelta(seconds=4), page=PageRequest()
    )
    assert [s.recorded_at for s in ranged.items] == [T0 + timedelta(seconds=i) for i in (2, 3)]

    logs = [
        DeviceLogEntry(T0, "INFO", "flipper", "翻转完成", "E1"),
        DeviceLogEntry(T0 + timedelta(seconds=1), "ERROR", "camera", "断流"),
        DeviceLogEntry(T0 + timedelta(seconds=2), "WARNING", "heater", "温度 100% 偏高"),
    ]
    assert database.telemetry.add_device_logs(logs) == 3
    everything = database.telemetry.query_device_logs(page=PageRequest())
    assert list(everything.items) == logs[::-1]
    assert database.telemetry.query_device_logs(level="ERROR", page=PageRequest()).total == 1
    assert database.telemetry.query_device_logs(keyword="100%", page=PageRequest()).total == 1
    assert database.telemetry.query_device_logs(keyword="%", page=PageRequest()).total == 1
    assert database.telemetry.query_device_logs(experiment_id="E1", page=PageRequest()).total == 1
    with pytest.raises(NotFoundError):
        database.telemetry.add_device_logs([DeviceLogEntry(T0, "INFO", "x", "y", "missing")])


def test_audit_event_roundtrip(database: Database) -> None:
    event = AuditEvent(
        T0, "alice", None, AuditAction.EXPERIMENT_CREATED, '新建 E1 "含引号" 与中文', "E1"
    )
    record = database.audit.append(event)
    assert database.audit.get(record.seq) == record
    assert record.event == event
    assert database.audit.get(999) is None


def test_no_simulation_flag_anywhere(database: Database) -> None:
    from sqlalchemy import inspect

    inspector = inspect(database.engine)
    for table in inspector.get_table_names():
        names = {column["name"] for column in inspector.get_columns(table)}
        assert not {name for name in names if "simulat" in name.lower()}
