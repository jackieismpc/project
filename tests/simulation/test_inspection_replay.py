"""``InspectionVideoSource`` 与 ``InspectionReplayAnalyzer`` 契约与映射测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.interfaces.analysis import (
    AnalyzerError,
    CapturedFrame,
    GelAnalyzer,
    RowAnalysisRequest,
    TubeSlot,
    check_row_result,
)
from gelinspect.interfaces.video import VideoConfig, VideoSource
from simulation.inspection_replay import InspectionReplayAnalyzer, InspectionVideoSource
from simulation.scene import StaticScene


def _create_mini_inspection(root: Path) -> Path:
    run_dir = root / "20261125_094748"
    (run_dir / "vis").mkdir(parents=True)
    (run_dir / "masks").mkdir(parents=True)
    # 故意创建一个空目录（无 result.json），验证自动跳过
    (root / "20261125_094800").mkdir(parents=True)

    # Video_A: 1 支试管；Video_B: 3 支试管
    for stem, count, (h0, w0), (h2, w2) in (
        ("Video_A", 1, (100, 160), (100, 160)),
        ("Video_B", 3, (100, 160), (110, 170)),
    ):
        left = np.full((max(h0, h2), w0, 3), 40, dtype=np.uint8)
        divider = np.full((max(h0, h2), 5, 3), 255, dtype=np.uint8)
        right = np.full((max(h0, h2), w2, 3), 80, dtype=np.uint8)
        vis = np.hstack([left, divider, right])
        assert cv2.imwrite(str(run_dir / "vis" / f"{stem}_judge.jpg"), vis)
        for i in range(count):
            x0, x1 = 20 + i * 40, 35 + i * 40
            m0 = np.zeros((h0, w0), dtype=np.uint8)
            m0[20:35, x0:x1] = 255
            assert cv2.imwrite(str(run_dir / "masks" / f"{stem}_t0_gel_inst{i}.png"), m0)
            mt0 = np.zeros((h0, w0), dtype=np.uint8)
            mt0[10:80, x0 - 3 : x1 + 3] = 255
            assert cv2.imwrite(str(run_dir / "masks" / f"{stem}_t0_tube_inst{i}.png"), mt0)
            m2 = np.zeros((h2, w2), dtype=np.uint8)
            m2[25:40, x0:x1] = 255
            assert cv2.imwrite(str(run_dir / "masks" / f"{stem}_t2_gel_inst{i}.png"), m2)
            mt2 = np.zeros((h2, w2), dtype=np.uint8)
            mt2[10:80, x0 - 3 : x1 + 3] = 255
            assert cv2.imwrite(str(run_dir / "masks" / f"{stem}_t2_tube_inst{i}.png"), mt2)

    result = {
        "summary": {
            "total_images": 2,
            "total_instances": 4,
            "positive": 2,
            "negative": 1,
            "review": 1,
            "score_threshold": 0.6,
        },
        "images": [
            {
                "stem": "Video_A",
                "t0_image_path": "/srv/Video_A/t0.jpg",
                "image_path": "/srv/Video_A/t2.jpg",
                "vis_path": "runs/inspection/20261125_094748/vis/Video_A_judge.jpg",
                "instances": [
                    {
                        "position": 1,
                        "orig_instance": 0,
                        "t2_instance": 0,
                        "score": 0.85,
                        "prediction": "positive",
                    }
                ],
            },
            {
                "stem": "Video_B",
                "t0_image_path": "/srv/Video_B/t0.jpg",
                "image_path": "/srv/Video_B/t2.jpg",
                "vis_path": "runs/inspection/20261125_094748/vis/Video_B_judge.jpg",
                "instances": [
                    {
                        "position": 1,
                        "orig_instance": 0,
                        "t2_instance": 0,
                        "score": 0.81,
                        "prediction": "positive",
                    },
                    {
                        "position": 2,
                        "orig_instance": 1,
                        "t2_instance": 1,
                        "score": 0.42,
                        "prediction": "negative",
                    },
                    {
                        "position": 3,
                        "orig_instance": 2,
                        "t2_instance": 2,
                        "score": 0.60,
                        "prediction": "REVIEW",
                        "note": "落在复核区间",
                    },
                ],
            },
        ],
    }
    (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return root


def _request_for(tube_nos: list[int], shape: tuple[int, int] = (200, 320)) -> RowAnalysisRequest:
    now = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    frame = np.zeros((*shape, 3), dtype=np.uint8)
    return RowAnalysisRequest(
        experiment_id="exp-test",
        row_index=1,
        tubes=[TubeSlot.from_tube_no(n) for n in tube_nos],
        frames_before=[CapturedFrame(image=frame, captured_at=now) for _ in range(3)],
        frames_after=[CapturedFrame(image=frame, captured_at=now) for _ in range(3)],
    )


def test_inspection_video_source_splits_before_and_after(tmp_path: Path) -> None:
    root = _create_mini_inspection(tmp_path / "inspection")
    scene = StaticScene()
    source = InspectionVideoSource(inspection_dir=root, scene=scene)
    assert isinstance(source, VideoSource)

    info = source.open(VideoConfig(device="inspection", width=320, height=200, fps=60))
    assert (info.width, info.height) == (320, 200)
    assert "20261125_094748" in info.description

    scene.set(row_index=1, flipped=False)
    before = source.read()
    scene.set(row_index=1, flipped=True, settled=False)
    t0_frame = source.read()
    scene.set(row_index=1, flipped=True, settled=True)
    after = source.read()
    assert before is not None and t0_frame is not None and after is not None
    assert before.image.shape == (200, 320, 3)
    assert t0_frame.image.shape == (200, 320, 3)
    assert after.image.shape == (200, 320, 3)
    assert before.image[100, 160, 0] == 40
    assert t0_frame.image[100, 160, 0] == 40
    assert after.image[100, 160, 0] == 80
    source.close()


def test_inspection_replay_analyzer_best_fit_and_missing_policies(tmp_path: Path) -> None:
    root = _create_mini_inspection(tmp_path / "inspection")
    analyzer = InspectionReplayAnalyzer(inspection_dir=root)
    assert isinstance(analyzer, GelAnalyzer)

    with pytest.raises(AnalyzerError):
        analyzer.analyze_row(_request_for([1]))

    info = analyzer.load(None)
    assert info.model_version == "inspection-20261125_094748"

    # 1 管优先选 Video_A（positive）
    req1 = _request_for([1])
    res1 = analyzer.analyze_row(req1)
    assert check_row_result(req1, res1) == []
    assert res1.tubes[0].state is GelState.FULLY_GELLED

    # 3 管优先选 Video_B（positive / negative / partially_gelled）
    req3 = _request_for([3, 1, 2])
    res3 = analyzer.analyze_row(req3)
    assert check_row_result(req3, res3) == []
    by_no = {t.tube_no: t for t in res3.tubes}
    assert by_no[1].state is GelState.FULLY_GELLED
    assert by_no[2].state is GelState.NOT_GELLED
    assert by_no[3].state is GelState.PARTIALLY_GELLED

    # 5 管超出 Video_B 的 3 支 → tube_not_found
    req5 = _request_for([1, 2, 3, 4, 5])
    res5 = analyzer.analyze_row(req5)
    assert check_row_result(req5, res5) == []
    assert {t.tube_no: t.status for t in res5.tubes}[4] is TubeAnalysisStatus.TUBE_NOT_FOUND

    analyzer.close()

    # cycle 模式循环复用
    cycled = InspectionReplayAnalyzer(inspection_dir=root, missing_policy="cycle")
    cycled.load(None)
    res5_c = cycled.analyze_row(req5)
    assert check_row_result(req5, res5_c) == []
    assert all(t.status is TubeAnalysisStatus.OK for t in res5_c.tubes)
    cycled.close()
