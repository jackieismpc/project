# 试验生命周期

## 目的

定义一个试验从创建到签署的全部状态、合法转换和前置条件，保证试剂不合格的试验不能启动、有待复核试管的试验不能签署。本文对应 P1 的契约，P6（试验引擎）和 P8–P11（界面、判定、报告）依赖它。

代码：`src/gelinspect/domain/state_machine.py`、`src/gelinspect/domain/entities.py`。运行期的调度、停止、故障和恢复由试验引擎驱动，见 [experiment-engine.md](experiment-engine.md)。

## 流程

### 状态

| 状态 | 枚举值 | 含义 |
|---|---|---|
| 草稿 | `draft` | 可编辑模板、样品和管位 |
| 试剂校验 | `reagent_check` | 校验试剂是否备案且在有效期内 |
| 试剂锁定 | `reagent_locked` | 试剂过期或未备案，不能启动 |
| 就绪 | `ready` | 可以启动 |
| 孵育 | `incubating` | 等待下一排到时 |
| 读数 | `reading` | 翻转一排并抓帧 |
| 分析 | `analyzing` | 算法处理一排 |
| 待复核 | `pending_review` | 全部排已分析，有试管等待人工复核 |
| 完成 | `completed` | 全部试管都有明确结论，可以签署 |
| 已签署 | `signed` | 终态 |
| 已停止 | `stopped` | 终态 |
| 异常 | `faulted` | 运行中发生故障，可恢复或停止 |

一个试验有 10 排独立倒计时，所以孵育、读数、分析描述的是试验当前的活动。某排读数和分析时，其他排仍在孵育。

### 合法转换

```text
draft ──提交──→ reagent_check ──通过──→ ready ──启动──→ incubating
                  │     ↑                                 ↑   │ 到时
                不通过  重新校验                     还有排 │   ↓
                  ↓     │                                 │  reading
              reagent_locked                              │   │ 抓帧完成
                                                          │   ↓
                                          下一排已到时 ←── analyzing
                                          (→ reading)     │   │
                                              有待复核 ────┘   └──── 无待复核
                                                 ↓                     ↓
                                          pending_review ──复核完成──→ completed ──签署──→ signed
                                                 ↑                     │
                                                 └────重新复核──────────┘
```

| 当前状态 | 事件 | 下一状态 |
|---|---|---|
| `draft` | `submit_for_reagent_check` | `reagent_check` |
| `reagent_check` | `reagent_check_passed` | `ready` |
| `reagent_check` | `reagent_check_failed` | `reagent_locked` |
| `reagent_locked` | `retry_reagent_check` | `reagent_check` |
| `reagent_check`、`reagent_locked`、`ready` | `edit` | `draft` |
| `ready` | `start` | `incubating` |
| `incubating`、`analyzing` | `row_due` | `reading` |
| `reading` | `row_captured` | `analyzing` |
| `analyzing` | `row_analyzed` | `incubating` |
| `analyzing` | `all_analyzed_clean` | `completed` |
| `analyzing` | `all_analyzed_needs_review` | `pending_review` |
| `pending_review` | `review_completed` | `completed` |
| `completed` | `reopen_review` | `pending_review` |
| `completed` | `sign` | `signed` |
| `incubating`、`reading`、`analyzing` | `fault` | `faulted` |
| `faulted` | `resume_incubation` | `incubating` |
| `faulted` | `resume_reading` | `reading` |
| `faulted` | `resume_analysis` | `analyzing` |
| `draft`、`reagent_check`、`reagent_locked`、`ready`、`incubating`、`reading`、`analyzing`、`faulted` | `stop` | `stopped` |

其他组合都是非法转换，抛 `IllegalTransitionError`。`pending_review` 和 `completed` 不接受 `stop`：试验已经跑完，结论只能通过复核确定。

### 引擎如何使用这些转换

- `analyzing` 一直保持到所有在途的分析都有结局，所以「某排分析中、下一排到时」走 `analyzing → reading → analyzing`，全部结局到齐后才发 `row_analyzed` 或 `all_analyzed_*`。
- `record_result` 只能在 `analyzing` 调用。异常期间到达的结果留在内存，恢复（`resume_analysis`）后再写入。
- `fault` 的原因：设备、视频、温度、写库，以及「全部排读完但有排分析失败」。单排分析失败不会立刻转异常，其他排继续读数。
- 恢复时有待处理的分析就发 `resume_analysis`，否则发 `resume_incubation`；读数失败的排回到等待状态，再走 `row_due`。`resume_reading` 目前没有使用。
- 软件启动时，库内停留在 `incubating`、`reading`、`analyzing` 的试验走 `fault` 转异常，不自动继续。

### 前置条件

转换合法但前置条件不满足时，抛 `TransitionGuardError`，状态不变。

| 事件 | 前置条件 |
|---|---|
| `start` | 至少安排了一支试管 |
| `all_analyzed_clean` | 每支试管都有结果，且没有待复核的试管 |
| `all_analyzed_needs_review` | 每支试管都有结果，且至少有一支待复核 |
| `review_completed` | 每支试管都有结果，且没有待复核的试管 |
| `sign` | 同上，且必须通过 `Experiment.sign()` 记录签署人和时间 |

### 试剂校验

`check_reagents` 校验鲎试剂、内毒素、检测用水三种试剂，结果的 `event` 属性给出应发送的事件。以下情况都会锁定：未备案、已过期、有效期未知、缺少某种试剂。同一种试剂有多个批次时，只要有一批不合格就锁定。有效期当天仍然有效。

### 单管判读和复核

1. 算法返回 `TubeAnalysis` 后，`TubeResult.from_analysis` 按 `GelStateMapping` 得出判读。

   | 算法结果 | 默认判读 | 复核状态 |
   |---|---|---|
   | 完全凝固 | 阳性 | 无需复核 |
   | 未凝固 | 阴性 | 无需复核 |
   | 部分凝固 | 待复核 | 待复核 |
   | `state` 为空，或状态为 `low_quality`、`tube_not_found`、`failed` | 待复核 | 待复核 |

2. 三态的映射可以配置。无法判定固定为待复核，不开放配置。
3. 复核结论只能是阳性或阴性，可以重复复核，以最后一次为准。保留算法的自动判读。
4. `completed` 状态下要改结论，先发送 `reopen_review`。签署后不能再修改。

### 内置模板

| 模板 | 管数 | 布局 | `needs_confirmation` |
|---|---|---|---|
| 灵敏度复核 (2λ:1) | 17 | 1、0.5、0.25、0.125 各 4 管，加 1 支阴性对照（0.0） | `False` |
| 干扰试验 | 28 | 从 1 号管连续占位，用途和数值留空 | `True` |
| 复试试验 | 10 | 同上 | `True` |
| 半定量试验 | 16 | 同上 | `True` |
| 限度试验 | 8 | 同上 | `True` |

灵敏度复核的 1、0.5、0.25、0.125、0.0 写入 `spiked_endotoxin`（外加内毒素），依据是参考视频新试验页（约 49.5 秒）的列位置；稀释倍数留空。

推定模板的用途是 `unconfirmed`，不含药典规则。判定规则由 P10 负责。

## 权限

状态机本身不检查权限，由服务层在调用前用 `has_permission` 检查。权限矩阵见 [permissions.md](permissions.md)。

| 操作 | 所需权限 |
|---|---|
| 新建、编辑试验 | `experiment_create` |
| 录入、校验试剂 | `reagent_manage` |
| 启动、恢复异常 | `experiment_run` |
| 停止 | `experiment_stop` |
| 复核单管、重新打开复核 | `result_review` |
| 签署 | `experiment_sign` |

## 数据

| 实体 | 关键字段 | 存储 |
|---|---|---|
| `Experiment` | 标识、类型、模板、仪器编号、操作人、状态、MVD、内毒素限值、实测灵敏度、签署人和时间 | P3 数据库 |
| `TubeAssignment`、`Sample` | 管号、样品名称、序列号、批号、稀释倍数、外加内毒素、用途 | P3 数据库 |
| `ReagentBatch` | 种类、批号、有效期、备案状态、标示灵敏度 | P3 数据库 |
| `TubeResult` | 算法状态、三态、评分、置信度、特征快照、模型和配置版本、自动判读、复核信息 | P3 数据库 |

审计事件（`AuditAction`）：`experiment_created`、`reagent_checked`、`experiment_state_changed`、`row_analyzed`、`analysis_timeout`、`engine_alert`、`tube_reviewed`、`experiment_signed`。运行期的状态转换和它的审计、结果、媒体记录在同一事务提交（`UnitOfWork`）；其余事件由后续阶段的服务层写入。

## 测试

| 文件 | 覆盖内容 |
|---|---|
| `tests/domain/test_state_machine.py` | 转换表与规格完全一致；穷举所有「状态 × 事件」组合；所有状态可达；终态无出口 |
| `tests/domain/test_entities.py` | 试剂校验和锁定；前置条件；复核流程；签署及其无副作用的失败 |
| `tests/domain/test_interpretation.py` | 默认映射；配置映射；失败结果固定待复核 |
| `tests/domain/test_templates.py` | 五类模板的管数和数值；推定模板的标记 |

运行期的状态路径和故障注入（算法失败转待复核等）由 `tests/services/test_experiment_engine*.py` 覆盖。需要人工验证：P9 完成后在界面上检查过期试剂被锁定。
