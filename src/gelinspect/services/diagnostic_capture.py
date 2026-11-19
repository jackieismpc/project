"""记录实际分析调用的完整结构化输出及掩码。"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np

from gelinspect.interfaces.analysis import RowAnalysisRequest, RowAnalysisResult
from gelinspect.services.video.paths import ensure_inside, validate_experiment_id


def capture_analysis(
    data_dir: Path, request: RowAnalysisRequest, result: RowAnalysisResult, at: datetime
) -> Path:
    """原子保存一次原始输出；重复管号、额外管号和掩码均保留。"""
    experiment_id = validate_experiment_id(request.experiment_id)
    root = data_dir / "diagnostics" / "analysis"
    directory = ensure_inside(root, root / at.astimezone().date().isoformat() / experiment_id)
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "experiment_id": experiment_id,
        "row_index": request.row_index,
        "captured_at": at.isoformat(),
        "tubes": [slot.model_dump(mode="json") for slot in request.tubes],
        "frames_before": [
            {
                "name": f.source_path.name if f.source_path else None,
                "captured_at": f.captured_at.isoformat(),
            }
            for f in request.frames_before
        ],
        "frames_after": [
            {
                "name": f.source_path.name if f.source_path else None,
                "captured_at": f.captured_at.isoformat(),
            }
            for f in request.frames_after
        ],
        "flip_angle_deg": request.flip_angle_deg,
        "tube_spec": request.tube_spec,
        "reagent_batch": request.reagent_batch,
        "analyzer_info": result.analyzer_info.model_dump(mode="json"),
        "elapsed_ms": result.elapsed_ms,
        "results": [tube.model_dump(mode="json", exclude={"masks"}) for tube in result.tubes],
    }
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".analysis-", suffix=".tmp", delete=False
        ) as handle:
            temp = Path(handle.name)
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("result.json", json.dumps(metadata, ensure_ascii=False, indent=2))
            for index, tube in enumerate(result.tubes):
                if tube.masks is None:
                    continue
                for name in ("tube_before", "tube_after", "gel_before", "gel_after"):
                    array = getattr(tube.masks, name)
                    if array is not None:
                        with archive.open(
                            f"masks/{index:03d}-{name}.npy", "w", force_zip64=True
                        ) as target:
                            np.save(target, array, allow_pickle=False)
        target_path = directory / f"row-{request.row_index:02d}-{at.strftime('%H%M%S%f')}.zip"
        os.replace(temp, target_path)
        temp = None
        return target_path
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
