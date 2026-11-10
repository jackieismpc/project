"""仪器设置：校验、保存、持久化、审计（旧值和新值）、权限、试验进行中的限制、视频输入切换。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from gelinspect.app_context import AppContext, build_app_context
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import MediaKind
from gelinspect.services.auth import PermissionDeniedError, Session
from gelinspect.services.config import DEFAULT_CONFIG, ConfigError, parse_config
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.settings_service import SettingsError
from gelinspect.services.user_settings import (
    NUMBER_LIMITS,
    SettingsDraft,
    SettingsProblemCode,
    VideoInput,
    parse_draft,
    settings_file,
)

from .conftest import NOW, PASSWORDS
from .engine_helpers import eventually
from .test_new_experiment_engine import request_for_tubes

FAST: dict[str, object] = {"incubation_minutes": 0.05, "time_scale": 60, "row_late_warn_s": 1e6}


def make_context(tmp_path: Path, **sections: object) -> AppContext:
    config: dict[str, object] = dict(DEFAULT_CONFIG)
    config["data_dir"] = str(tmp_path / "data")
    config["engine"] = FAST
    config["video"] = {
        "source": "simulation.sim_video:SimVideoSource",
        "device": "sim",
        "width": 640,
        "height": 360,
        "fps": 30,
    }
    config.update(sections)
    return build_app_context(parse_config(config), clock=lambda: NOW)


@pytest.fixture
def context(tmp_path: Path) -> Iterator[AppContext]:
    built = make_context(tmp_path)
    yield built
    built.close()


def admin(context: AppContext) -> Session:
    return context.auth.login("admin", PASSWORDS["admin"])


def draft_of(context: AppContext, **changes: object) -> SettingsDraft:
    current = context.settings.current(admin(context)).settings
    return replace(SettingsDraft.from_settings(current), **changes)  # type: ignore[arg-type]


def audit_lines(context: AppContext) -> list[str]:
    from gelinspect.interfaces.storage import AuditQuery, PageRequest

    page = context.database.audit.query(
        AuditQuery(action=AuditAction.SETTINGS_CHANGED), PageRequest(limit=100)
    )
    return [r.event.detail for r in sorted(page.items, key=lambda r: r.seq)]


def write_images(folder: Path, count: int = 5, bgr: tuple[int, int, int] = (40, 200, 40)) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        image = np.zeros((360, 640, 3), np.uint8)
        image[:] = bgr
        image[:, :: 8 + i] = (bgr[0] // 2, bgr[1] // 2, bgr[2] // 2)  # 每张略有不同，避免被判卡帧
        assert cv2.imwrite(str(folder / f"frame_{i:03d}.png"), image)


# ---------------------------------------------------------------------- 校验


def test_current_settings_show_the_effective_values_and_the_defaults(context: AppContext) -> None:
    view = context.settings.current(context.auth.login("experimenter", PASSWORDS["experimenter"]))
    assert view.settings == view.defaults  # 没改过
    assert view.settings.instrument_no == "GI-001" and view.settings.retention_days is None
    assert view.settings.video_input is VideoInput.CONFIG
    assert view.backend.analysis_backend.endswith("MockAnalyzer")
    assert view.backend.model_version is None  # 还没有跑过试验
    assert view.running_experiment is None


def test_invalid_values_are_all_reported_with_a_reason_and_nothing_is_written(
    context: AppContext,
) -> None:
    session = admin(context)
    bad = draft_of(
        context,
        instrument_no="   ",
        incubation_minutes="abc",
        frames_per_side="2",
        settle_s="-1",
        temperature_target_c="99",
        temperature_tolerance_c="nan",
        temperature_hold_s="1e9",
        retention_days="0.5",
        copyright_holder="x" * 500,
    )
    with pytest.raises(SettingsError) as caught:
        context.settings.save(session, bad)
    reasons = {p.name: p.code for p in caught.value.problems}
    assert reasons == {
        "instrument_no": SettingsProblemCode.REQUIRED,
        "incubation_minutes": SettingsProblemCode.NOT_A_NUMBER,
        "frames_per_side": SettingsProblemCode.OUT_OF_RANGE,
        "settle_s": SettingsProblemCode.OUT_OF_RANGE,
        "temperature_target_c": SettingsProblemCode.OUT_OF_RANGE,
        "temperature_tolerance_c": SettingsProblemCode.NOT_A_NUMBER,
        "temperature_hold_s": SettingsProblemCode.OUT_OF_RANGE,
        "retention_days": SettingsProblemCode.NOT_AN_INTEGER,
        "copyright_holder": SettingsProblemCode.TOO_LONG,
    }
    assert not settings_file(context.config.data_dir).exists()
    assert audit_lines(context) == []
    assert context.engine.config.incubation_minutes == 0.05  # 运行时也没动


def test_number_limits_are_inclusive_and_integers_stay_integers() -> None:
    base = SettingsDraft.from_settings(parse_draft(_good())[0])  # type: ignore[arg-type]
    low, high = NUMBER_LIMITS["frames_per_side"]
    for value, ok in ((low, True), (high, True), (low - 1, False), (high + 1, False)):
        settings, problems = parse_draft(replace(base, frames_per_side=str(int(value))))
        assert (settings is not None) is ok and bool(problems) is not ok
    assert parse_draft(replace(base, frames_per_side="3.0"))[0] is None
    assert parse_draft(replace(base, retention_days=""))[0] is not None  # 留空 = 不自动删除


def _good() -> SettingsDraft:
    return SettingsDraft(
        instrument_no="GI-009",
        incubation_minutes="30",
        frames_per_side="4",
        settle_s="1",
        temperature_target_c="37",
        temperature_tolerance_c="1",
        temperature_hold_s="60",
        video_input=VideoInput.CONFIG,
        video_path="",
        retention_days="",
        copyright_holder="某某公司",
    )


def test_video_paths_are_checked_against_the_chosen_type(
    context: AppContext, tmp_path: Path
) -> None:
    session = admin(context)
    empty = tmp_path / "empty"
    empty.mkdir()
    a_file = tmp_path / "note.txt"
    a_file.write_text("x")
    cases = [
        (VideoInput.FILE, "", SettingsProblemCode.REQUIRED),
        (VideoInput.FILE, str(tmp_path / "missing.mp4"), SettingsProblemCode.PATH_MISSING),
        (VideoInput.FILE, str(empty), SettingsProblemCode.NOT_A_FILE),
        (VideoInput.IMAGES, str(a_file), SettingsProblemCode.NOT_A_FOLDER),
        (VideoInput.IMAGES, str(empty), SettingsProblemCode.NO_IMAGES),
    ]
    for kind, path, code in cases:
        problems = context.settings.validate(
            session, draft_of(context, video_input=kind, video_path=path)
        )
        assert [p.code for p in problems] == [code], (kind, path)


# ---------------------------------------------------------------------- 保存


def test_save_applies_persists_and_audits_old_and_new_values(
    context: AppContext, tmp_path: Path
) -> None:
    session = admin(context)
    result = context.settings.save(
        session,
        draft_of(
            context,
            instrument_no="GI-009",
            incubation_minutes="30",
            frames_per_side="4",
            temperature_target_c="36.5",
            temperature_hold_s="90",
            retention_days="365",
            copyright_holder="厦门某某生物科技",
        ),
    )
    assert set(result.changed) == {
        "instrument_no",
        "incubation_minutes",
        "frames_per_side",
        "temperature_target_c",
        "temperature_hold_s",
        "retention_days",
        "copyright_holder",
    }
    # 运行时立即生效（对之后启动的试验）
    assert context.engine.config.incubation_minutes == 30
    assert context.engine.config.frames_per_side == 4
    assert context.engine.config.temperature_target_c == 36.5
    assert context.engine.config.temperature_hold_s == 90
    assert context.experiments.instrument_no == "GI-009"
    assert context.reports.copyright_holder == "厦门某某生物科技"
    # 保存在数据目录的用户配置文件里，只含改动的项
    saved = json.loads(settings_file(context.config.data_dir).read_text(encoding="utf-8"))
    assert saved["settings"] == {
        "copyright_holder": "厦门某某生物科技",
        "frames_per_side": 4,
        "incubation_minutes": 30.0,
        "instrument_no": "GI-009",
        "retention_days": 365,
        "temperature_hold_s": 90.0,
        "temperature_target_c": 36.5,
    }
    # 审计逐项记录旧值和新值
    (line,) = audit_lines(context)
    assert "仪器编号 GI-001 → GI-009" in line
    assert "孵育时间（分钟） 0.05 → 30" in line
    assert "数据保留天数 不自动删除 → 365" in line
    assert "版权方名称 厦门鲎试剂生物科技股份有限公司 → 厦门某某生物科技" in line
    # 默认配置没有被改：基础配置对象不变，用户配置文件在数据目录里
    assert context.base_config.instrument_no == "GI-001"
    assert settings_file(context.base_config.data_dir).parent == tmp_path / "data"


def test_saving_the_same_values_writes_nothing(context: AppContext) -> None:
    session = admin(context)
    result = context.settings.save(session, draft_of(context))
    assert result.changed == () and audit_lines(context) == []
    assert not settings_file(context.config.data_dir).exists()


def test_a_setting_changed_back_to_the_default_leaves_the_file(context: AppContext) -> None:
    session = admin(context)
    context.settings.save(session, draft_of(context, instrument_no="GI-009"))
    assert settings_file(context.config.data_dir).exists()
    context.settings.save(session, draft_of(context, instrument_no="GI-001"))
    assert not settings_file(context.config.data_dir).exists()
    assert len(audit_lines(context)) == 2


def test_settings_survive_a_restart_and_the_repository_defaults_stay_untouched(
    tmp_path: Path,
) -> None:
    first = make_context(tmp_path)
    try:
        first.settings.save(
            admin(first),
            draft_of(first, instrument_no="GI-777", incubation_minutes="12", frames_per_side="5"),
        )
    finally:
        first.close()
    second = make_context(tmp_path)
    try:
        assert second.config.instrument_no == "GI-777"
        assert second.config.engine.incubation_minutes == 12
        assert second.engine.config.frames_per_side == 5
        assert second.config.pipeline_config().snapshot.frames_per_side == 5
        assert second.base_config.instrument_no == "GI-001"  # 默认配置不变
        view = second.settings.current(admin(second))
        assert view.settings.instrument_no == "GI-777" and view.defaults.instrument_no == "GI-001"
    finally:
        second.close()


def test_a_corrupt_settings_file_stops_startup_with_a_clear_message(tmp_path: Path) -> None:
    path = settings_file(tmp_path / "data")
    path.parent.mkdir(parents=True)
    for text, expect in (
        ("{ not json", "不是合法的 JSON"),
        (json.dumps({"version": 1, "settings": {"nope": 1}}), "未知的设置项"),
        (json.dumps({"version": 1, "settings": {"frames_per_side": 1}}), "内容有误"),
        (json.dumps({"version": 9, "settings": {}}), "版本不受支持"),
    ):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match=expect):
            make_context(tmp_path).close()
    assert not (tmp_path / "data" / "gelinspect.db").exists()  # 报错时没有创建数据库


# ---------------------------------------------------------------------- 权限和限制


def test_only_admins_can_save_but_everyone_can_look(context: AppContext) -> None:
    for name in ("experimenter", "analyst"):
        session = context.auth.login(name, PASSWORDS[name])
        assert context.settings.current(session).settings.instrument_no == "GI-001"
        with pytest.raises(PermissionDeniedError):
            context.settings.save(session, draft_of(context, instrument_no="HACK"))
        with pytest.raises(PermissionDeniedError):
            context.settings.preview_video(session, VideoInput.FILE, "/x")
    assert audit_lines(context) == [] and not settings_file(context.config.data_dir).exists()
    with pytest.raises(PermissionDeniedError):
        context.settings.current(None)


def test_changes_that_affect_a_run_are_refused_while_an_experiment_runs(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ExperimentEngine, "active_experiment_id", property(lambda _s: "EXP-RUNNING")
    )
    session = admin(context)
    for changes in (
        {"incubation_minutes": "20"},
        {"frames_per_side": "6"},
        {"temperature_target_c": "38"},
        {"settle_s": "2"},
        {"video_input": VideoInput.FILE, "video_path": __file__},
    ):
        with pytest.raises(ServiceError) as caught:
            context.settings.save(session, draft_of(context, **changes))
        assert caught.value.code is ServiceErrorCode.SETTINGS_BUSY, changes
    assert context.engine.config.incubation_minutes == 0.05
    # 不影响运行的项仍可修改
    result = context.settings.save(
        session,
        draft_of(context, instrument_no="GI-002", retention_days="30", copyright_holder="新名称"),
    )
    assert set(result.changed) == {"instrument_no", "retention_days", "copyright_holder"}
    assert context.settings.current(session).running_experiment == "EXP-RUNNING"


# ---------------------------------------------------------------------- 视频输入


def test_video_preview_reports_resolution_fps_and_a_frame(
    context: AppContext, tmp_path: Path
) -> None:
    session = admin(context)
    folder = tmp_path / "frames"
    write_images(folder, 4)
    probe = context.settings.preview_video(session, VideoInput.IMAGES, str(folder))
    assert (probe.width, probe.height) == (640, 360) and probe.frame_count == 4
    assert probe.frame is not None and probe.frame.shape == (360, 640, 3)
    current = context.settings.preview_video(session)  # 当前生效的（模拟视频源）
    assert (current.width, current.height) == (640, 360) and current.fps == 30
    with pytest.raises(SettingsError):
        context.settings.preview_video(session, VideoInput.FILE, str(tmp_path / "missing.mp4"))
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(ServiceError) as caught:
        context.settings.preview_video(session, VideoInput.FILE, str(broken))
    assert caught.value.code is ServiceErrorCode.SETTINGS_PREVIEW_FAILED


def test_a_new_experiment_really_uses_the_newly_chosen_video_source(
    context: AppContext, tmp_path: Path
) -> None:
    folder = tmp_path / "my frames"
    tint = (40, 200, 40)  # 偏绿；模拟视频源的画面不是这个颜色
    write_images(folder, 5, tint)
    session = admin(context)
    context.settings.save(
        session, draft_of(context, video_input=VideoInput.IMAGES, video_path=str(folder))
    )
    assert context.config.video.source == "images" and context.config.video.device == str(folder)
    assert saved_video(context) == {"video_input": "images", "video_path": str(folder)}

    experimenter = context.auth.login("experimenter", PASSWORDS["experimenter"])
    experiment_id = context.experiments.create_experiment(
        experimenter, request_for_tubes(list(range(1, 19)))
    )
    context.experiments.start_experiment(experimenter, experiment_id)

    def finished() -> bool:
        experiment = context.database.experiments.get(experiment_id)
        return experiment is not None and experiment.state in (
            ExperimentState.PENDING_REVIEW,
            ExperimentState.COMPLETED,
        )

    assert eventually(finished, 60)
    assert context.engine.wait(30)
    assert context.pipeline.info is None or "图片序列" in (context.pipeline.info.description or "")
    originals = context.database.media.list_for_experiment(experiment_id, kind=MediaKind.IMAGE)
    assert originals, "试验没有保存任何原图"
    image = cv2.imread(str(context.config.data_dir / originals[0].path))
    assert image is not None
    mean = image.reshape(-1, 3).mean(axis=0)
    assert np.allclose(mean, np.array(tint) * 0.9, atol=30), f"原图不是新视频源的画面：{mean}"
    # 换回配置文件的视频源：下一个试验又用回模拟视频源
    context.settings.save(session, draft_of(context, video_input=VideoInput.CONFIG, video_path=""))
    assert context.config.video.source == "simulation.sim_video:SimVideoSource"
    assert not settings_file(context.config.data_dir).exists()


def saved_video(context: AppContext) -> dict[str, object]:
    data = json.loads(settings_file(context.config.data_dir).read_text(encoding="utf-8"))[
        "settings"
    ]
    return {k: v for k, v in data.items() if k.startswith("video")}


def test_a_failed_runtime_apply_is_rolled_back_and_nothing_is_saved(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = admin(context)
    folder = tmp_path / "ok"
    write_images(folder, 3)

    def refuse(*_args: object) -> None:
        raise OSError("磁盘只读")

    monkeypatch.setattr("gelinspect.services.settings_service.write_overrides", refuse)
    with pytest.raises(ServiceError) as caught:
        context.settings.save(
            session,
            draft_of(
                context,
                incubation_minutes="20",
                video_input=VideoInput.IMAGES,
                video_path=str(folder),
            ),
        )
    assert caught.value.code is ServiceErrorCode.SETTINGS_SAVE_FAILED
    assert context.engine.config.incubation_minutes == 0.05  # 已撤销
    assert context.config.video.source == "simulation.sim_video:SimVideoSource"
    assert audit_lines(context) == []


def test_settings_switch_encryption_on_immediately_and_persist(context: AppContext) -> None:
    assert not context.exports.encryption_required
    result = context.settings.save(admin(context), draft_of(context, encrypt_exports=True))
    assert "encrypt_exports" in result.changed
    assert context.exports.encryption_required
    saved = json.loads(settings_file(context.config.data_dir).read_text(encoding="utf-8"))
    assert saved["settings"] == {"encrypt_exports": True}
    assert context.settings.current(admin(context)).settings.encrypt_exports
