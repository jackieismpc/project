"""把工作线程里的引擎事件转到 Qt 主线程。

试验引擎的事件在 ``engine-events`` 线程上回调，仪器状态在 ``instrument-monitor`` 线程上回调，
界面对象只能在主线程使用。:class:`EngineBridge` 的回调可以在任意线程调用，
它发出一个内部信号，接收槽属于主线程，所以 Qt 用排队连接投递，
随后在主线程上把事件按类型分发成带类型的信号。界面只连接这些信号，不直接碰引擎线程。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QImage

from gelinspect.services.engine_types import (
    EngineAlert,
    EngineEvent,
    EngineEventListener,
    RowCaptured,
    RowCountdown,
    StateChanged,
    TemperatureChanged,
    WellUpdated,
)
from gelinspect.services.instrument_monitor import InstrumentStatus
from gelinspect.services.video.events import VideoEvent
from gelinspect.services.video.preview import PreviewFrame


class EngineEventSource(Protocol):
    """能订阅引擎事件的对象（``ExperimentEngine``）。"""

    def add_listener(self, listener: EngineEventListener) -> None:
        """添加监听者。"""
        ...

    def remove_listener(self, listener: EngineEventListener) -> None:
        """移除监听者。"""
        ...


class InstrumentSource(Protocol):
    """能订阅仪器状态的对象（``InstrumentMonitor``）。"""

    def add_listener(self, listener: Callable[[InstrumentStatus], None]) -> None:
        """添加监听者。"""
        ...

    def remove_listener(self, listener: Callable[[InstrumentStatus], None]) -> None:
        """移除监听者。"""
        ...


class VideoSource(Protocol):
    """能订阅预览帧和视频事件的对象（``MonitorService``）。"""

    def add_preview_listener(self, listener: Callable[[PreviewFrame], None]) -> None:
        """订阅预览帧。"""
        ...

    def remove_preview_listener(self, listener: Callable[[PreviewFrame], None]) -> None:
        """取消订阅预览帧。"""
        ...

    def add_video_listener(self, listener: Callable[[VideoEvent], None]) -> None:
        """订阅视频事件。"""
        ...

    def remove_video_listener(self, listener: Callable[[VideoEvent], None]) -> None:
        """取消订阅视频事件。"""
        ...


def preview_to_image(frame: PreviewFrame) -> QImage:
    """把预览帧（BGR）转成独立持有数据的 QImage，可在任何线程调用。

    Args:
        frame: 预览帧。

    Returns:
        拷贝了像素的 QImage（不引用 numpy 缓冲）。
    """
    data = np.ascontiguousarray(frame.image)
    height, width = data.shape[:2]
    return QImage(data.data, width, height, width * 3, QImage.Format.Format_BGR888).copy()


class EngineBridge(QObject):
    """引擎事件和仪器状态的 Qt 信号桥。必须在主线程创建。

    Signals:
        eventReceived: 任何引擎事件，参数为 :class:`EngineEvent`。
        stateChanged: 试验状态变化（:class:`StateChanged`）。
        alertRaised: 告警（:class:`EngineAlert`）。
        temperatureChanged: 温度读数（:class:`TemperatureChanged`）。
        wellUpdated: 孔状态变化（:class:`WellUpdated`）。
        rowCountdown: 一排倒计时（:class:`RowCountdown`）。
        instrumentChanged: 仪器状态（:class:`InstrumentStatus`）。
        rowCaptured: 一排抓帧完成（:class:`RowCaptured`）。
        previewFrame: 最新的预览帧（:class:`QImage`），慢时丢帧，只发最新的。
        videoEvent: 视频管线事件（:class:`VideoEvent`）。

    Attributes:
        preview_received: 预览线程送来的帧数。
        preview_delivered: 实际转发给界面的帧数（被覆盖的不计）。
    """

    eventReceived = Signal(object)  # noqa: N815
    stateChanged = Signal(object)  # noqa: N815
    alertRaised = Signal(object)  # noqa: N815
    temperatureChanged = Signal(object)  # noqa: N815
    wellUpdated = Signal(object)  # noqa: N815
    rowCountdown = Signal(object)  # noqa: N815
    instrumentChanged = Signal(object)  # noqa: N815
    rowCaptured = Signal(object)  # noqa: N815
    previewFrame = Signal(QImage)  # noqa: N815
    videoEvent = Signal(object)  # noqa: N815

    _incoming = Signal(object)
    _incoming_instrument = Signal(object)
    _incoming_video = Signal(object)
    _preview_ready = Signal()

    def __init__(
        self,
        engine: EngineEventSource | None = None,
        instrument: InstrumentSource | None = None,
        parent: QObject | None = None,
        video: VideoSource | None = None,
    ) -> None:
        """创建桥并订阅事件源。

        Args:
            engine: 引擎，``None`` 时只能手动调用 :meth:`handle_event`。
            instrument: 仪器状态监视器。
            parent: 父对象。
            video: 预览帧和视频事件的来源。
        """
        super().__init__(parent)
        self.main_thread_id = threading.get_ident()
        self._engine = engine
        self._instrument = instrument
        self._video = video
        self._closed = False
        self._preview_lock = threading.Lock()
        self._latest_preview: QImage | None = None
        self._preview_queued = False
        self.preview_received = 0
        self.preview_delivered = 0
        # 接收槽是本对象（主线程）的方法：从其他线程发出时 Qt 自动用排队连接。
        self._incoming.connect(self._dispatch)
        self._incoming_instrument.connect(self._dispatch_instrument)
        self._incoming_video.connect(self._dispatch_video)
        self._preview_ready.connect(self._deliver_preview)
        if video is not None:
            video.add_preview_listener(self.handle_preview)
            video.add_video_listener(self.handle_video_event)
        if engine is not None:
            engine.add_listener(self.handle_event)
        if instrument is not None:
            instrument.add_listener(self.handle_instrument)

    def handle_event(self, event: EngineEvent) -> None:
        """引擎事件回调，可在任意线程调用。

        Args:
            event: 引擎事件。
        """
        if not self._closed:
            self._incoming.emit(event)

    def handle_instrument(self, status: InstrumentStatus) -> None:
        """仪器状态回调，可在任意线程调用。

        Args:
            status: 仪器状态。
        """
        if not self._closed:
            self._incoming_instrument.emit(status)

    def handle_preview(self, frame: PreviewFrame) -> None:
        """预览帧回调，可在任意线程调用。

        像素在调用线程上拷成 QImage；只保留最新一帧，主线程还没取走时新帧覆盖旧帧，
        所以界面慢时丢帧，不会堆积，也不会拖慢预览线程。

        Args:
            frame: 预览帧。
        """
        if self._closed:
            return
        image = preview_to_image(frame)
        with self._preview_lock:
            self.preview_received += 1
            self._latest_preview = image
            if self._preview_queued:
                return
            self._preview_queued = True
        self._preview_ready.emit()

    def handle_video_event(self, event: VideoEvent) -> None:
        """视频管线事件回调，可在任意线程调用。

        Args:
            event: 视频事件。
        """
        if not self._closed:
            self._incoming_video.emit(event)

    def close(self) -> None:
        """取消订阅并丢弃之后到达的事件，可重复调用。"""
        if self._closed:
            return
        self._closed = True
        if self._video is not None:
            self._video.remove_preview_listener(self.handle_preview)
            self._video.remove_video_listener(self.handle_video_event)
        if self._engine is not None:
            self._engine.remove_listener(self.handle_event)
        if self._instrument is not None:
            self._instrument.remove_listener(self.handle_instrument)

    @Slot(object)
    def _dispatch(self, event: object) -> None:
        """在主线程上按类型分发事件。"""
        if self._closed or not isinstance(event, EngineEvent):
            return
        self.eventReceived.emit(event)
        if isinstance(event, StateChanged):
            self.stateChanged.emit(event)
        elif isinstance(event, EngineAlert):
            self.alertRaised.emit(event)
        elif isinstance(event, TemperatureChanged):
            self.temperatureChanged.emit(event)
        elif isinstance(event, WellUpdated):
            self.wellUpdated.emit(event)
        elif isinstance(event, RowCountdown):
            self.rowCountdown.emit(event)
        elif isinstance(event, RowCaptured):
            self.rowCaptured.emit(event)

    @Slot()
    def _deliver_preview(self) -> None:
        """在主线程上转发最新的预览帧。"""
        with self._preview_lock:
            image = self._latest_preview
            self._latest_preview = None
            self._preview_queued = False
        if image is not None and not self._closed:
            self.preview_delivered += 1
            self.previewFrame.emit(image)

    @Slot(object)
    def _dispatch_video(self, event: object) -> None:
        """在主线程上转发视频事件。"""
        if not self._closed and isinstance(event, VideoEvent):
            self.videoEvent.emit(event)

    @Slot(object)
    def _dispatch_instrument(self, status: object) -> None:
        """在主线程上转发仪器状态。"""
        if not self._closed and isinstance(status, InstrumentStatus):
            self.instrumentChanged.emit(status)
