"""YoloOnnxAnalyzer、gel_inspection_core 与全链路集成测试。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.infrastructure.analysis.gel_inspection_core import (
    FeatureScoringConfig,
    FrameSegmentation,
    OnnxGelSegmenter,
    SegmentConfig,
    SegmentedInstance,
    classify_score,
    evaluate_gel_pair,
    score_features,
)
from gelinspect.infrastructure.analysis.yolo_onnx import YoloOnnxAnalyzer
from gelinspect.interfaces.analysis import (
    AnalyzerError,
    CapturedFrame,
    GelAnalyzer,
    RowAnalysisRequest,
    TubeSlot,
    check_row_result,
)

ROOT = Path(__file__).resolve().parents[3]
REAL_MODEL = ROOT / "ckpt" / "gel-seg-v1.onnx"
REAL_SAMPLE_VIS = ROOT / "inspection" / "20261125_094748" / "vis" / "Video_1_judge.jpg"
REAL_CONFIG = ROOT / "packaging" / "config" / "analyzer.yolo_onnx.yaml"


def _make_rect_mask(
    shape: tuple[int, int], x0: int, y0: int, x1: int, y1: int
) -> NDArray[np.uint8]:
    mask = np.zeros(shape, dtype=np.uint8)
    mask[y0:y1, x0:x1] = 255
    return mask


def _instance(
    shape: tuple[int, int], x0: int, y0: int, x1: int, y1: int, *, conf: float = 0.92
) -> SegmentedInstance:
    mask = _make_rect_mask(shape, x0, y0, x1, y1)
    xs = np.where(mask > 0)[1]
    return SegmentedInstance(
        mask=mask,
        conf=conf,
        x_center=float(xs.mean()),
        area=int((mask > 0).sum()),
        box_xyxy=(float(x0), float(y0), float(x1), float(y1)),
    )


class _StubSegmenter(OnnxGelSegmenter):
    """按帧对象身份返回预设分割结果，用于确定性覆盖全部分支。"""

    def __init__(self, mapping: dict[int, FrameSegmentation]) -> None:
        super().__init__()
        self._mapping = mapping
        self._loaded = False
        self.load_calls = 0
        self.close_calls = 0

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self, model_path: Path, cfg: SegmentConfig) -> Any:
        self.load_calls += 1
        self._loaded = True
        return None

    def segment(self, image_bgr: NDArray[np.uint8], cfg: SegmentConfig) -> FrameSegmentation:
        assert self._loaded
        return self._mapping.get(id(image_bgr), FrameSegmentation(gels=(), tubes=()))

    def close(self) -> None:
        self.close_calls += 1
        self._loaded = False


def _request_from_images(
    before_img: NDArray[np.uint8],
    t0_img: NDArray[np.uint8],
    t2_img: NDArray[np.uint8],
    *,
    tube_nos: list[int],
    row_index: int = 1,
) -> RowAnalysisRequest:
    base = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
    frames_before = [
        CapturedFrame(image=before_img, captured_at=base + timedelta(milliseconds=100 * i))
        for i in range(3)
    ]
    frames_after = [
        CapturedFrame(image=t0_img, captured_at=base + timedelta(seconds=1)),
        CapturedFrame(image=t0_img, captured_at=base + timedelta(seconds=2)),
        CapturedFrame(image=t2_img, captured_at=base + timedelta(seconds=3)),
    ]
    return RowAnalysisRequest(
        experiment_id="exp-onnx-test",
        row_index=row_index,
        tubes=[TubeSlot.from_tube_no(n) for n in tube_nos],
        frames_before=frames_before,
        frames_after=frames_after,
        flip_angle_deg=180.0,
    )


def test_feature_scoring_and_thresholds_match_gel_inspection() -> None:
    cfg = FeatureScoringConfig()
    fill_s, height_s, flat_s, total = score_features(0.12, 0.50, 0.0, cfg)
    assert (fill_s, height_s, flat_s) == (1.0, 1.0, 1.0)
    assert total == pytest.approx(1.0)
    assert classify_score(0.606, cfg) == "positive"
    assert classify_score(0.600, cfg) == "REVIEW"
    assert classify_score(0.594, cfg) == "negative"

    shape = (200, 200)
    tube = _make_rect_mask(shape, 50, 20, 150, 180)
    gel = _make_rect_mask(shape, 55, 30, 145, 80)  # height=50, width=100 -> norm=0.50
    breakdown = evaluate_gel_pair(gel, tube, gel, tube, cfg)
    assert breakdown is not None
    assert breakdown.height_norm_t2 == pytest.approx(0.50, abs=0.02)
    assert breakdown.prediction == "positive"


def test_analyzer_lifecycle_and_all_status_branches(tmp_path: Path) -> None:
    model_file = tmp_path / "dummy.onnx"
    model_file.write_bytes(b"onnx-stub")
    shape = (240, 640)
    before_img = np.full((*shape, 3), 20, dtype=np.uint8)
    t0_img = np.full((*shape, 3), 30, dtype=np.uint8)
    t2_img = np.full((*shape, 3), 40, dtype=np.uint8)
    t0_img[0, 0] = 200
    t2_img[0, 0] = 210

    # 管1: 正常阳性（饱满+高度0.5+平底）
    # 管2: t2 缺凝胶 -> GEL_MISSING + state=None
    # 管3: t0/t2 缺试管掩码但有凝胶 -> LOW_QUALITY
    # 管4: 完全未检出 -> TUBE_NOT_FOUND
    seg_t0 = FrameSegmentation(
        gels=(
            _instance(shape, 20, 20, 100, 70),
            _instance(shape, 160, 20, 240, 70),
            _instance(shape, 300, 20, 380, 70),
        ),
        tubes=(
            _instance(shape, 15, 10, 105, 190),
            _instance(shape, 155, 10, 245, 190),
        ),
    )
    seg_t2 = FrameSegmentation(
        gels=(
            _instance(shape, 20, 20, 100, 65),
            _instance(shape, 300, 20, 380, 65),
        ),
        tubes=(
            _instance(shape, 15, 10, 105, 190),
            _instance(shape, 155, 10, 245, 190),
        ),
    )
    stub = _StubSegmenter({id(t0_img): seg_t0, id(t2_img): seg_t2})
    analyzer = YoloOnnxAnalyzer(model_path=model_file, segmenter=stub)
    assert isinstance(analyzer, GelAnalyzer)

    req = _request_from_images(before_img, t0_img, t2_img, tube_nos=[1, 2, 3, 4])
    with pytest.raises(AnalyzerError):
        analyzer.analyze_row(req)

    info = analyzer.load(None)
    assert "dummy" in info.model_version
    assert info.thresholds is not None

    result = analyzer.analyze_row(req)
    assert check_row_result(req, result) == []
    by_no = {t.tube_no: t for t in result.tubes}

    assert by_no[1].status is TubeAnalysisStatus.OK
    assert by_no[1].state is GelState.FULLY_GELLED
    assert by_no[1].features.extra["frame_pair_mode"] == "post_flip_span"
    assert by_no[1].masks is not None

    assert by_no[2].status is TubeAnalysisStatus.GEL_MISSING
    assert by_no[2].state is None
    assert by_no[2].features.gel_missing_after is True
    assert by_no[2].features.area_after is None
    assert by_no[2].features.delta_area_pct is None

    assert by_no[3].status is TubeAnalysisStatus.LOW_QUALITY
    assert by_no[3].state is None
    assert by_no[3].message

    assert by_no[4].status is TubeAnalysisStatus.TUBE_NOT_FOUND
    assert by_no[4].masks is None

    # 常量黑帧不抛异常，统一返回 LOW_QUALITY
    black = np.zeros((*shape, 3), dtype=np.uint8)
    black_req = _request_from_images(black, black, black, tube_nos=[1, 2])
    black_res = analyzer.analyze_row(black_req)
    assert check_row_result(black_req, black_res) == []
    assert all(t.status is TubeAnalysisStatus.LOW_QUALITY for t in black_res.tubes)

    analyzer.close()
    analyzer.close()
    with pytest.raises(AnalyzerError):
        analyzer.analyze_row(req)


def test_onnx_segmenter_decode_pipeline_with_fake_session(tmp_path: Path) -> None:
    model_file = tmp_path / "fake.onnx"
    model_file.write_bytes(b"fake")

    @dataclass
    class _Node:
        name: str
        shape: list[Any]

    @dataclass
    class _Meta:
        custom_metadata_map: dict[str, str]

    class _FakeSession:
        def __init__(self) -> None:
            # 1 个 tube + 1 个 gel，输入 640x640，proto 160x160
            self._pred = np.zeros((1, 4 + 2 + 32, 2), dtype=np.float32)
            # det 0: gel (class 0) at x=320, y=200, w=80, h=60
            self._pred[0, :4, 0] = [320.0, 200.0, 80.0, 60.0]
            self._pred[0, 4, 0] = 0.95
            self._pred[0, 6:, 0] = 1.0
            # det 1: tube (class 1) at x=320, y=260, w=100, h=220
            self._pred[0, :4, 1] = [320.0, 260.0, 100.0, 220.0]
            self._pred[0, 5, 1] = 0.96
            self._pred[0, 6:, 1] = 1.0

            self._proto = np.ones((1, 32, 160, 160), dtype=np.float32)

        def get_inputs(self) -> list[_Node]:
            return [_Node("images", [1, 3, 640, 640])]

        def get_outputs(self) -> list[_Node]:
            return [_Node("output0", [1, 38, 2]), _Node("output1", [1, 32, 160, 160])]

        def get_modelmeta(self) -> _Meta:
            return _Meta(custom_metadata_map={"names": "{0: 'gel', 1: 'tube'}"})

        def get_providers(self) -> list[str]:
            return ["CPUExecutionProvider"]

        def run(self, _outputs: list[str], _inputs: dict[str, Any]) -> list[Any]:
            return [self._pred, self._proto]

    seg = OnnxGelSegmenter(session_factory=lambda _p, _c: _FakeSession())
    meta = seg.load(model_file, SegmentConfig())
    assert meta.gel_class_id == 0 and meta.tube_class_id == 1

    img = np.full((480, 640, 3), 80, dtype=np.uint8)
    out = seg.segment(img, SegmentConfig())
    assert len(out.gels) == 1 and len(out.tubes) == 1
    assert out.gels[0].mask.shape == (480, 640)
    assert out.gels[0].mask.dtype == np.uint8
    assert set(np.unique(out.gels[0].mask)).issubset({0, 255})


@pytest.mark.skipif(
    not (REAL_MODEL.is_file() and REAL_SAMPLE_VIS.is_file()),
    reason="需要本地 ckpt/gel-seg-v1.onnx 与 inspection 样本",
)
def test_real_ckpt_onnx_inference_on_video1_sample() -> None:
    vis = cv2.imread(str(REAL_SAMPLE_VIS))
    assert vis is not None
    # Video_1_judge.jpg 为左右拼接图（1352 + 5px 白线 + 1348）
    w = min(1352, vis.shape[1] - 1357)
    t0_panel: NDArray[np.uint8] = vis[:, :w].astype(np.uint8, copy=True)
    t2_panel: NDArray[np.uint8] = vis[:, 1357 : 1357 + w].astype(np.uint8, copy=True)
    before_panel: NDArray[np.uint8] = t0_panel.copy()

    analyzer = YoloOnnxAnalyzer()
    info = analyzer.load(REAL_CONFIG)
    assert info.model_version == "gel-seg-v1"
    assert info.config_version == "yolo-onnx-v1"
    try:
        req = _request_from_images(before_panel, t0_panel, t2_panel, tube_nos=list(range(1, 9)))
        res = analyzer.analyze_row(req)
        assert check_row_result(req, res) == []
        by_no = {t.tube_no: t for t in res.tubes}
        assert len(by_no) == 8
        ok_tubes = [t for t in res.tubes if t.status is TubeAnalysisStatus.OK]
        assert len(ok_tubes) >= 4
        for t in ok_tubes:
            assert t.score is not None
            assert t.state is not None
            assert t.features is not None
            assert "fill_ratio_t0" in t.features.extra
            assert "height_norm_t2" in t.features.extra
            assert t.features.extra["frame_pair_mode"] == "post_flip_span"
    finally:
        analyzer.close()


def test_end_to_end_pipeline_engine_yolo_onnx_and_media_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打通视频流 -> 抓帧 -> 引擎 -> YoloOnnxAnalyzer -> 掩码归档 -> MediaService 全链路。"""
    from gelinspect.app_context import build_app_context
    from gelinspect.domain.enums import ReagentKind
    from gelinspect.services.config import parse_config
    from gelinspect.services.experiment_draft import ExperimentDraft, NewExperimentRequest

    model_file = tmp_path / "e2e.onnx"
    model_file.write_bytes(b"e2e-stub")
    algo_cfg = tmp_path / "analyzer.yaml"
    algo_cfg.write_text(
        f"model_path: {model_file.as_posix()!r}\nframe_pair_mode: 'post_flip_span'\n",
        encoding="utf-8",
    )

    cfg = parse_config(
        {
            "data_dir": str(tmp_path / "data"),
            "analysis": {
                "backend": "gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer",
                "config_path": str(algo_cfg),
                "timeout_ms": 5000,
            },
            "video": {
                "source": "simulation.sim_video:SimVideoSource",
                "device": "sim",
                "width": 640,
                "height": 360,
                "fps": 20,
                "snapshot": {"settle_s": 0.0, "after_span_s": 0.2, "wait_timeout_s": 5.0},
            },
            "device": {
                "backend": "simulation.sim_device:SimDevice",
                "options": {
                    "time_scale": 600.0,
                    "tray_duration_s": 0.0,
                    "flip_duration_s": 0.0,
                    "settle_duration_s": 0.0,
                },
            },
            "engine": {"incubation_minutes": 0.01, "time_scale": 600.0},
            "bootstrap": {"hook": "simulation.demo_accounts:DemoAccounts"},
            "traceability": {
                "parser": {"backend": "simulation.reagents:SimReagentCodeParser"},
                "registry": {"backend": "simulation.reagents:SimReagentRegistry"},
                "government": {"backend": "simulation.reagents:SimTraceabilityClient"},
            },
        },
        base_dir=ROOT,
    )
    ctx = build_app_context(cfg)
    try:
        # 用确定性分割结果替换真实 ONNX 会话，验证完整引擎+视频+落库+MediaService 链路
        shape = (360, 640)
        seg = FrameSegmentation(
            gels=(_instance(shape, 40, 40, 120, 90),),
            tubes=(_instance(shape, 35, 30, 125, 220),),
        )
        analyzer = ctx.analyzer
        assert isinstance(analyzer, YoloOnnxAnalyzer)

        monkeypatch.setattr(analyzer._segmenter, "load", lambda _path, _cfg: None)
        monkeypatch.setattr(analyzer._segmenter, "segment", lambda _img, _cfg: seg)
        monkeypatch.setattr(type(analyzer._segmenter), "is_loaded", property(lambda _s: True))

        session = ctx.auth.login("analyst", "12345678")
        tpl = ctx.templates.get_template(session, "builtin.limit").template
        draft = ExperimentDraft()
        draft.choose_template(tpl)
        draft.set_selected(range(1, len(tpl.items) + 1))
        base = draft.to_request()
        codes = {
            ReagentKind.LAL_REAGENT: "GL-LAL-0.25|LAL2606A|lal_reagent|20991231|0001",
            ReagentKind.ENDOTOXIN: "GL-CSE-10|CSE2605A|endotoxin|20991231|0001",
            ReagentKind.WATER: "GL-BET-W|WAT2607A|water|20991231|0001",
        }
        experiment_id = ctx.experiments.create_experiment(
            session, NewExperimentRequest(base.template_id, base.assignments, codes)
        )
        ctx.experiments.start_experiment(session, experiment_id)
        assert ctx.engine.wait(30.0)

        exp = ctx.database.experiments.get(experiment_id)
        assert exp is not None
        res = exp.results.get(1)
        assert res is not None
        assert res.analysis_status is TubeAnalysisStatus.OK
        extra = res.features.get("extra")
        assert isinstance(extra, dict)
        assert extra["frame_pair_mode"] == "post_flip_span"

        imgs = ctx.media.tube_images(session, experiment_id, 1, post_flip_span=True)
        assert imgs.before is not None and imgs.after is not None
        assert imgs.after_initial is not None
        assert len(imgs.sequence_before) == 3 and len(imgs.sequence_after) == 3
    finally:
        ctx.close()


def test_config_validation_provider_and_model_resolution(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from gelinspect.infrastructure.analysis.gel_inspection_core import inference_providers
    from gelinspect.infrastructure.analysis.yolo_onnx import YoloOnnxConfig, resolve_model_path

    assert inference_providers("cuda", ["CUDAExecutionProvider", "CPUExecutionProvider"]) == [
        ("CUDAExecutionProvider", {"device_id": 0}),
        "CPUExecutionProvider",
    ]
    assert inference_providers("gpu", ["CUDAExecutionProvider", "CPUExecutionProvider"]) == [
        ("CUDAExecutionProvider", {"device_id": 0}),
        "CPUExecutionProvider",
    ]

    with pytest.raises(ValidationError):
        FeatureScoringConfig(fill_weight=0.0, height_weight=0.0, flatness_weight=0.0)
    with pytest.raises(ValidationError):
        YoloOnnxConfig(slot_x_centers=[1.0, 2.0])
    with pytest.raises(ValidationError):
        YoloOnnxConfig(roi_x_range=(500.0, 100.0))

    deploy = tmp_path / "deploy"
    (deploy / "config").mkdir(parents=True)
    (deploy / "ckpt").mkdir(parents=True)
    cfg_file = deploy / "config" / "analyzer.yolo_onnx.yaml"
    cfg_file.write_text("model_path: 'ckpt/custom-external.onnx'\n", encoding="utf-8")
    expected_ckpt = deploy / "ckpt" / "custom-external.onnx"
    expected_ckpt.write_bytes(b"onnx")
    assert resolve_model_path("ckpt/custom-external.onnx", cfg_file) == expected_ckpt


def test_recorder_min_duration_extends_clip_and_is_interruptible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from gelinspect.app_context import build_app_context
    from gelinspect.domain.enums import ReagentKind
    from gelinspect.services.config import parse_config
    from gelinspect.services.experiment_draft import ExperimentDraft, NewExperimentRequest

    shape = (360, 640)
    seg = FrameSegmentation(
        gels=(_instance(shape, 40, 40, 120, 90),),
        tubes=(_instance(shape, 35, 30, 125, 220),),
    )

    def _run_case(data_sub: str, min_dur: float, *, stop_early: bool) -> float:
        model_file = tmp_path / f"{data_sub}.onnx"
        model_file.write_bytes(b"stub")
        algo_cfg = tmp_path / f"{data_sub}.yaml"
        algo_cfg.write_text(
            f"model_path: {model_file.as_posix()!r}\nframe_pair_mode: 'post_flip_span'\n",
            encoding="utf-8",
        )
        cfg = parse_config(
            {
                "data_dir": str(tmp_path / data_sub),
                "analysis": {
                    "backend": "gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer",
                    "config_path": str(algo_cfg),
                    "timeout_ms": 5000,
                },
                "video": {
                    "source": "simulation.sim_video:SimVideoSource",
                    "device": "sim",
                    "width": 640,
                    "height": 360,
                    "fps": 15,
                    "snapshot": {"settle_s": 0.0, "after_span_s": 0.1, "wait_timeout_s": 5.0},
                    "recorder": {"pre_flip_s": 0.2, "min_duration_s": min_dur},
                },
                "device": {
                    "backend": "simulation.sim_device:SimDevice",
                    "options": {
                        "time_scale": 600.0,
                        "tray_duration_s": 0.0,
                        "flip_duration_s": 0.0,
                        "settle_duration_s": 0.0,
                    },
                },
                "engine": {"incubation_minutes": 0.005, "time_scale": 600.0},
                "bootstrap": {"hook": "simulation.demo_accounts:DemoAccounts"},
                "traceability": {
                    "parser": {"backend": "simulation.reagents:SimReagentCodeParser"},
                    "registry": {"backend": "simulation.reagents:SimReagentRegistry"},
                    "government": {"backend": "simulation.reagents:SimTraceabilityClient"},
                },
            },
            base_dir=ROOT,
        )
        ctx = build_app_context(cfg)
        try:
            analyzer = ctx.analyzer
            assert isinstance(analyzer, YoloOnnxAnalyzer)
            monkeypatch.setattr(analyzer._segmenter, "load", lambda _p, _c: None)
            monkeypatch.setattr(analyzer._segmenter, "segment", lambda _i, _c: seg)
            monkeypatch.setattr(type(analyzer._segmenter), "is_loaded", property(lambda _s: True))

            session = ctx.auth.login("analyst", "12345678")
            tpl = ctx.templates.get_template(session, "builtin.limit").template
            draft = ExperimentDraft()
            draft.choose_template(tpl)
            draft.set_selected(range(1, len(tpl.items) + 1))
            base = draft.to_request()
            codes = {
                ReagentKind.LAL_REAGENT: "GL-LAL-0.25|LAL2606A|lal_reagent|20991231|0001",
                ReagentKind.ENDOTOXIN: "GL-CSE-10|CSE2605A|endotoxin|20991231|0001",
                ReagentKind.WATER: "GL-BET-W|WAT2607A|water|20991231|0001",
            }
            exp_id = ctx.experiments.create_experiment(
                session, NewExperimentRequest(base.template_id, base.assignments, codes)
            )
            started = time.monotonic()
            ctx.experiments.start_experiment(session, exp_id)
            if stop_early:
                deadline = time.monotonic() + 10.0
                while not ctx.pipeline.recorder.active and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert ctx.pipeline.recorder.active
                time.sleep(0.35)
                ctx.engine.stop(session.user, "提前停止")
                assert ctx.engine.wait(5.0)
                return time.monotonic() - started
            assert ctx.engine.wait(15.0)
            clip = ctx.media.open_clip(session, exp_id, 1)
            try:
                return clip.duration_s
            finally:
                clip.close()
        finally:
            ctx.close()

    duration = _run_case("dur_ok", 1.2, stop_early=False)
    assert duration >= 1.0
    stop_elapsed = _run_case("dur_stop", 10.0, stop_early=True)
    assert stop_elapsed < 3.0
