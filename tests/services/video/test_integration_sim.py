"""集成：模拟设备 + 模拟视频源 → 管线（翻转、录制、抓帧）→ MockAnalyzer。

另一条路径：图片文件夹 → ``load_row_capture`` → MockAnalyzer。
"""

from __future__ import annotations

from pathlib import Path

import cv2

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.domain.plate import tubes_in_row
from gelinspect.infrastructure.db import open_database
from gelinspect.infrastructure.video.factory import create_video_source
from gelinspect.infrastructure.video.image_io import write_image
from gelinspect.infrastructure.video.image_sequence import load_row_capture
from gelinspect.interfaces.analysis import RowAnalysisRequest, TubeSlot, check_row_result
from gelinspect.interfaces.device import TrayPosition
from gelinspect.interfaces.storage import MediaKind
from gelinspect.interfaces.video import VideoConfig
from gelinspect.services.video import (
    SnapshotConfig,
    VideoPipeline,
    VideoPipelineConfig,
    segment_to_media_file,
    snapshot_to_media_files,
)
from simulation.image_synth import SynthConfig, render_pair, specs_from_states
from simulation.mock_analyzer import MockAnalyzer
from simulation.scenario import Scenario
from simulation.sim_device import SimDevice
from simulation.sim_video import SimVideoSource
from tests.infrastructure.db.conftest import make_experiment
from tests.services.video.helpers import wait_until

GELLED = Scenario(tube_states=dict.fromkeys(range(1, 9), GelState.FULLY_GELLED))


def test_sim_device_flip_record_grab_analyze_and_persist(tmp_path: Path) -> None:
    device = SimDevice(time_scale=60.0)  # 6 秒翻转 → 约 0.1 秒；20 秒流动 → 约 0.33 秒
    device.connect()
    device.move_tray(TrayPosition.IN)
    source = SimVideoSource(scene=device, scenario=GELLED)
    config = VideoPipelineConfig(
        data_dir=tmp_path / "data",
        source=VideoConfig(device="sim", width=960, height=540, fps=40.0),
        snapshot=SnapshotConfig(settle_s=0.5, wait_timeout_s=10.0),
    )
    pipeline = VideoPipeline(source, config)
    pipeline.start()
    try:
        assert wait_until(lambda: len(pipeline.buffer) >= 6, 10.0)
        pipeline.start_segment("EXP-SIM-1", 1)
        pipeline.mark_flip_start(device.clock.now(), 1)
        flip = device.flip_row(1)
        snap = pipeline.grab(1, 3, experiment_id="EXP-SIM-1", flip_completed_at=flip.completed_at)
        record = pipeline.stop_segment()
    finally:
        pipeline.stop()

    # 录制：可播放、帧数一致
    cap = cv2.VideoCapture(str(record.path))
    count = 0
    while cap.read()[0]:
        count += 1
    assert count == record.frame_count > 0 and not record.interrupted
    # 抓帧：翻转前画面在翻转开始前，翻转后画面在稳定时间之后
    assert snap.frames_before[-1].captured_at < flip.started_at
    assert snap.frames_after[0].captured_at >= flip.completed_at
    # 交给 MockAnalyzer：输出合规，凝固的管判为完全凝固
    request = RowAnalysisRequest(
        experiment_id="EXP-SIM-1",
        row_index=1,
        tubes=[TubeSlot.from_tube_no(n) for n in tubes_in_row(1)],
        frames_before=snap.frames_before,
        frames_after=snap.frames_after,
        flip_angle_deg=flip.actual_angle_deg,
    )
    analyzer = MockAnalyzer()
    analyzer.load(None)
    result = analyzer.analyze_row(request)
    assert check_row_result(request, result) == []
    assert all(t.status is TubeAnalysisStatus.OK for t in result.tubes)
    assert {t.state for t in result.tubes} == {GelState.FULLY_GELLED}

    # 入库：管线不碰数据库，由调用方转换后写入媒体仓储
    database = open_database(tmp_path / "gel.db")
    try:
        database.experiments.save(make_experiment("EXP-SIM-1"))
        database.media.add(segment_to_media_file(record))
        for media in snapshot_to_media_files(snap):
            database.media.add(media)
        videos = database.media.list_for_experiment("EXP-SIM-1", kind=MediaKind.VIDEO)
        images = database.media.list_for_experiment("EXP-SIM-1", kind=MediaKind.IMAGE, row_index=1)
        assert [v.sha256 for v in videos] == [record.sha256]
        assert len(images) == 6 and {i.sha256 for i in images} == {f.sha256 for f in snap.files}
    finally:
        database.close()


def test_source_created_from_config_string_without_importing_simulation_in_main_code() -> None:
    source = create_video_source("simulation.sim_video:SimVideoSource")
    assert source.open(VideoConfig(device="sim", width=320, height=180, fps=10.0)).is_live
    source.close()


def test_image_folder_to_load_row_capture_to_mock_analyzer(tmp_path: Path) -> None:
    states = (
        [GelState.FULLY_GELLED] * 3 + [GelState.PARTIALLY_GELLED] * 3 + [GelState.NOT_GELLED] * 2
    )
    before, after = render_pair(
        specs_from_states(states, 3), SynthConfig(width=960, height=540), seed=1
    )
    row = tmp_path / "样片" / "第1排"  # 中文目录
    for n in range(1, 4):
        write_image(row / "before" / f"翻转前_{n}.png", before.image)
        write_image(row / "after" / f"翻转后_{n}.jpg", after.image)
    capture = load_row_capture(row)
    request = RowAnalysisRequest(
        experiment_id="EXP-IMG-1",
        row_index=1,
        tubes=[TubeSlot.from_tube_no(n) for n in tubes_in_row(1)],
        frames_before=capture.frames_before,
        frames_after=capture.frames_after,
    )
    analyzer = MockAnalyzer()
    analyzer.load(None)
    result = analyzer.analyze_row(request)
    assert check_row_result(request, result) == []
    assert [t.state for t in result.tubes] == states
