"""视频管线：采集、环形缓冲、预览、分段录制、抓帧和质量检查。

只依赖 ``threading``，不依赖 Qt；视频源通过 ``interfaces.video.VideoSource`` 注入。
详见 ``docs/features/video-pipeline.md``。
"""

from gelinspect.services.video.capture import CaptureState, CaptureWorker, FrameConsumer
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.config import (
    BufferConfig,
    PreviewConfig,
    QualityConfig,
    ReconnectConfig,
    RecorderConfig,
    SnapshotConfig,
    VideoPipelineConfig,
)
from gelinspect.services.video.errors import (
    InsufficientFramesError,
    InvalidIdentifierError,
    NoActiveSegmentError,
    PipelineStateError,
    RecorderError,
    SnapshotError,
    VideoPipelineError,
)
from gelinspect.services.video.events import (
    EventBus,
    VideoEvent,
    VideoEventCode,
    VideoEventLevel,
)
from gelinspect.services.video.media import segment_to_media_file, snapshot_to_media_files
from gelinspect.services.video.pipeline import PipelineState, PipelineStats, VideoPipeline
from gelinspect.services.video.preview import PreviewFrame, PreviewWorker
from gelinspect.services.video.quality import QualityMonitor, QualityStats
from gelinspect.services.video.recorder import RecorderStats, SegmentRecord, SegmentRecorder
from gelinspect.services.video.ring_buffer import BufferStats, FrameRingBuffer
from gelinspect.services.video.snapshot import SavedImage, SnapshotGrabber, SnapshotResult

__all__ = [
    "BufferConfig",
    "BufferStats",
    "CaptureState",
    "CaptureWorker",
    "Clock",
    "EventBus",
    "FrameConsumer",
    "FrameRingBuffer",
    "InsufficientFramesError",
    "InvalidIdentifierError",
    "NoActiveSegmentError",
    "PipelineState",
    "PipelineStateError",
    "PipelineStats",
    "PreviewConfig",
    "PreviewFrame",
    "PreviewWorker",
    "QualityConfig",
    "QualityMonitor",
    "QualityStats",
    "ReconnectConfig",
    "RecorderConfig",
    "RecorderError",
    "RecorderStats",
    "SavedImage",
    "SegmentRecord",
    "SegmentRecorder",
    "SnapshotConfig",
    "SnapshotError",
    "SnapshotGrabber",
    "SnapshotResult",
    "SystemClock",
    "VideoEvent",
    "VideoEventCode",
    "VideoEventLevel",
    "VideoPipeline",
    "VideoPipelineConfig",
    "VideoPipelineError",
    "segment_to_media_file",
    "snapshot_to_media_files",
]
