"""真实算法 JSON 协议适配层（``algorithm_json.py``）单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.infrastructure.analysis.algorithm_json import (
    AlgorithmImageResult,
    AlgorithmInstance,
    AlgorithmSummary,
    LetterboxTransform,
    convert_instance_to_tube_analysis,
    extract_thresholds,
    load_algorithm_run,
    match_tube_mask,
)
from gelinspect.interfaces.analysis import (
    AnalyzerInfo,
    CapturedFrame,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeSlot,
    check_row_result,
)


def _write_mask(path: Path, shape: tuple[int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mask = np.zeros(shape, dtype=np.uint8)
    x0, y0, x1, y1 = box
    mask[y0:y1, x0:x1] = 255
    assert cv2.imwrite(str(path), mask)


def test_match_tube_mask_handles_independent_yolo_indices() -> None:
    shape = (100, 200)
    gel = np.zeros(shape, dtype=np.uint8)
    gel[30:45, 20:40] = 255  # 位于左侧 x=[20, 40]

    # tube_inst0 故意放在右侧，tube_inst1 在左侧
    tube_right = np.zeros(shape, dtype=np.uint8)
    tube_right[20:90, 150:180] = 255
    tube_left = np.zeros(shape, dtype=np.uint8)
    tube_left[20:90, 15:45] = 255

    matched = match_tube_mask(gel, [tube_right, tube_left])
    assert matched is tube_left


def test_convert_v2_dual_frame_and_missing_t2_satisfies_contract(tmp_path: Path) -> None:
    run_dir = tmp_path / "20261125_094748"
    masks = run_dir / "masks"
    shape = (120, 240)

    # 两支试管：gel_inst 与 tube_inst 编号故意交叉
    _write_mask(masks / "Video_1_t0_gel_inst1.png", shape, (20, 20, 40, 35))
    _write_mask(masks / "Video_1_t0_tube_inst0.png", shape, (100, 10, 130, 90))
    _write_mask(masks / "Video_1_t0_tube_inst1.png", shape, (15, 10, 45, 90))
    _write_mask(masks / "Video_1_t2_gel_inst0.png", shape, (20, 25, 40, 40))
    _write_mask(masks / "Video_1_t2_tube_inst0.png", shape, (15, 10, 45, 90))

    # 第 2 支试管：t2 缺失（t2_instance=None, score=None, REVIEW）
    _write_mask(masks / "Video_1_t0_gel_inst0.png", shape, (105, 20, 125, 35))

    payload = {
        "summary": {
            "total_images": 1,
            "total_instances": 2,
            "positive": 1,
            "negative": 0,
            "review": 1,
            "score_threshold": 0.6,
        },
        "images": [
            {
                "stem": "Video_1",
                "image_path": "/srv/Video_1/t2.jpg",
                "t0_image_path": "/srv/Video_1/t0.jpg",
                "vis_path": "runs/inspection/20261125_094748/vis/Video_1_judge.jpg",
                "instances": [
                    {
                        "position": 1,
                        "orig_instance": 1,
                        "t2_instance": 0,
                        "gel_area": 300,
                        "fill_ratio_t0": 0.18,
                        "height_norm_t2": 0.44,
                        "curvature_t2": 0.08,
                        "score": 0.78,
                        "prediction": "positive",
                        "note": "阳性",
                        "mask_path": "runs/inspection/x/masks/Video_1_t0_gel_inst1.png",
                        "x_center": 30.0,
                    },
                    {
                        "position": 2,
                        "orig_instance": 0,
                        "t2_instance": None,
                        "gel_area": 300,
                        "score": None,
                        "prediction": "REVIEW",
                        "note": "某一帧未检测到或无法匹配",
                        "mask_path": "runs/inspection/x/masks/Video_1_t0_gel_inst0.png",
                        "x_center": 115.0,
                    },
                ],
            }
        ],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_algorithm_run(run_dir)
    thresholds = extract_thresholds(loaded.summary)
    assert thresholds is not None and thresholds.not_gelled_max < thresholds.fully_gelled_min

    tr = LetterboxTransform.fit(shape, (240, 320))
    img_res = loaded.images[0]
    t1 = convert_instance_to_tube_analysis(
        1,
        img_res.instances[0],
        run_dir=run_dir,
        image_result=img_res,
        target_shape=(240, 320),
        transform_before=tr,
        transform_after=tr,
    )
    t2 = convert_instance_to_tube_analysis(
        2,
        img_res.instances[1],
        run_dir=run_dir,
        image_result=img_res,
        target_shape=(240, 320),
        transform_before=tr,
        transform_after=tr,
    )

    assert t1.status is TubeAnalysisStatus.OK
    assert t1.state is GelState.FULLY_GELLED
    assert t1.features.extra["fill_ratio_t0"] == 0.18
    assert t2.status is TubeAnalysisStatus.GEL_MISSING
    assert t2.state is None
    assert t2.features.gel_missing_after is True
    assert t2.features.area_after is None
    assert t2.features.delta_area_pct is None

    from datetime import UTC, datetime

    now = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    req = RowAnalysisRequest(
        experiment_id="exp-1",
        row_index=1,
        tubes=[TubeSlot.from_tube_no(1), TubeSlot.from_tube_no(2)],
        frames_before=[CapturedFrame(image=frame, captured_at=now) for _ in range(3)],
        frames_after=[CapturedFrame(image=frame, captured_at=now) for _ in range(3)],
    )
    res = RowAnalysisResult(
        analyzer_info=AnalyzerInfo(model_version="v2", config_version="c", thresholds=thresholds),
        elapsed_ms=5.0,
        tubes=[t1, t2],
    )
    assert check_row_result(req, res) == []


def test_convert_v1_single_frame_format(tmp_path: Path) -> None:
    run_dir = tmp_path / "20261105_205026"
    _write_mask(run_dir / "masks" / "test0001_gel_inst0.png", (100, 160), (20, 20, 40, 40))
    _write_mask(run_dir / "masks" / "test0001_tube_inst0.png", (100, 160), (15, 10, 45, 90))
    img_res = AlgorithmImageResult(
        stem="test0001",
        instances=[
            AlgorithmInstance(
                position=1,
                orig_instance=0,
                gel_area=400,
                prediction="negative",
                note="低于阈值",
            )
        ],
    )
    summary = AlgorithmSummary(total_images=1, total_instances=1, negative=1, threshold=1500)
    assert extract_thresholds(summary) is None
    out = convert_instance_to_tube_analysis(
        1,
        img_res.instances[0],
        run_dir=run_dir,
        image_result=img_res,
        target_shape=(100, 160),
    )
    assert out.status is TubeAnalysisStatus.OK
    assert out.state is GelState.NOT_GELLED
    assert out.masks is not None
