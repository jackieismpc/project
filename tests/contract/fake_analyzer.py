"""契约测试使用的最小假算法，按孔号固定产出各种状态，覆盖全部字段组合。"""

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from gelinspect.interfaces.analysis import (
    AnalyzerError,
    AnalyzerInfo,
    GelFeatures,
    GelState,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeAnalysis,
    TubeAnalysisStatus,
    TubeMasks,
    TubeSlot,
)


def _rect_mask(size: tuple[int, int], position: int) -> NDArray[np.uint8]:
    height, width = size
    mask = np.zeros(size, dtype=np.uint8)
    column_width = width // 8
    left = (position - 1) * column_width
    mask[height // 4 : height * 3 // 4, left : left + column_width // 2] = 255
    return mask


def _complete_features() -> GelFeatures:
    return GelFeatures(
        area_before=1000.0,
        area_after=990.0,
        delta_area_pct=1.0,
        centroid_before=(10.0, 20.0),
        centroid_after=(10.0, 21.0),
        axial_before=0.5,
        axial_after=0.51,
        delta_s=0.01,
        sigma_area=1.0,
        sigma_axial=0.01,
    )


def _analyze_slot(slot: TubeSlot, size: tuple[int, int]) -> TubeAnalysis:
    tube = _rect_mask(size, slot.position_in_row)
    gel = tube.copy()
    full = TubeMasks(tube_before=tube, tube_after=tube, gel_before=gel, gel_after=gel)
    ok_states = {
        1: GelState.FULLY_GELLED,
        2: GelState.PARTIALLY_GELLED,
        3: GelState.NOT_GELLED,
    }
    match slot.position_in_row:
        case position if position in ok_states:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.OK,
                state=ok_states[position],
                score=0.9,
                confidence=0.95,
                features=_complete_features(),
                masks=full,
            )
        case 4:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.GEL_MISSING,
                state=GelState.NOT_GELLED,
                score=0.1,
                confidence=0.8,
                features=GelFeatures(
                    area_before=1000.0,
                    centroid_before=(10.0, 20.0),
                    axial_before=0.5,
                    gel_missing_after=True,
                ),
                masks=TubeMasks(tube_before=tube, tube_after=tube, gel_before=gel),
            )
        case 5:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.GEL_MISSING,
                features=GelFeatures(gel_missing_before=True, gel_missing_after=True),
                masks=TubeMasks(tube_before=tube, tube_after=tube),
            )
        case 6:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.LOW_QUALITY,
                message="图像过曝",
            )
        case 7:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.TUBE_NOT_FOUND,
                message="未检测到试管",
            )
        case _:
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.FAILED,
                message="推理失败",
            )


class FakeGelAnalyzer:
    """满足 ``GelAnalyzer`` 契约的假实现。"""

    def __init__(self) -> None:
        self._info: AnalyzerInfo | None = None

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        self._info = AnalyzerInfo(model_version="fake-1", config_version="fake-config-1")
        return self._info

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        if self._info is None:
            raise AnalyzerError("模型未加载")
        return RowAnalysisResult(
            analyzer_info=self._info,
            elapsed_ms=1.0,
            tubes=[_analyze_slot(slot, request.image_size) for slot in request.tubes],
        )

    def close(self) -> None:
        self._info = None
