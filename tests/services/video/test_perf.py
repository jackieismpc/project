"""压测：4000×3000、10 fps、约 20 秒，同时预览和录制，周期性抓拍。

默认跳过（约 40 秒），设置 ``GELINSPECT_PERF_VIDEO=1`` 或 ``-m video_perf`` 运行，用 ``-s`` 查看实测数字。
模拟帧复用同一块底图：每帧 ``copy`` 一份（约 36 MB，几毫秒）再把序号写进左上角像素，
这样每帧占用独立内存，RSS 的变化才反映真实情况。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from gelinspect.interfaces.video import VideoConfig, VideoFrame, VideoInfo
from gelinspect.services.video import (
    BufferConfig,
    PreviewConfig,
    QualityConfig,
    SnapshotConfig,
    SnapshotResult,
    VideoPipeline,
    VideoPipelineConfig,
)
from tests.services.video.helpers import frame_number, stamped_image

WIDTH, HEIGHT, FPS, DURATION_S = 4000, 3000, 10.0, 20.0
FRAME_BYTES = WIDTH * HEIGHT * 3


class RealtimeSource:
    """按真实时间以 FPS 出帧的 12MP 假源。"""

    def __init__(self) -> None:
        rng = np.random.default_rng(1)
        tile = rng.integers(60, 200, (30, 40, 3), dtype=np.uint8)
        self._base = np.kron(tile, np.ones((100, 100, 1), np.uint8))  # 30×40 的块拼成 3000×4000
        self._seq = 0
        self._due = 0.0

    def open(self, config: VideoConfig) -> VideoInfo:
        self._due = time.monotonic()
        return VideoInfo(width=WIDTH, height=HEIGHT, fps=FPS, is_live=True)

    def read(self) -> VideoFrame | None:
        wait = self._due - time.monotonic()
        if wait > 0.5:
            time.sleep(0.5)
            return None
        if wait > 0:
            time.sleep(wait)
        self._due = max(self._due + 1 / FPS, time.monotonic() - 1.0)
        image = stamped_image(self._base, self._seq)
        frame = VideoFrame(
            image=image,
            sequence=self._seq,
            captured_at=datetime.now(UTC),
            monotonic_s=time.monotonic(),
        )
        self._seq += 1
        return frame

    def close(self) -> None:
        pass


def rss_mb() -> float:
    """当前进程的常驻内存（MB）：Linux 读 /proc，macOS 用 proc_pidinfo，其他平台退回峰值。"""
    if sys.platform.startswith("linux"):
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1e6
    if sys.platform == "darwin":
        import ctypes

        info = (ctypes.c_uint64 * 12)()  # proc_taskinfo：前两项为虚拟和常驻字节数
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        if libc.proc_pidinfo(os.getpid(), 4, 0, info, ctypes.sizeof(info)) > 0:
            return float(info[1]) / 1e6
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def check_snapshot(snap: SnapshotResult) -> None:
    """抓拍帧不丢：翻转前后各 3 帧，序号连续，翻转前后的时间关系正确，文件已落盘。"""
    for frames in (snap.frames_before, snap.frames_after):
        numbers = [frame_number(f.image) for f in frames]
        assert numbers == list(range(numbers[0], numbers[0] + 3))
    assert snap.frames_before[-1].captured_at < snap.flip_started_at
    assert snap.frames_after[0].captured_at >= snap.flip_completed_at + timedelta(seconds=0.3)
    assert all(f.path.exists() for f in snap.files)


@pytest.mark.perf
@pytest.mark.video_perf
def test_4000x3000_10fps_preview_record_and_snapshots(tmp_path: Path) -> None:
    config = VideoPipelineConfig(
        data_dir=tmp_path,
        source=VideoConfig(device="perf"),
        buffer=BufferConfig(max_bytes=FRAME_BYTES * 14),
        preview=PreviewConfig(max_fps=15, long_side=960),
        quality=QualityConfig(
            watchdog_timeout_s=3.0, frozen_samples=10**6
        ),  # 假帧画面相同，不测卡帧
        snapshot=SnapshotConfig(settle_s=0.3, wait_timeout_s=10.0),
    )
    preview_shapes: set[tuple[int, ...]] = set()  # 只记形状，不持有预览图
    pipeline = VideoPipeline(
        RealtimeSource(), config, on_preview=lambda p: preview_shapes.add(p.image.shape)
    )
    pipeline.start()
    pipeline.start_segment("EXP-PERF", 1)

    rss: list[tuple[float, float]] = []
    peak_buffer = 0
    stop = threading.Event()

    def sampler() -> None:
        nonlocal peak_buffer
        while not stop.is_set():
            rss.append((time.monotonic() - t0, rss_mb()))
            peak_buffer = max(peak_buffer, pipeline.stats().buffer.bytes_used)
            stop.wait(1.0)

    t0 = time.monotonic()
    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    snapshots: list[int] = []
    try:
        next_snap = 3.0
        while (elapsed := time.monotonic() - t0) < DURATION_S:
            if elapsed >= next_snap:
                row = len(snapshots) + 1
                start = datetime.now(UTC)
                pipeline.mark_flip_start(start, row)
                time.sleep(1.0)  # 「翻转」期间视频继续出帧
                snap = pipeline.grab(
                    row, 3, experiment_id="EXP-PERF", flip_completed_at=datetime.now(UTC)
                )
                # 抓拍结果持有 6 帧（约 216 MB），验证完立即释放，和引擎用完即弃的用法一致
                check_snapshot(snap)
                snapshots.append(row)
                del snap
                next_snap += 4.0
            time.sleep(0.05)
        wall = time.monotonic() - t0
        stats = pipeline.stats()
        record = pipeline.stop_segment()
    finally:
        stop.set()
        thread.join(5)
        pipeline.stop()

    fps = stats.frames_captured / wall
    print("\n[perf] RSS(t, MB):", " ".join(f"{t:.0f}s={mb:.0f}" for t, mb in rss))
    steady = [mb for t, mb in rss if t >= 6.0]  # 缓冲填满之后
    growth = steady[-1] - steady[0]
    print(
        f"\n[perf] 采集 {stats.frames_captured} 帧 / {wall:.1f}s = {fps:.2f} fps；"
        f"预览送出 {stats.preview_delivered}（跳过 {stats.preview_skipped}，覆盖 {stats.preview_dropped}）；"
        f"录制 {record.frame_count} 帧、丢 {record.dropped_frames}、{record.width}×{record.height}、"
        f"{record.size_bytes / 1e6:.1f} MB；\n[perf] 缓冲峰值 {peak_buffer / 1e6:.0f} MB（上限 "
        f"{config.buffer.max_bytes / 1e6:.0f} MB）；RSS {steady[0]:.0f} → {steady[-1]:.0f} MB "
        f"（稳态增长 {growth:+.0f} MB，最大 {max(steady):.0f} MB）；抓拍 {len(snapshots)} 次；"
        f"驱动丢帧 {stats.quality.driver_dropped_frames}"
    )

    assert fps >= 0.9 * FPS, "采集帧率被拖慢"
    assert stats.quality.driver_dropped_frames == 0
    assert peak_buffer <= config.buffer.max_bytes
    assert stats.buffer.forced_evictions == 0
    assert growth < 300, "RSS 持续增长"
    assert len(snapshots) >= 4
    assert preview_shapes == {(720, 960, 3)}
    assert record.frame_count >= 0.5 * stats.frames_captured
    assert not record.interrupted
