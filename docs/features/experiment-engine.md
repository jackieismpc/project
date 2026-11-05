# 试验引擎（experiment-engine）

对应阶段：P6。代码在 `src/gelinspect/services/`：`experiment_engine.py`（门面、启动恢复）、`engine_run.py`（一次运行）、`engine_workers.py`（算法线程、事件分发）、`engine_types.py`（配置、事件、异常）、`config.py`（YAML 和后端加载）；组装根是 `src/gelinspect/app_context.py`。

## 目的

把设备、视频管线、算法和存储串成一次完整的试验，面向界面（P9）和后续阶段的开发人员。全系统的并发和一致性问题都集中在这里解决：

- 10 排独立按截止时间调度，算法和回调不能拖慢翻转。
- 任何时候内存、数据库和界面看到的状态不分叉：试验状态、单管结果、媒体记录和审计事件同一事务提交。
- 任何停止或故障都不留悬挂的录制段、pin 和翻转状态。
- 单管失败转待复核，**绝不默认判阴性**。

纯 Python + `threading`，不依赖 Qt。判定结论（λc 等）留给 P10，这里只到单管和试验状态。

## 流程

### 用法

```python
from gelinspect.app_context import build_app_context

context = build_app_context(on_event=bridge)  # 默认配置指向 simulation/，并执行启动恢复
context.engine.start("EXP-001", user)  # 立即返回，试验必须已通过试剂校验（就绪）
context.engine.stop(user, "换批")  # 立即返回，engine.wait() 等待收尾
context.close()
```

命令都不阻塞调用线程，可以从 Qt 主线程直接调用。

| 方法 | 作用 | 权限 |
|---|---|---|
| `start(experiment_id, user)` | 启动就绪的试验 | `experiment_run` |
| `stop(user, reason)` | 停止运行中的试验 | `experiment_stop` |
| `resume(user)` | 从异常恢复：未读的排继续，读数失败的排重读，分析失败的排重试 | `experiment_run` |
| `retry_analysis(user)` | 只重试分析失败的排，不翻转 | `experiment_run` |
| `recover_interrupted()` | 启动时把停留在运行中状态的试验标记为异常 | 系统调用 |
| `stop_faulted(id, user, reason)` | 停止没有在运行的异常试验（启动恢复标记的） | `experiment_stop` |
| `snapshot()` | 只读快照，任意线程可读 | — |
| `shutdown(timeout)` | 软件退出：还在进行的试验转异常，释放全部线程 | 系统调用 |

### 一次运行

```text
启动前检查（失败则仍为就绪，可重新启动）
  连接设备 → 通信、故障、舱门、温度 → 加载算法 → 启动视频并等到出画 → 滑台送入
START：incubating，所有排的截止时间 = 现在 + 孵育时长（默认 60 分钟）
每排到时（只处理有样品的排，排号升序）：
  ROW_DUE：reading
  select_row 对准 → 等新帧（等不到不翻转）
  开始录制段 → 标记翻转开始（pin）→ flip_row → 抓翻转前后各 n 帧 → restore_row → 结束录制段
  ROW_CAPTURED：analyzing，媒体记录同一事务入库
  analyze_row 提交到算法线程，主循环继续调度其他排
结果到达：映射 → 结果、审计、状态同一事务提交
  还有未读的排：ROW_ANALYZED → incubating
  全部读完：有待复核 all_analyzed_needs_review，否则 all_analyzed_clean
  全部读完但有排分析失败：FAULT，等待重试
跑完或停止：退出滑台，停算法线程，停视频（只停自己启动的管线），断开设备
```

某排分析期间下一排到时，状态机走 `analyzing → reading → analyzing`，算法线程排队处理。倒计时用截止时间计算，翻转阻塞期间不会漂移，读数晚于到时超过 `row_late_warn_s`（默认 120 秒）时告警并写审计，审计里记录每排实际孵育时长。

翻转前多等一帧再取帧：跨过 `select_row` 时刻的那一帧可能拍的是上一排。

### 单管结果映射

`TubeResult.from_analysis` 按 `GelStateMapping` 映射。下面的情况都转待复核：`low_quality`、`failed`、`tube_not_found`、`state` 为空、部分凝固（默认映射）。算法结果缺管号或重复管号时引擎补一条 `failed` 结果并告警（`analysis_contract`），多余管号只告警，都不会变成阴性。

### 算法线程

`load`、`analyze_row`、`close` 都在 `engine-analysis` 线程上串行执行。

- **超时**（默认 2 秒，真实时间，从算法真正开始算，不含排队）：写 `analysis_timeout` 审计并告警一次，继续等待，不中断。
- **整体故障**（`AnalyzerError` 或算法抛出的其他异常）：该排转 `analysis_failed`，写审计。**其他排继续按时读数**：翻转不可逆，错过的读数补不回来，而分析可以重做。全部排都读完后试验转 `faulted`，`retry_analysis` 从磁盘读回原图、核对 SHA-256（被改动的图不给算法）后重新分析，不再翻转。内存里不保留失败排的帧（12 MP 一排 216 MB）。
- **算法卡死**：停止不等它，线程在限时后告警（`worker_stuck`），算法返回后自行结束。用守护线程而不是线程池，进程能退出。

### 停止和故障

| 触发 | 阶段 | 行为 | 试验状态 |
|---|---|---|---|
| 用户停止 | 启动前检查 | 取消启动，已送入的滑台退回 | 仍为 `ready`（还没孵育，可重新启动） |
| | 孵育 | 立即停止 | `stopped` |
| | 对准、等帧 | 不翻转 | `stopped` |
| | 翻转中 | 等设备动作返回（无法打断），不抓帧，复位，封存录制段 | `stopped` |
| | 抓帧中 | 取消等待，复位，封存录制段 | `stopped` |
| | 复位中 | 等复位完成，已抓的图入库，不分析 | `stopped` |
| | 分析中 | 立即停止，算法结果丢弃 | `stopped` |
| | 异常 | 再试一次复位 | `stopped` |
| | 已跑完 | 告警 `stop_ignored`，状态不变 | 不变 |
| 设备故障 | 孵育或分析中（状态轮询发现） | 不再动作，结果留到恢复后写入 | `faulted` |
| | 翻转失败 | 该排 `read_failed`，仍尝试复位（可能走了一半），封存录制段 | `faulted` |
| | 复位失败 | 图像已抓到，照常分析；转异常，恢复或停止时再试复位 | `faulted` |
| 舱门打开 | 任意 | 告警；到时的排等舱门关闭后才读数 | 不变 |
| 视频断流 | 孵育 | 告警，等重连。断流期间到时的排**不翻转**。`video_down_grace_s`（默认 30 秒）内没恢复，或重连被放弃，转异常 | 不变或 `faulted` |
| | 读数中，图像齐全 | 照常分析，录像标记不完整（`interrupted`），审计注明 | 不变 |
| | 读数中，抓不到帧 | 该排 `read_failed`，复位，封存录制段 | `faulted` |
| 温度越限 | 任意 | 第一次越限告警；持续 `temperature_hold_s`（默认 60 秒设备时间）转异常；中途回到 37±1 ℃ 则清零。无法读取温度按越限处理 | 不变或 `faulted` |
| 写锁被占用 | 任意 | 等到 `storage_busy_timeout_s`，期间发 `storage_busy`，不算故障 | 不变 |
| 写库失败（含等写锁超时） | 任意 | 内存撤销，转异常；分析结果没写进去的排可重试。连异常都写不进去时运行中止，不再做无审计的动作，下次启动由恢复流程标记 | `faulted` 或库内旧状态 |
| 软件退出 | 运行中 | 转异常，原因写审计 | `faulted` |

每一行都写审计（`experiment_state_changed` 或 `engine_alert`），操作者是发起停止的用户或 `system`。转异常后倒计时继续走（试管还在孵育），恢复后已到时的排马上读数。`stop` 期间已经产生的录像和图像都随停止事务入库。

**恢复条件**：`resume` 先连接设备，核对通信、故障、舱门、温度，再试复位遗留的排，确认视频出画。任何一项不满足发 `resume_rejected`，试验保持异常。读数失败的排重读前由人确认：翻动过的凝胶状态可能已变，引擎不自动重读。`retry_analysis` 在还有其他异常原因时被拒绝。

### 启动恢复

`recover_interrupted`（`build_app_context` 会调用）查询库内停留在 `incubating`、`reading`、`analyzing` 的试验，走状态机转 `faulted`，原因写审计：上次运行没有正常结束，设备实际状态未知，某排可能仍处于翻转状态，需要人工检查。**不自动继续**，也不碰设备。重启后这些试验只能 `stop_faulted`，`resume` 只对当前进程的运行有效（内存里的调度已经没了）。

### 工作单元

`interfaces/storage.py` 的 `UnitOfWork`，`Database.unit_of_work()` 实现：

```python
with storage.unit_of_work() as uow:
    uow.experiments.update_state(experiment_id, state, expected=previous)
    uow.experiments.save_results(experiment_id, results)
    uow.media.add(media)
    uow.audit.append_many(events)
```

- 以 `BEGIN IMMEDIATE` 开始，进入时就持有写锁，事务内先读后写（审计链头）不会读到过期快照。其他线程最多等 `busy_timeout`（5 秒）。
- 仓储绑定到同一个会话，只 `flush` 不提交。写入异常被调用方吞掉时事务不提交，退出后再用这些仓储会报错。
- 等写锁超时抛 `StorageBusyError`（`StorageError` 的子类）：事务没开始，代码块没执行，可以原样重试。提交失败抛 `StorageError`。
- 引擎写库时等写锁等到 `storage_busy_timeout_s`（默认 60 秒）。SQLite 的忙等待不排队，界面登录审计这类短事务可能连续抢到锁，单次 `busy_timeout` 超时不代表库坏了，试验不能因此中止。等待期间发一次 `storage_busy` 事件，不翻转（到时事务没提交）。温度采样不等，只试一次，丢一条只告警。
- 不要在事务里调用设备、算法或等待。

文件先落盘，事务后提交。崩溃时最多留下没有入库的孤儿文件（不覆盖、不影响后续），库里不会有指向不存在文件的记录。

### 事件

`on_event` 或 `add_listener` 注册的监听者收到 `EngineEvent` 的子类，都在事务提交之后发出：

| 事件 | 字段 | 说明 |
|---|---|---|
| `StateChanged` | `previous`、`current`、`trigger`、`actor`、`reason` | 状态变化 |
| `WellUpdated` | `row_index`、`tube_no`、`status`、`interpretation` | 孔更新 |
| `RowCountdown` | `row_index`、`phase`、`remaining_s` | 排倒计时，设备时间 |
| `TemperatureChanged` | `celsius`、`in_range` | 温度 |
| `EngineAlert` | `level`、`code`、`message`、`row_index` | 告警，码见 `AlertCode` |

先把事件放进分发队列，再更新 `snapshot()`：看到新状态的观察者，相关事件都已经入队，随后一定会收到；`fault_reason` 在转异常的同时就能从快照读到。监听者在独立的 `engine-events` 线程上调用：抛异常只记日志，卡死也不影响翻转时序。引擎线程卡在设备动作上时事件要等动作返回才发。P9 用 Qt 信号桥接。

### 配置

`services/config.py` 读 YAML。不指定文件时用 `DEFAULT_CONFIG`（内容同 `simulation/config.example.yaml`）。**配置文件是完整配置，不与默认值合并**：换成真实后端时漏写的项不会沿用模拟后端或 60 倍加速。相对路径相对仓库根目录，与工作目录无关；数据目录默认 `data/`。后端用 `包.模块:类名` 加载，导入失败时报错，不回退到模拟实现；`simulation` 缺失时错误信息说明要恢复目录还是改配置。

| 字段（`engine`） | 默认 | 时间基准 |
|---|---|---|
| `incubation_minutes` | 60 | 设备时间 |
| `time_scale` | 1（默认配置 60） | |
| `frames_per_side` | 3（不小于 3） | |
| `temperature_target_c`、`temperature_tolerance_c` | 37、1 | |
| `temperature_hold_s`、`temperature_log_interval_s` | 60、30 | 设备时间 |
| `row_late_warn_s` | 120 | 设备时间 |
| `status_poll_s`、`countdown_interval_s`、`tick_s` | 1、1、0.05 | 真实时间 |
| `video_ready_timeout_s`、`preflip_frame_timeout_s` | 30、5 | 真实时间 |
| `video_down_grace_s`、`worker_stop_timeout_s` | 30、5 | 真实时间 |
| `storage_busy_timeout_s` | 60 | 真实时间 |
| `tray_out_on_finish` | true | |

算法时限在 `analysis.timeout_ms`（默认 2000）。三态映射在 `interpretation`，例如 `{partially_gelled: positive}`。

**两个时钟**：`clock` 是真实时钟，与设备、视频帧同一基准，翻转标记和抓帧靠它；`schedule_clock` 是调度时钟，读数是设备时间，孵育和温度持续时间用它，默认按 `time_scale` 加速 `clock`。不能只用一个加速时钟：翻转标记的时间必须和帧时间可比，算法耗时也不能被加速。

## 权限

见上方命令表。引擎在门面上用 `has_permission` 检查，停用账号一律拒绝，没有权限抛 `EnginePermissionError`。引擎自己的动作在审计中记为 `system`，角色为空。

## 数据

| 写入 | 时机 | 同一事务内 |
|---|---|---|
| `experiments.state` | 每次转换 | 对应的审计 |
| `media_files` | 抓帧完成、停止或转异常 | `row_captured` 转换和审计 |
| `tube_results` | 算法结果到达 | `row_analyzed` 审计和可能的状态转换 |
| `temperature_log` | 每 `temperature_log_interval_s` | 单独一个事务，失败只告警 |

审计事件：`experiment_state_changed`（含原因和操作者）、`row_analyzed`（模型版本、配置版本、耗时、每管状态和判读）、`analysis_timeout`、`engine_alert`（`[告警码] 说明`，P6 新增）。特征在 `tube_results.features`。文件路径同视频管线：`<data_dir>/videos/<试验>/row_XX.mp4`、`<data_dir>/images/<试验>/row_XX/`，数据库 `<data_dir>/gelinspect.db`。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/services/test_experiment_engine.py` | 端到端：17 管到待复核（与脚本一致）、80 管到完成、文件和哈希、审计链；测量模式证明翻转前的帧拍的是当前排；流程顺序和线程归属；算法不阻塞主循环；超时；单管失败和缺管转待复核；启动条件 |
| `test_experiment_engine_faults.py` | `AnalyzerError` 与重试（不再翻转、用磁盘原图、改过的图被拒）；断流的各阶段；翻转和复位失败；设备故障；舱门；温度（短暂、持续、无法读取、分析中）；回调抛异常和卡死；写库失败；启动恢复；软件退出 |
| `test_experiment_engine_stop.py` | 上表每个阶段的停止，算法卡死时的停止 |
| `test_experiment_engine_concurrency.py` | 停止与到时竞争（12 个随机种子）、连续运行线程不增长、外部启动的管线不被关闭、并发写审计、写锁被占用时等待和等待超限、真实进程在结果事务提交前后被杀 |
| `tests/infrastructure/db/test_unit_of_work.py` | 回滚、每一步失败、提交失败、吞掉异常不提交、写锁、并发下的哈希链、真实进程在事务各点被杀 |
| `tests/services/test_config.py` | 默认配置、不合并、路径、错误信息、`simulation` 缺失、组装和一键运行 |

测试用门闩和手动调度时钟控制时序，不靠真实长等待；`tests/services/conftest.py` 的 `thread_baseline` 断言线程回到基线。

需要人工验证：真实设备的翻转和 `select_row` 语义、复位幂等；真实相机；Windows 上的文件锁；断电（测试只杀进程，`synchronous=NORMAL` 下断电可能丢最后几个已提交的事务，但数据库和哈希链保持一致）。

## 已知限制

- **重启后不能续跑**：进程退出后调度和原图元数据都不在内存里，试验只能停止。
- **掩码没有存档**：`TubeAnalysis.masks` 目前丢弃，P9 的掩码叠加需要决定如何落盘。
- **低置信度不转待复核**：`GelStateMapping` 只看状态和三态，置信度只存储。
- **翻转动作不可打断**：`DeviceController` 没有取消接口，停止要等当前动作返回。
- **同时只运行一个试验**：设备只有一台。
