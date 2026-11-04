"""适配模板必须满足 Protocol，并且方法抛 NotImplementedError。"""

from typing import Any

import pytest

from gelinspect.infrastructure.analysis.adapter_template import GelAnalyzerAdapter
from gelinspect.infrastructure.device.adapter_template import DeviceControllerAdapter
from gelinspect.infrastructure.video.industrial_template import IndustrialCameraSource
from gelinspect.interfaces.analysis import GelAnalyzer
from gelinspect.interfaces.device import DeviceController, TrayPosition
from gelinspect.interfaces.video import VideoConfig, VideoSource

# 静态检查：模板在 mypy 下也必须满足 Protocol。
_analyzer: GelAnalyzer = GelAnalyzerAdapter()
_device: DeviceController = DeviceControllerAdapter()
_video: VideoSource = IndustrialCameraSource()


@pytest.mark.parametrize(
    ("template", "protocol"),
    [
        (GelAnalyzerAdapter, GelAnalyzer),
        (DeviceControllerAdapter, DeviceController),
        (IndustrialCameraSource, VideoSource),
    ],
)
def test_template_satisfies_protocol(template: type, protocol: type) -> None:
    assert isinstance(template(), protocol)


@pytest.mark.parametrize(
    ("instance", "method", "args"),
    [
        (GelAnalyzerAdapter(), "load", (None,)),
        (GelAnalyzerAdapter(), "analyze_row", (object(),)),
        (GelAnalyzerAdapter(), "close", ()),
        (DeviceControllerAdapter(), "connect", ()),
        (DeviceControllerAdapter(), "move_tray", (TrayPosition.IN,)),
        (DeviceControllerAdapter(), "flip_row", (1,)),
        (DeviceControllerAdapter(), "restore_row", (1,)),
        (DeviceControllerAdapter(), "read_status", ()),
        (DeviceControllerAdapter(), "close", ()),
        (IndustrialCameraSource(), "open", (VideoConfig(device="0"),)),
        (IndustrialCameraSource(), "read", ()),
        (IndustrialCameraSource(), "close", ()),
    ],
)
def test_template_methods_raise_not_implemented(
    instance: Any, method: str, args: tuple[Any, ...]
) -> None:
    with pytest.raises(NotImplementedError):
        getattr(instance, method)(*args)
