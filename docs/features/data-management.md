# 数据管理（数据层）

## 目的

提供软件的持久化存储：用户、试验、管位与样品、试剂批次、单管结果、视频与图像文件记录、报告记录、审计事件、温度日志和设备日志。对应 P3；P6（试验引擎写结果）、P9（复核）、P11（报告）和 P12（历史与审计页）都通过它读写数据。

代码位于 `src/gelinspect/infrastructure/db/`，仓储接口位于 `src/gelinspect/interfaces/storage.py`。

- 存储：SQLite（WAL、外键开启），SQLAlchemy 2.1 的 typed ORM（`Mapped[...]`），Alembic 迁移。
- **没有 `is_simulated` 一类字段**。模拟与真实数据在库中不可区分（`docs/PLAN.md` 第 3 节）。
- ORM 模型（`models.py`）只在 `infrastructure/db` 内部使用。服务层依赖 `interfaces.storage` 中的仓储 `Protocol`，输入输出都是 `domain` 实体或 `interfaces.storage` 中的值对象。`tests/infrastructure/db/test_layering.py` 检查 `domain`、`interfaces`、`services`、`ui` 不导入 `infrastructure` 和 `sqlalchemy`。
- 仓储**与权限无关**：不检查调用者角色。权限由服务层用 `domain.permissions` 检查。

## 流程

### 打开数据库

```python
from gelinspect.infrastructure.db import open_database, seed_defaults, verify_audit_chain

db = open_database(path)  # 创建或升级到最新版本，启用 WAL 与外键
seed_defaults(db, admin_password=password)  # 首次启动：内置模板与管理员（密码由调用方传入）
db.experiments.save(experiment)  # 各仓储通过 db.users / templates / experiments /
db.audit.append(event)  #   audit / media / reports / telemetry 访问
verify_audit_chain(db)  # 校验审计哈希链
```

`open_database` 依次：创建父目录 → 建立引擎并在每个连接上设置 `journal_mode=WAL`、`foreign_keys=ON`、`synchronous=NORMAL`、`busy_timeout=5000` → 运行 Alembic `upgrade head`。迁移脚本以包内路径（`migrations/`）定位，**不使用 `alembic.ini`，也不依赖当前工作目录**；连接通过 `config.attributes["connection"]` 传给 `migrations/env.py`。数据库版本比程序新（`alembic_version` 指向未知版本）时抛 `DatabaseVersionError`，不降级。

**打包要求（P13）**：Alembic 在运行时按文件加载 `migrations/env.py` 和 `migrations/versions/*.py`，所以打包时必须把整个 `migrations/` 目录作为数据文件带上，仅编译成二进制模块不够。

### 新增迁移

1. 修改 `models.py`。
2. 用 Alembic 的 `revision --autogenerate`（参考 `migrations/script.py.mako`）生成脚本，放入 `migrations/versions/`，命名 `rev_<编号>_<说明>.py`。
3. 迁移脚本是某一时刻的快照，不要导入应用代码（包括自定义列类型）。
4. `test_migrations.py::test_migration_schema_matches_orm_models` 会比较迁移后的表结构与 ORM，二者不一致时失败。

### 审计哈希链

每条审计记录存 `seq`、`prev_hash`、`hash`：

```text
hash = SHA-256( prev_hash + "\n" + canonical_json(seq, occurred_at, actor, actor_role, action, detail, experiment_id) )
```

- 规范化序列化：键排序、无空白、UTF-8、`ensure_ascii=False`；时间统一为 UTC 定长文本 `YYYY-MM-DDTHH:MM:SS.ffffffZ`。时间必须带时区，读出时为 UTC（与原值相等，但不保留原时区）。
- 第一条的 `prev_hash` 是 64 个 `0`。
- 链头表 `audit_chain_head`（一行）保存最后一条的序号和哈希，由迁移初始化，每次追加在同一事务内更新。追加前先对链头做一次空更新取得写锁，所以并发写入不会使链分叉。
- 仓储只有 `append`、`append_many`、`get`、`query`、`verify_chain`，**没有更新和删除**。测试检查公共方法集合固定。
- `verify_audit_chain(db)` 依次检查序号连续、内容与 `hash` 相符、`prev_hash` 与上一条相符、链头与最后一条相符，返回第一处异常：

| 情况 | `first_bad_seq` | `failure` |
|---|---|---|
| 某条内容被改（未重算 hash） | 该条 | `hash_mismatch` |
| 某条内容被改且重算了自己的 hash | 下一条（`prev_hash` 对不上） | `link_broken` |
| `prev_hash` 被改 | 该条 | `link_broken` |
| 中间或开头某条被删除 | 缺失的序号 | `missing` |
| 尾部被删除 | 第一条缺失的序号 | `missing` |
| 链头被改或缺失 | 最后一条（链头缺失时为空） | `head_mismatch` |

**限制**：本阶段不使用密钥。能直接写库文件的人，如果同时重算从被改处起的整条链**和**链头，校验无法发现。密钥（HMAC 或签名）和库文件加密由 P13 负责。

### 查询与分页

所有列表查询接受 `PageRequest(offset, limit)`（`limit` 1–1000），返回 `Page(items, total, request)`。

- 审计：`AuditQuery` 按时间范围 `[start, end)`、操作者、事件类型、试验标识和关键字（在操作记录与试验标识中不区分大小写的子串匹配，`%`、`_` 按字面处理）过滤，按序号倒序。
- 历史：`ExperimentQuery` 按试验类型、创建时间范围 `[start, end)`、状态、操作人和批号过滤，按创建时间倒序。批号同时匹配**试剂批号**和**样品批号**，精确匹配。
- 温度与设备日志：按试验、时间范围分页；设备日志另可按级别和关键字过滤。
- 索引：审计 `occurred_at`、`(actor, seq)`、`(action, seq)`、`(experiment_id, seq)`；试验 `created_at`、`(experiment_type, created_at)`、`(state, created_at)`、`(operator, created_at)`；试剂批号、样品批号、复核状态、媒体 `(experiment_id, row_index)` 与 `sha256`、温度与设备日志的时间等。关键字的子串匹配无法使用索引，是全表扫描，10 万条约 36 ms。

### 初始数据

`seed_defaults(db, admin_password=...)`：

- 写入五类内置模板（`domain.templates.BUILTIN_TEMPLATES`，标记 `builtin`）。已存在的模板不覆盖。
- 创建管理员（默认登录名 `admin`），密码用 argon2id 哈希，`must_change_password=True`，首次登录必须修改。已存在同名用户时不改动，也不重置密码。
- 密码**必须由调用方传入**，代码里没有默认密码；少于 8 个字符抛 `ValueError`。
- 每个新增项写一条操作者为 `system` 的审计事件，事件文本不含密码。

## 权限

数据层不判断权限。各操作所需权限由服务层按 `docs/features/permissions.md` 检查，下表是预期的调用方约定：

| 操作 | 所需权限 |
|---|---|
| 查询审计、校验哈希链 | `audit_view` |
| 查询历史与读取试验 | `experiment_view` |
| 保存试验与管位 | `experiment_create` |
| 写入分析结果 | 试验引擎（系统）调用，不对应用户权限 |
| 写入复核结论 | `result_review` |
| 用户增删改、重置密码 | `user_manage` |
| 保存模板 | `template_manage` |
| 追加审计事件 | 无需权限，所有受审计的操作都应写入 |

## 数据

| 表 | 内容 | 对应实体 |
|---|---|---|
| `users` | 登录名、显示名、角色、启用、argon2 密码哈希、是否必须改密 | `domain.User`，哈希在 `UserCredentials` |
| `templates`、`template_items` | 模板与管位项，`builtin` 标记 | `ExperimentTemplate` |
| `experiments` | 类型、模板、仪器、操作人、状态、灵敏度、MVD、限值、签署人与时间 | `Experiment` |
| `tube_assignments` | 管号、样品（标识、名称、序列号、批号）、用途、稀释倍数、外加内毒素 | `TubeAssignment` |
| `reagent_batches` | 种类、批号、有效期、是否备案、标示灵敏度、录入顺序 | `ReagentBatch` |
| `tube_results` | 算法状态与三态、Score、置信度、特征（JSON）、模型版本、配置版本、耗时、阴阳性、复核状态与结论、复核人、时间、备注 | `TubeResult` |
| `media_files` | 视频与图像的路径、SHA-256、试验、排号、大小 | `MediaFile` |
| `reports` | 报告路径、SHA-256、生成者、是否已签名 | `ReportRecord` |
| `audit_events`、`audit_chain_head` | 审计事件、哈希链、链头 | `AuditRecord` |
| `temperature_log`、`device_log` | 温度采样、设备日志 | `TemperatureSample`、`DeviceLogEntry` |

说明：

- 枚举以字符串值存储，不用数据库枚举类型，新增枚举值不需要迁移。
- 试验的 `template_id` 和 `operator` 是历史快照，不设外键；子表对 `experiments` 设外键并级联删除（仓储不提供删除试验）。
- 时间列统一用 `UtcDateTime`：UTC 定长文本，拒绝不带时区的时间。
- `TubeResult.elapsed_ms` 是 P3 为存储耗时新增的字段（见 `docs/CHANGELOG.md`），取该排 `RowAnalysisResult.elapsed_ms`。
- 数据库只存媒体和报告文件的路径与哈希，不存内容。文件落盘、路径约定和哈希计算由 P5、P11 负责。
- `ExperimentRepository.save` 整体覆盖试验及其管位、试剂和结果；引擎按排写入用 `save_results`，状态转换用 `update_state`（可带预期状态做比较后更新，不符时抛 `StateConflictError`）。
- 单个仓储方法各自是一个事务。需要多个仓储一起生效时用 `Database.unit_of_work()`（`interfaces.storage.UnitOfWork`，P6 新增）：以 `BEGIN IMMEDIATE` 开始，全部仓储共用一个会话，正常退出提交，异常回滚。用法和约束见 [experiment-engine.md](experiment-engine.md)。

审计事件：数据层本身只写初始化事件（`template_changed`、`user_changed`，操作者 `system`）。其余事件由各服务按 `AuditAction` 写入。

## 测试

自动化测试在 `tests/infrastructure/db/`：

| 文件 | 覆盖 |
|---|---|
| `test_roundtrip.py` | 用户、五个内置模板、含复核与特征元组的试验、增量写结果、UTC 归一、媒体与报告、温度与设备日志、审计事件的往返；表中没有任何含 `simulat` 的列 |
| `test_migrations.py` | 创建与重复打开、WAL 与外键、外键生效、迁移结果与 ORM 一致、切换工作目录后仍可迁移、拒绝更新版本的库、索引存在 |
| `test_audit_chain.py` | 与独立实现的哈希一致；直接用 SQL 篡改内容、重算单条哈希、改 `prev_hash`、删除开头/中间/尾部/全部、改链头，都能定位；跨批次校验；并发写入不分叉；仓储无更新删除方法 |
| `test_queries.py` | 审计按时间、操作者、事件、试验、关键字（含 `%`、`_`）过滤与分页；历史按类型、时间、批号、状态过滤与分页；查询计划用到索引 |
| `test_seed.py` | 模板与管理员、argon2 哈希与改密标记、幂等、不重置密码、无默认密码、弱密码被拒、审计链有效 |
| `test_unit_of_work.py` | 工作单元：回滚、每一步失败、提交失败、吞掉异常不提交、写锁、并发下的哈希链、真实进程在事务各点被杀 |
| `test_layering.py` | ORM 与 SQLAlchemy 不被上层导入；数据层不依赖权限矩阵；仓储返回 domain 对象 |
| `test_perf.py`（`@pytest.mark.perf`） | 10 万条审计 + 1 万管结果，各类分页查询小于 1 秒，并校验整条哈希链 |

`perf` 标记在 `pyproject.toml` 注册，默认运行；跳过用 `uv run pytest -m "not perf"`，查看实测数字用 `uv run pytest -s tests/infrastructure/db/test_perf.py`。

实测（macOS 开发机，SQLite 文件库）：写入 10 万条审计 2.0 s，1 万管结果 0.5 s；审计首页 1.2 ms，深翻页（offset 50000）3.2 ms，按操作者 1.3 ms，按时间段 3.2 ms，关键字 36 ms，组合条件 6.4 ms；历史各类查询 0.8–1.4 ms；校验 10 万条哈希链 0.38 s。

需要人工验证：

- Windows 上的 `open_database`、WAL 与文件锁行为（特别是库文件放在网络盘或被杀毒软件扫描时）。
- 打包产物（P13）中迁移能运行，即 `migrations/` 目录已随包分发。
