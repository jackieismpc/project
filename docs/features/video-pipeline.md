# 视频管线（video-pipeline）

对应阶段：P5。代码在 `src/gelinspect/services/video/`（管线）和 `src/gelinspect/infrastructure/video/`（视频源）。

## 目的

给试验引擎提供“视频进、图像出”的一整套能力，面向开发人员和试验引擎（P6）：

- 从视频源持续取帧，放进有字节上限的环形缓冲，**采集线程永远不被消费者阻塞**。
- 实时预览（缩放后、不超过 15 fps）、按排分段录制 MP4、翻转前后抓帧存原图、图像和断流质量检查。
- 抓到的帧直接组装成 `RowAnalysisRequest` 交给 `GelAnalyzer`。

**本阶段不实现实时摄像头**（2026-11-04 用户决定）：支持读入视频文件和图片。`VideoSource` 接口、工厂和 `industrial_template.py` 保留，相机选型后只需新增一个视频源类，管线不用改。

## 流程

### 线程图

```text
                     video-capture（采集线程，1 个）
                     VideoSource.open/read/close 只在这条线程上调用
                              │ 写入
                              ▼
                     FrameRingBuffer（带锁，字节上限，pin 保护）
                              │ 采集线程对每个消费者调用 offer（非阻塞）
        ┌─────────────────────┼──────────────────────┐
        ▼                     ▼                      ▼
 video-preview          video-writer-rowNN      video-quality
 单槽 latest-wins        每个录制段 1 条          每 N 帧分析 + 看门狗
 ≤15 fps，缩放后回调     有界队列，满则丢帧计数    只计数，不阻塞
        │                     │                      │
        └──────────── 事件（VideoEvent）──────────────┘
                              ▼
                     video-events（事件分发线程，有界队列）→ 监听者

 抓帧：video-grab（任务线程，2 个）→ video-imgio（编码写盘，io_workers 个）
       两个线程池按需创建，pipeline.stop() 时关闭
```

所有线程都是守护线程，只用 `threading`，不依赖 Qt。`start`/`stop` 幂等，可反复启停；`stop` 对每个线程设有 `stop_timeout_s` 的 join 限时，超时发 `STOP_TIMEOUT` 事件并继续。预览回调在 `video-preview` 线程上调用，界面需要自己用 Qt 信号切回主线程。

### 一排的典型调用

```python
pipeline = VideoPipeline(source, config, on_preview=show)
pipeline.start()  # 打开视频源，启动线程
pipeline.start_segment("EXP-001", 1)  # 开始录制第 1 排
pipeline.mark_flip_start(flip_started_at, 1)  # 翻转开始：保护翻转前最近 n 帧
flip = device.flip_row(1)  # 设备翻转（阻塞）
snap = pipeline.grab(1, 3, experiment_id="EXP-001", flip_completed_at=flip.completed_at)
record = pipeline.stop_segment()  # 封存 MP4，得到 SHA-256
pipeline.stop()
```

- **抓帧规则**：翻转前取“时间早于翻转开始”的最近 n 帧；翻转后等到“翻转结束 + 稳定时间 `settle_s`”之后，取最早的 n 帧。不足 n 帧时等到超时，抛 `InsufficientFramesError`，**不会静默少帧**。
- **时间约定**：`VideoFrame.captured_at` 与 `FlipResult.started_at/completed_at` 使用同一个本机时钟。`captured_at` 是曝光时刻，不是处理完的时刻：抓帧只按时间判断帧在翻转前还是翻转后，时间戳晚于画面会把翻转中的帧当成翻转后的帧。
- **帧只读**：缓冲里的 `ndarray` 都设为不可写，`CapturedFrame.image` 直接引用，不复制。
- **断流**：`read` 抛 `VideoDisconnectedError`（或实时源连续 `stall_disconnect_s` 读不到帧）后，采集线程按指数退避重连。进行中的录制段被封存为 `interrupted=True`（文件可播放、有哈希），**重连后不会自动续录**，由调用方决定是否重录该排；重连期间看门狗暂停。
- **重新读回原图**：`load_saved_frames(snapshot.files)` 从磁盘读回一次抓帧并核对 SHA-256，试验引擎重试分析时使用，内存里不必保留失败排的帧。
- **文件回放结束**：`read` 返回 `None` 且 `is_live=False` 时，状态变为 `ENDED`，发 `END_OF_STREAM`，缓冲和抓帧仍可用。

### 内存策略

| 机制 | 行为 |
|---|---|
| 字节上限 `buffer.max_bytes`（默认 1 GiB） | 超出时淘汰最旧的**未受保护**帧。按 4000×3000（36 MB/帧）约 28 帧 |
| pin 保护 | `mark_flip_start` 保护翻转前最近 n 帧；`grab` 保护翻转后的等待窗口。其余帧照常淘汰 |
| pin 硬上限 `pin_hard_limit_bytes`（默认 1.5 × 上限） | 受保护帧也超过它时发 `PIN_OVERFLOW`（ERROR）并淘汰最旧的受保护帧，内存绝不无限增长 |
| 预览 | 单槽，慢消费者只会覆盖旧帧（`preview_dropped`），不丢抓拍帧 |
| 录制 | 队列同时限帧数和字节数，满了丢录制帧并计数（`dropped_frames`），不影响缓冲和抓帧 |

### 三种输入方式

| 方式 | 类 / 函数 | 用途 |
|---|---|---|
| 视频文件 | `FileVideoSource`（别名 `file`） | 读入 MP4 等视频文件当帧流。默认按文件帧率限速，`loop` 可循环，`speed` 倍速 |
| 图片文件夹当帧流 | `ImageSequenceSource`（别名 `images`） | 把文件夹里的 JPG/PNG 按自然排序、按 `fps`（默认 10）播放，`open` 会校验全部图片 |
| 直接读一排图片 | `load_row_capture(directory)` | 不经过管线，读翻转前后的图片，返回可直接组装 `RowAnalysisRequest` 的 `RowCapture` |

前两种实现 `VideoSource`，播完后 `read` 返回 `None`（`is_live=False`），通过 `VideoPipeline` 使用，因此可以走录制、抓帧等全部流程。视频源由 `create_video_source("file" | "images" | "包.模块:类名", options)` 按配置创建；类路径无法导入或没有实现 `VideoSource` 时抛 `VideoSourceConfigError`，**不会回退到模拟实现**。

### 图片目录约定（`load_row_capture`）

```text
<一排目录>/
├── before/        翻转前，至少 3 张 .png / .jpg / .jpeg
│   ├── 1.png
│   └── 2.png ...
└── after/         翻转后，至少 3 张
    └── ...
```

- 文件按自然排序（`img2` 在 `img10` 之前），扩展名不区分大小写。
- 每组至少 3 张（算法接口要求 n≥3），所有图片分辨率必须一致，否则抛 `ImageLoadError` 并指出文件。损坏、不存在的目录同样是明确的错误。
- 照片没有可靠的采集时间：`captured_at` 按“最早的文件修改时间 + 序号 × 0.1 秒”合成，翻转后第一张比翻转前最后一张晚 10 秒，保证满足 `RowAnalysisRequest` 的时间顺序要求。`source_path` 指向原文件。
- 也接受 `SnapshotGrabber` 落盘的平铺格式（`row_01/before_01.png`、`after_01.png`），因此抓拍过的一排可以直接重新分析。
- 路径可以含中文，见下文。

### 中文（非 ASCII）路径

| 操作 | 做法 |
|---|---|
| 读写图片 | `np.fromfile` + `cv2.imdecode` / `cv2.imencode` + `ndarray.tofile`，不用 `cv2.imread/imwrite`。抓帧落盘用 Python 文件 I/O，同样安全 |
| 读视频文件 | Windows 下 `cv2.VideoCapture` 打不开含中文的路径：把文件硬链接（同卷）或复制（跨卷）到纯 ASCII 临时目录再打开，关闭时删除。大文件跨卷复制会慢 |
| 写 MP4 | Windows 下 `cv2.VideoWriter` 同样不行：编码器先写到纯 ASCII 临时目录，封存时 `shutil.move` 到 `.part.mp4`，之后照常改名并算哈希 |

纯 ASCII 临时目录依次尝试系统临时目录、`%PUBLIC%`、`%PROGRAMDATA%`、系统盘根目录（系统临时目录常在含中文用户名的目录下）。全都不可用时抛明确的错误，提示把数据目录改为纯英文路径。macOS 和 Linux 不触发此流程。该分支在 macOS 上通过替换判断函数覆盖测试，**Windows 实机尚未验证**。

### 工业相机接入

1. 在 `infrastructure/video/industrial_template.py` 的基础上新建类，无参构造，构造时不连接硬件。
2. `open` 内部才导入厂商 SDK（保证未装 SDK 的机器仍可导入）；用 `VideoConfig.device`（序列号）打开，设置分辨率、帧率、曝光，回读实际参数，`VideoInfo.is_live=True`。
3. `read` 带超时取帧：超时返回 `None`；掉线抛 `VideoDisconnectedError`；把 Bayer 等格式转为 H×W×3、uint8、BGR，复制出 SDK 缓冲并归还；`captured_at` 用本机时钟，在 SDK 交出缓冲时立刻取（颜色转换和复制之前），SDK 自身的延迟要小于 `settle_s`；`sequence` 递增（跳号即驱动丢帧）。
4. `open`、`read`、`close` 都在采集线程上调用，SDK 不需要线程安全。
5. 配置 `video.source` 指向新类，并用 4000×3000 重跑压测（下文）。

## 权限

视频管线是内部服务，没有独立的用户权限。权限在调用方：试验引擎（P6）按 `domain.permissions` 控制谁能启动试验；回放和图像显示页（P9）按角色控制可见范围。`experiment_id` 和排号在拼接路径前都经过校验（只允许字母、数字、`_`、`.`、`-`，首字符为字母或数字，不含 `..`，排号 1–10），并检查解析后的路径仍在数据目录内（防符号链接逃逸）。

## 数据

### 文件

| 内容 | 路径 | 说明 |
|---|---|---|
| 录制 | `<data_dir>/videos/<试验>/row_XX.mp4` | 先写 `row_XX.part.mp4`，release、fsync 后改名，再计算 SHA-256。已有文件绝不覆盖，重录得到 `row_XX_02.mp4` |
| 抓拍原图 | `<data_dir>/images/<试验>/row_XX/before_NN.png`、`after_NN.png` | 默认无损 PNG；先写 `.part`、fsync、改名，同时算 SHA-256；已有目录不覆盖（`row_XX_02/`） |

`.part.mp4` 保留 `.mp4` 结尾，因为 OpenCV 按扩展名选择封装格式，`.mp4.part` 打不开。

### 入库

管线不访问数据库。引擎在自己的线程里拿到 `SegmentRecord` / `SnapshotResult` 后，用 `media.segment_to_media_file` / `snapshot_to_media_files` 转成 `MediaFile`，再交给 `MediaRepository.add`。`SegmentRecord` 带 `sha256`、`frame_count`、`dropped_frames`、`source_gap_frames`、`interrupted`、`interrupt_reason`。

### 配置项（`VideoPipelineConfig`，全部有默认值，可由 YAML 字典解析，未知字段会被拒绝）

| 分组 | 字段（默认值） |
|---|---|
| 顶层 | `data_dir`、`source`（`VideoConfig`：`device`、`width`、`height`、`fps`、`read_timeout_ms`）、`open_timeout_s`（30）、`stop_timeout_s`（5） |
| `buffer` | `max_bytes`（1 GiB）、`pin_hard_limit_bytes`（1.5 × `max_bytes`） |
| `preview` | `max_fps`（15）、`long_side`（960） |
| `recorder` | `long_side`（1920）、`fourcc`（`mp4v`）、`default_fps`（10）、`queue_max_frames`（60）、`queue_max_bytes`（512 MiB）、`finish_timeout_s`（60） |
| `snapshot` | `frames_per_side`（3）、`settle_s`（1.0）、`wait_timeout_s`（10）、`image_format`（`png`）、`png_compression`（3）、`jpeg_quality`（95）、`io_workers`（4） |
| `quality` | `enabled`、`check_every_n_frames`（10）、`analysis_long_side`（160）、`black_mean_max`（8）、`overexposed_level`（250）、`overexposed_fraction`（0.6）、`frozen_diff_max`（0.02）、`frozen_samples`（3）、`expected_fps`、`low_fps_ratio`（0.5）、`fps_window_s`（5）、`watchdog_timeout_s`（3）、`tick_interval_s`（0.25） |
| `reconnect` | `enabled`、`initial_delay_s`（0.5）、`max_delay_s`（10）、`factor`（2）、`max_attempts`（无限）、`stall_disconnect_s`（10） |

### 事件码（`VideoEventCode`）

事件经异步总线投递，`active=False` 表示该状况已恢复。需要写审计日志时由引擎订阅 `on_event` 转存。

**发布顺序**：先把事件记入历史，再发布状态。看到 `FAILED`、`ENDED`、重连计数增加或录制段结束的观察者，都能在 `recent_events()` 里读到原因。反过来不成立：监听者收到事件时读到的状态可能还是旧值，要以事件本身为准。

| 事件码 | 级别 | 含义 |
|---|---|---|
| `black_frame` | ERROR | 黑屏：缩小图平均亮度低于 `black_mean_max` |
| `overexposed` | WARNING | 过曝：饱和像素占比超过阈值（正常的亮背光不会触发） |
| `frozen_frame` | ERROR | 卡帧：连续 `frozen_samples` 次采样几乎没有变化 |
| `low_fps` | WARNING | 实测帧率低于期望的 `low_fps_ratio` |
| `frames_dropped` | WARNING | 帧序号跳号，驱动层掉帧 |
| `stream_stalled` | ERROR | 看门狗：超过 `watchdog_timeout_s` 没有新帧 |
| `disconnected` | ERROR | 视频源断流 |
| `reconnecting` | WARNING | 正在重连 |
| `reconnected` | INFO | 重连成功 |
| `reconnect_failed` | ERROR | 采集不会自行恢复：重连达到最大次数、重连被关闭，或采集线程异常退出 |
| `end_of_stream` | INFO | 文件回放结束 |
| `pin_overflow` | ERROR | 受保护帧超过硬上限，已强制淘汰，抓拍帧可能丢失 |
| `consumer_error` | ERROR | 预览/录制/质量检查消费者抛出异常（线程不会被杀死） |
| `callback_error` | ERROR | 预览回调或事件监听者抛出异常 |
| `recording_dropped` | WARNING | 录制队列已满，丢了录制帧 |
| `segment_interrupted` | ERROR | 录制段被异常终止，文件已封存但不完整 |
| `stop_timeout` | ERROR | 停止时某个线程没有在限时内退出 |

## 测试

自动化测试（视频相关 120 余项，另有 1 项压测默认跳过）：

| 文件 | 覆盖 |
|---|---|
| `tests/services/video/test_ring_buffer.py` | 字节上限、先淘汰最旧、时间查询、pin 保护、pin 硬上限告警与恢复、等待与取消、时钟回退、并发读写 |
| `test_preview.py` | 限帧率与缩放、latest-wins（慢回调只保留最新帧）、回调异常不杀线程、可重启 |
| `test_recorder.py` | 真写 MP4 读回逐帧核对、`.part` 存在与改名、SHA-256、偶数分辨率与缩放、不覆盖、writer 打不开、无帧不生成空文件、写盘失败清理、路径穿越与符号链接逃逸、慢写盘丢帧不阻塞 `offer`、驱动跳号计数、中断封存、中文目录（含模拟 Windows 的 ASCII 临时目录流程） |
| `test_snapshot.py` | 用带已知时间戳和帧号的假源核对帧归属：翻转前严格早于翻转开始、翻转后晚于翻转结束 + settle；settle 参数；等待未来的帧；不足 n 帧抛异常；pin 保护翻转前帧；PNG 无损与哈希；异步抓帧；停止时取消等待 |
| `test_quality.py` | 黑屏、过曝与正常亮背光的区别、卡帧、实测帧率与低帧率、驱动掉帧、看门狗断流与恢复、重连期间暂停、分析抽样开销 |
| `test_pipeline.py` | 启停幂等且采集线程唯一、20 次启停线程不增长、慢预览/慢录制/慢监听者不阻塞采集、消费者与回调异常不杀线程、卡死的 `read` 不拖死 `stop`、断流重连与录制段处理、退避与次数上限、关闭重连、状态在事件之后发布、看门狗转断流、文件结束、缓冲不超限、帧只读 |
| `tests/infrastructure/video/test_sources.py` | `FileVideoSource`、`ImageSequenceSource`、`load_row_capture`（`before/` `after/`、平铺格式、每组 ≥3 张）、损坏文件与分辨率不一致的明确错误、中文路径读写图片与视频、模拟 Windows 的 ASCII 副本流程、工厂 |
| `tests/services/video/test_integration_sim.py` | `SimVideoSource` + `SimDevice` 翻转、录制、抓帧 → `MockAnalyzer`，`check_row_result` 无违规，并入库；图片文件夹 → `load_row_capture` → `MockAnalyzer` |
| `tests/services/video/test_perf.py` | 4000×3000 压测，默认跳过 |

### 压测

```bash
GELINSPECT_PERF_VIDEO=1 uv run pytest -m video_perf -v -s
```

4000×3000、10 fps、约 20 秒，同时预览和录制，每 4 秒做一次抓拍，缓冲上限设为 14 帧（504 MB）。2026-11-05 在 macOS（Apple Silicon，Python 3.12）上的实测：

| 指标 | 结果 |
|---|---|
| 采集帧率 | 210 帧 / 20.9 秒 = 10.04 fps（目标 10），驱动丢帧 0 |
| 内存 | 缓冲峰值 504 MB（等于上限，未超）；缓冲填满后 RSS 757 → 760 MB，稳态增长 +3 MB，最大 760 MB |
| 抓拍 | 5 次，每次 6 帧全部落盘，翻转前后时间和文件校验通过；`forced_evictions=0`（受保护的帧没有被强制淘汰） |
| 预览 | 送出 209 帧（受 15 fps 上限限制未触发跳过），覆盖 0 |
| 录制 | 210 帧、丢 0，缩到 1920×1440 |

注意：压测的假帧复用同一块底图，所以 MP4 只有 0.9 MB，不代表真实画面的码率和磁盘写入压力。

需要人工验证：Windows 实机运行（中文路径流程、`mp4v` 编码器可用性）、真实相机的帧率与掉帧、真实画面下的 MP4 写盘速度。

## 已知限制

- **没有实时摄像头**：只有文件和图片输入，工业相机待选型后按模板实现。
- **Windows 未实机验证**：中文路径的 ASCII 临时目录流程只在 macOS 上通过替换判断函数验证过。
- **录制是有损缩小的**：为了写盘可行，录制缩到长边 1920；原图只存在于抓拍的 PNG 中。编码默认 `mp4v`（OpenCV 自带，无需额外编解码器），压缩效率不如 H.264。
- **断流后不自动续录**：被中断的段只封存，是否重录由引擎决定。
- **照片的时间是合成的**：`load_row_capture` 的 `captured_at` 不是真实拍摄时间。
- **12 MP 的内存**：每帧 36 MB，默认 1 GiB 缓冲约 28 帧（约 2.8 秒 @ 10 fps）。抓拍结果持有 `2n` 帧引用，用完应尽快释放。
- **单个视频源**：一个 `VideoPipeline` 对应一个视频源，不支持多相机。
- **不处理音频**。
