"""后台任务执行器和预览帧的桥接：回调在主线程、过期结果丢弃、慢消费者只留最新帧。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from gelinspect.services.video.preview import PreviewFrame
from gelinspect.ui.background import BackgroundRunner
from gelinspect.ui.bridge import EngineBridge, preview_to_image
from tests.ui.conftest import wait_for


def frame(value: int, size: tuple[int, int] = (20, 30)) -> PreviewFrame:
    image = np.full((size[1], size[0], 3), value, np.uint8)
    return PreviewFrame(image, value, datetime.now(UTC), 1920, 1080)


def test_background_tasks_run_off_the_main_thread_and_call_back_on_it(qapp: QApplication) -> None:
    runner = BackgroundRunner()
    main = threading.get_ident()
    where: dict[str, int] = {}
    results: list[int] = []

    def work() -> int:
        where["work"] = threading.get_ident()
        return 42

    def done(value: int) -> None:
        where["done"] = threading.get_ident()
        results.append(value)

    runner.submit(work, done)
    assert wait_for(qapp, lambda: results == [42])
    assert where["work"] != main and where["done"] == main
    assert runner.pending() == 0


def test_errors_are_reported_and_cancelled_results_are_dropped(qapp: QApplication) -> None:
    runner = BackgroundRunner()
    errors: list[Exception] = []
    results: list[int] = []

    def boom() -> int:
        raise ValueError("坏了")

    runner.submit(boom, lambda v: results.append(v), lambda e: errors.append(e))
    assert wait_for(qapp, lambda: len(errors) == 1) and isinstance(errors[0], ValueError)
    token = runner.submit(lambda: 1, lambda v: results.append(v))
    runner.cancel(token)
    runner.submit(lambda: 2, lambda v: results.append(v))
    assert wait_for(qapp, lambda: results == [2])
    assert runner.wait(5000)


def test_preview_conversion_copies_the_pixels() -> None:
    source = frame(200)
    image = preview_to_image(source)
    source.image.flags.writeable = True
    source.image[:] = 0  # 原帧之后被改写也不影响已转换的图像
    assert image.width() == 20 and image.height() == 30
    assert image.pixelColor(3, 3).red() == 200


def test_the_bridge_keeps_only_the_newest_preview(qapp: QApplication) -> None:
    class Source:
        def add_preview_listener(self, _l: object) -> None: ...
        def remove_preview_listener(self, _l: object) -> None: ...
        def add_video_listener(self, _l: object) -> None: ...
        def remove_video_listener(self, _l: object) -> None: ...

    bridge = EngineBridge(video=Source())
    shown: list[int] = []
    threads: list[int] = []

    def on_frame(image: QImage) -> None:
        shown.append(image.pixelColor(0, 0).red())
        threads.append(threading.get_ident())

    bridge.previewFrame.connect(on_frame)

    def produce() -> None:
        for value in range(1, 101):
            bridge.handle_preview(frame(value))

    worker = threading.Thread(target=produce)
    worker.start()
    worker.join()  # 主线程没有处理事件：100 帧都已送到
    assert bridge.preview_received == 100
    qapp.processEvents()
    assert shown == [100] and threads == [threading.get_ident()]  # 只转发最新一帧
    assert bridge.preview_delivered == 1
    bridge.close()
    bridge.handle_preview(frame(7))  # 关闭后丢弃
    qapp.processEvents()
    assert shown == [100]
