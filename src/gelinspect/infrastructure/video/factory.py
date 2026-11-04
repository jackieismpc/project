"""按配置创建视频源。

配置值是 ``"包.模块:类名"`` 或 ``"包.模块.类名"``
（也接受别名 ``file``、``images``；真实相机选型后按类路径配置）。
这样主代码不需要导入 ``simulation``：模拟配置写 ``simulation.sim_video:SimVideoSource`` 即可。
创建失败时抛 :class:`VideoSourceConfigError`，**不会回退到模拟实现**。
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from gelinspect.interfaces.video import VideoError, VideoSource

ALIASES: dict[str, str] = {
    "file": "gelinspect.infrastructure.video.file_source:FileVideoSource",
    "images": "gelinspect.infrastructure.video.image_sequence:ImageSequenceSource",
}
"""内置视频源的别名。"""


class VideoSourceConfigError(VideoError):
    """视频源配置有误：类路径无法导入、不是类，或没有实现 ``VideoSource``。"""


def create_video_source(spec: str, options: Mapping[str, Any] | None = None) -> VideoSource:
    """按配置字符串创建视频源。

    Args:
        spec: 类路径或别名。
        options: 传给构造函数的关键字参数，默认无参构造。

    Returns:
        视频源实例（尚未打开）。

    Raises:
        VideoSourceConfigError: 路径格式错误、导入失败、构造失败，或实例不满足 ``VideoSource``。
    """
    path = ALIASES.get(spec, spec).strip()
    module_name, sep, class_name = path.partition(":")
    if not sep:
        module_name, _, class_name = path.rpartition(".")
    if not module_name or not class_name:
        raise VideoSourceConfigError(f"视频源配置格式应为 '包.模块:类名'：{spec!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise VideoSourceConfigError(f"无法导入视频源模块 {module_name}：{exc}") from exc
    cls = getattr(module, class_name, None)
    if not isinstance(cls, type):
        raise VideoSourceConfigError(f"{module_name} 中没有名为 {class_name} 的类")
    try:
        instance = cls(**dict(options or {}))
    except Exception as exc:
        raise VideoSourceConfigError(f"创建视频源 {spec} 失败：{exc!r}") from exc
    if not isinstance(instance, VideoSource):
        raise VideoSourceConfigError(f"{spec} 没有实现 VideoSource（需要 open、read、close）")
    return instance
