"""契约测试的后端选择和请求构造。

默认校验 ``FakeGelAnalyzer``。可通过以下方式追加后端，两者可同时使用：

- 命令行：``--analyzer-backend=pkg.module:Class``（可重复）；
- 环境变量：``GELINSPECT_ANALYZER_BACKEND=pkg.a:A,pkg.b:B``。

算法配置文件用 ``--analyzer-config`` 或环境变量 ``GELINSPECT_ANALYZER_CONFIG`` 指定。
"""

import importlib
import os
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

from gelinspect.domain.plate import tubes_in_row
from gelinspect.interfaces.analysis import (
    CapturedFrame,
    GelAnalyzer,
    RowAnalysisRequest,
    TubeSlot,
)

FAKE_BACKEND = "tests.contract.fake_analyzer:FakeGelAnalyzer"
FRAME_SHAPE = (1080, 1920, 3)

RequestFactory = Callable[..., RowAnalysisRequest]
AnalyzerFactory = Callable[[], GelAnalyzer]


def load_backend_class(spec: str) -> type:
    """按 ``pkg.module:Class`` 或 ``pkg.module.Class`` 导入后端类。"""
    module_name, separator, class_name = spec.partition(":")
    if not separator:
        module_name, _, class_name = spec.rpartition(".")
    backend = getattr(importlib.import_module(module_name), class_name)
    assert isinstance(backend, type), f"{spec} 不是类"
    return backend


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "analyzer_backend" not in metafunc.fixturenames:
        return
    from_env = os.environ.get("GELINSPECT_ANALYZER_BACKEND", "")
    specs = [
        FAKE_BACKEND,
        *metafunc.config.getoption("--analyzer-backend"),
        *(spec.strip() for spec in from_env.split(",") if spec.strip()),
    ]
    metafunc.parametrize("analyzer_backend", list(dict.fromkeys(specs)))


@pytest.fixture
def analyzer_factory(analyzer_backend: str) -> AnalyzerFactory:
    """构造未加载的算法实例。"""
    backend = load_backend_class(analyzer_backend)
    return lambda: cast(GelAnalyzer, backend())


@pytest.fixture
def analyzer_config(request: pytest.FixtureRequest) -> Path | None:
    value = request.config.getoption("--analyzer-config") or os.environ.get(
        "GELINSPECT_ANALYZER_CONFIG"
    )
    return Path(value) if value else None


@pytest.fixture
def analyzer(
    analyzer_factory: AnalyzerFactory, analyzer_config: Path | None
) -> Iterator[GelAnalyzer]:
    """已加载的算法实例，测试结束后关闭。"""
    instance = analyzer_factory()
    instance.load(analyzer_config)
    yield instance
    instance.close()


def _synthetic_row_image() -> NDArray[np.uint8]:
    image = np.full(FRAME_SHAPE, 40, dtype=np.uint8)
    column_width = FRAME_SHAPE[1] // 8
    for column in range(8):
        left = column * column_width + column_width // 4
        image[200:880, left : left + column_width // 2] = 180
    return image


@pytest.fixture(scope="session")
def row_image() -> NDArray[np.uint8]:
    """一张合成的整排图像，符合文档要求的 1920×1080。"""
    return _synthetic_row_image()


@pytest.fixture
def make_request(row_image: NDArray[np.uint8]) -> RequestFactory:
    """构造分析请求，默认第 1 排 8 支、翻转前后各 3 帧。"""

    def factory(
        *,
        row_index: int = 1,
        tube_nos: Sequence[int] | None = None,
        image: NDArray[np.uint8] | None = None,
        frames_per_side: int = 3,
    ) -> RowAnalysisRequest:
        frame_image = row_image if image is None else image
        start = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
        moments = [start + timedelta(milliseconds=100 * n) for n in range(frames_per_side * 2)]
        frames = [CapturedFrame(image=frame_image, captured_at=moment) for moment in moments]
        return RowAnalysisRequest(
            experiment_id="contract-test",
            row_index=row_index,
            tubes=[
                TubeSlot.from_tube_no(no)
                for no in (tubes_in_row(row_index) if tube_nos is None else tube_nos)
            ],
            frames_before=frames[:frames_per_side],
            frames_after=frames[frames_per_side:],
            flip_angle_deg=180.0,
        )

    return factory
