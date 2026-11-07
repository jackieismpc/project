"""视频管线配置。

全部配置项都有默认值，可以直接由 YAML 字典 ``VideoPipelineConfig.model_validate`` 解析。
未知字段会被拒绝，避免拼写错误被静默忽略。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gelinspect.interfaces.video import VideoConfig

MIB = 1024 * 1024


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BufferConfig(_Model):
    """环形缓冲配置。

    Attributes:
        max_bytes: 缓冲的字节数上限（按图像 ``nbytes`` 累计），超出时淘汰最旧的未受保护帧。
        pin_hard_limit_bytes: 含受保护帧在内的硬上限，默认为 ``max_bytes`` 的 1.5 倍。
            超过时发出 ``PIN_OVERFLOW`` 告警，并淘汰最旧的受保护帧，内存绝不无限增长。
    """

    max_bytes: int = Field(default=1024 * MIB, gt=0)
    pin_hard_limit_bytes: int | None = Field(default=None, gt=0)

    @property
    def hard_limit_bytes(self) -> int:
        """生效的硬上限（不小于 ``max_bytes``）。"""
        if self.pin_hard_limit_bytes is None:
            return self.max_bytes + self.max_bytes // 2
        return max(self.pin_hard_limit_bytes, self.max_bytes)


class PreviewConfig(_Model):
    """预览配置。

    Attributes:
        max_fps: 预览帧率上限。
        long_side: 预览图长边像素数，大于原图时不放大。
    """

    max_fps: float = Field(default=15.0, gt=0)
    long_side: int = Field(default=960, gt=0)


class RecorderConfig(_Model):
    """分段录制配置。

    Attributes:
        long_side: 录制图像长边像素数。12MP 原尺寸写盘不现实，所以默认缩到 1920。
        fourcc: 编码器四字符码，默认 ``mp4v``。
        default_fps: 无法得知帧率时写入容器的帧率。
        queue_max_frames: 写盘队列的帧数上限，满了丢录制帧。
        queue_max_bytes: 写盘队列的字节数上限，满了丢录制帧。
        finish_timeout_s: ``stop_segment`` 等待写盘线程收尾的最长时间。
    """

    long_side: int = Field(default=1920, gt=0)
    fourcc: str = Field(default="mp4v", min_length=4, max_length=4)
    default_fps: float = Field(default=10.0, gt=0)
    queue_max_frames: int = Field(default=60, gt=0)
    queue_max_bytes: int = Field(default=512 * MIB, gt=0)
    finish_timeout_s: float = Field(default=60.0, gt=0)


class SnapshotConfig(_Model):
    """抓帧配置。

    Attributes:
        frames_per_side: 翻转前后各取多少帧，算法要求至少 3 帧。
        settle_s: 翻转结束后的稳定时间，之后的帧才算翻转后的帧。
        wait_timeout_s: 等待翻转后帧凑够 n 帧的最长时间（从翻转结束起算）。
        image_format: ``png`` 为无损，``jpg`` 为有损。
        png_compression: PNG 压缩级别 0–9，越大越慢越小。
        jpeg_quality: JPG 质量 1–100。
        io_workers: 并行编码、写盘的线程数。
    """

    frames_per_side: int = Field(default=3, ge=1)
    settle_s: float = Field(default=1.0, ge=0)
    wait_timeout_s: float = Field(default=10.0, gt=0)
    image_format: Literal["png", "jpg"] = "png"
    png_compression: int = Field(default=3, ge=0, le=9)
    jpeg_quality: int = Field(default=95, ge=1, le=100)
    io_workers: int = Field(default=4, ge=1)


class QualityConfig(_Model):
    """质量检查配置。

    Attributes:
        enabled: 是否启用图像质量检查（看门狗和帧率统计始终启用）。
        check_every_n_frames: 每隔多少帧取一帧做图像分析。
        analysis_long_side: 分析用缩小图的长边像素数。
        black_mean_max: 平均灰度低于此值判为黑屏。
        overexposed_level: 灰度不低于此值的像素算饱和。
        overexposed_fraction: 饱和像素占比超过此值判为过曝。
        frozen_diff_max: 相邻两次采样的平均绝对差低于此值算“无变化”。
        frozen_samples: 连续多少次无变化判为卡帧。
        expected_fps: 期望帧率，None 时用视频源报告的帧率；都没有则不检查低帧率。
        low_fps_ratio: 实测帧率低于期望的这个比例时告警。
        fps_window_s: 统计实测帧率的滑动窗口。
        watchdog_timeout_s: 超过这么久没收到新帧，判为断流。
        tick_interval_s: 看门狗和帧率检查的周期。
    """

    enabled: bool = True
    check_every_n_frames: int = Field(default=10, ge=1)
    analysis_long_side: int = Field(default=160, ge=8)
    black_mean_max: float = Field(default=8.0, ge=0)
    overexposed_level: int = Field(default=250, ge=1, le=255)
    overexposed_fraction: float = Field(default=0.6, gt=0, le=1)
    frozen_diff_max: float = Field(default=0.02, ge=0)
    frozen_samples: int = Field(default=3, ge=1)
    expected_fps: float | None = Field(default=None, gt=0)
    low_fps_ratio: float = Field(default=0.5, gt=0, le=1)
    fps_window_s: float = Field(default=5.0, gt=0)
    watchdog_timeout_s: float = Field(default=3.0, gt=0)
    tick_interval_s: float = Field(default=0.25, gt=0)


class ReconnectConfig(_Model):
    """断流后的重连策略（指数退避）。

    Attributes:
        enabled: 是否自动重连。关闭时断流后采集线程直接结束，状态为失败。
        initial_delay_s: 第一次重试前的等待。
        max_delay_s: 退避的最长等待。
        factor: 每次失败后的等待倍数。
        max_attempts: 最多重试次数，None 表示一直重试直到停止。
        stall_disconnect_s: 实时源连续这么久读不到帧（``read`` 一直返回 None）就当作断流，
            None 表示不判定。
    """

    enabled: bool = True
    initial_delay_s: float = Field(default=0.5, gt=0)
    max_delay_s: float = Field(default=10.0, gt=0)
    factor: float = Field(default=2.0, ge=1)
    max_attempts: int | None = Field(default=None, ge=1)
    stall_disconnect_s: float | None = Field(default=10.0, gt=0)


class VideoPipelineConfig(_Model):
    """视频管线总配置。

    Attributes:
        data_dir: 数据根目录，视频写到 ``<data_dir>/videos``，图像写到 ``<data_dir>/images``。
        source: 视频源配置，传给 ``VideoSource.open``。
        buffer: 环形缓冲。
        preview: 预览。
        recorder: 分段录制。
        snapshot: 抓帧。
        quality: 质量检查。
        reconnect: 重连策略。
        open_timeout_s: ``start`` 等待视频源打开的最长时间。
        stop_timeout_s: ``stop`` 等待每个工作线程退出的最长时间。
    """

    data_dir: Path
    source: VideoConfig
    buffer: BufferConfig = BufferConfig()
    preview: PreviewConfig = PreviewConfig()
    recorder: RecorderConfig = RecorderConfig()
    snapshot: SnapshotConfig = SnapshotConfig()
    quality: QualityConfig = QualityConfig()
    reconnect: ReconnectConfig = ReconnectConfig()
    open_timeout_s: float = Field(default=30.0, gt=0)
    stop_timeout_s: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _check_limits(self) -> VideoPipelineConfig:
        if self.reconnect.initial_delay_s > self.reconnect.max_delay_s:
            raise ValueError("reconnect.initial_delay_s 不能大于 max_delay_s")
        return self
