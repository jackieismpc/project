"""工业相机视频源模板，相机型号和 SDK 确定后实现。

接入步骤：

1. 在 ``open`` 内部导入厂商 SDK，保证未安装 SDK 的机器上模块仍可导入。
2. 用 ``VideoConfig.device`` 中的序列号打开相机，设置分辨率、帧率和曝光。
3. 把 SDK 的像素格式（通常是 Bayer）转换为 H×W×3、uint8、BGR。
4. 把 SDK 错误转换为 ``gelinspect.interfaces.video`` 中的异常。
5. 在配置中设置 ``video.source`` 指向本类，并按 4000×3000 运行 P5 的压力测试。
"""

from __future__ import annotations

from gelinspect.interfaces.video import VideoConfig, VideoFrame, VideoInfo


class IndustrialCameraSource:
    """``VideoSource`` 的工业相机实现模板。"""

    def open(self, config: VideoConfig) -> VideoInfo:
        """打开工业相机。

        Args:
            config: 视频源配置。

        Returns:
            实际生效的参数，``is_live`` 为 True。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：枚举设备 → 按序列号打开 → 设置参数 → 开始取流 → 回读实际参数。
        raise NotImplementedError("请在相机选型后实现")

    def read(self) -> VideoFrame | None:
        """读取下一帧。

        Returns:
            下一帧，超时时返回 None。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：带超时取帧 → 超时返回 None → 掉线抛 VideoDisconnectedError
        # → 立刻记下 captured_at（必须在转换之前：抓帧按它判断帧在翻转前后）
        # → 转换为 BGR → 复制出 SDK 缓冲区并归还 → 组装 VideoFrame。
        raise NotImplementedError("请在相机选型后实现")

    def close(self) -> None:
        """关闭相机，可重复调用。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：停止取流 → 关闭设备 → 释放 SDK 句柄。
        raise NotImplementedError("请在相机选型后实现")
