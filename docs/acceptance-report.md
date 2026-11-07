# 验收指标报告

本报告逐条对照研发计划的验收指标和 [PLAN](PLAN.md) 第 9 节“验证方式”，记录软件能测的项的**实测数字**。依赖算法和样机的项写明“无法验证”，不编造数字。

## 1. 结论

| 类别 | 结果 |
|---|---|
| 软件可测的指标（界面响应、查询、导出、多语言、报告、权限、加密防护、全流程不卡顿） | **在 macOS 上全部通过**，数字见第 3 节 |
| 依赖算法和样机的指标（8 支 10 秒、准确率 ≥99.5%、错判率 <0.3%） | **本阶段无法验证**，需算法和样机联调 |
| Windows | **未实测**（没有 Windows 环境），脚本和流程已就绪，清单见 [部署说明](deployment.md) 第 2.2 节 |
| 仓库内一键启动（干净克隆） | 通过，见第 5 节 |

## 2. 测量方法和环境

| 项 | 内容 |
|---|---|
| 系统 | macOS 27.0.1（Darwin 27.0.0），arm64 |
| 机型 | MacBook Air（Mac16,12），Apple M4，16 GB 内存 |
| Python / Qt | Python 3.11.15，PySide6 6.12.0 |
| 软件版本 | 0.1.0（仓库运行：`uv run`） |
| 显示 | `QT_QPA_PLATFORM=offscreen` 离屏，**不含 GPU 绘制耗时**；耗时是墙钟时间 |
| 后端 | `simulation/` 的模拟设备、模拟视频、模拟算法 |
| 测量脚本 | `scripts/measure_acceptance.py`（本报告的全部“全流程、界面、报告、权限、加密”数字）；已有 `perf` 测试：`uv run pytest -m perf -s`；视频压测：`uv run pytest -m video_perf -s` |

重现命令：

```bash
QT_QPA_PLATFORM=offscreen uv run python scripts/measure_acceptance.py --json result.json
QT_QPA_PLATFORM=offscreen uv run pytest -m video_perf -s
```

数据量：10 万条审计记录、125 个试验共 1 万管结果（与 PLAN 第 9 节一致；性能数据按每个试验 80 管造数以保持 1 万管的规模，与孔板改为 96 孔无关）。操作由脚本通过**真实控件**（点击、键入、回车）驱动，与自动化测试用的是同一套方法。

## 3. 逐项结果

### 3.1 软件可测的指标

| # | 指标 | 测量方法 | 结果（macOS，M4） | 通过 |
|---|---|---|---|---|
| 1 | **界面响应 <1 s** | 10 万审计 + 1 万管数据下，登录后逐页切换（11 个页面，首次创建页面和再次进入）；页面上的审计关键字查询；试验运行中切换监控页标签 | 首次切页最慢 **112 ms**（报告中心），再次进入最慢 55 ms；审计页关键字全表查询 68 ms；运行中切标签最慢 19 ms | 是 |
| 2 | **数据查询 <1 s** | 服务层查询，10 万条审计 + 1 万管（`perf` 测试和测量脚本各一遍） | 审计首页 2 ms；深翻页（offset 5 万）8 ms；按操作者 1.1 ms；按时间段 3 ms；按关键字（全表子串）36 ms；组合条件 6 ms；历史首页 0.7–1.1 ms；按批号 1.4–1.7 ms；读一个 80 管试验 2.4 ms；**校验 10 万条审计哈希链 0.49 s** | 是 |
| 3 | **批量导出报告 <10 s** | 100 份已签署 PDF（每份约 99 KB）批量导出，含逐份 SHA-256 核对和清单；另测 Excel 导出 | 100 份报告 **0.026 s**（文件是同一份签名 PDF 的副本，主要成本是校验和复制）；Excel 导出 1 万管结果 **0.86 s**（0.9 MB）；Excel 导出 10 万条审计 4.6 s；历史列表 125 行 0.01 s | 是 |
| 4 | **多语言切换** | 全部页面都创建后，简体中文 ↔ English 切换并等待界面重绘；英文模式下以三种角色逐页、逐标签、逐对话框扫描所有标签、按钮、提示、下拉项、表头和单元格里的中文（`scripts/check_english_ui.py`）；检查翻译文件是否完整 | 切换 **46 ms**（中→英）、38 ms（英→中）；翻译文件 0 项未翻译；英文界面**除审计追踪的“操作记录”原文外没有中文残留**（审计记录按发生时的语言原文入库并受哈希链保护，不翻译，已在手册说明）；各页面中英文 × 1280×800 与 1024×700 的布局测试在自动测试里 | 是 |
| 5 | **报告生成** | 17 管和 96 管（整板 12 排）试验：预览（版面计算）、渲染成 A4 PDF、签署（渲染 + PAdES 签名 + 自检 + 落盘 + 入库）、验签、导出 | 预览 5–8 ms；渲染成 PDF 17 管（1 页）56 ms、96 管（3 页）136 ms；**签署 0.23 s**；验签 4 ms；导出单份 1 ms；PDF 约 98 KB | 是 |
| 6 | **权限** | 3 个角色 × 14 项权限：服务层 `authorize` 42 项；7 个服务入口（用户、审计、模板、导出、复核、签署、报告预览）21 项；界面路由 3 × 12 = 36 项，与权限矩阵逐项对照 | 99 项检查，**0 项与矩阵不符**；自动测试另覆盖界面禁用和服务层再校验 | 是 |
| 7 | **加密防护** | 抽查：私钥落盘、密码哈希、登录锁定、导出加密、签名 PDF 改一个字节、审计链篡改 | 签名私钥文件头 `GIKP1:file`（受保护，非明文 PEM）；全部密码哈希为 argon2；连续输错 5 次锁定；加密导出是 AES-256 加密 ZIP，密文不含明文表格标记，错误密码解不开、正确密码能解开；签名 PDF 改一个字节后验签失败，还原后通过；审计库里改一条记录，校验检出并定位到被改的序号（50），还原后通过。macOS 发布包的完整性校验、钥匙串保护见 [部署说明](deployment.md) 第 2.1 节的实测 | 是 |
| 8 | **全流程运行无卡顿** | 真实向导启动，运行期间用 5 ms 心跳定时器统计界面线程被阻塞的最长时间；视频用默认的 1920×1080、15 fps | **17 管灵敏度复核**：点“确认并启动”到跑完 6.5 s（孵育压缩到 0.05 分钟），界面线程心跳最大间隔 **65 ms**，超过 100 ms 的 0 次；复核 0.05 s、签署 0.23 s、验签 4 ms，从选模板到验签通过共约 7.2 s。**整板 96 管（12 排）**：启动到跑完 23.7 s，最大间隔 **80 ms**，超过 100 ms 的 0 次，结果 96/96 | 是 |
| 9 | **视频管线压测**（PLAN 第 9 节） | 4000×3000、10 fps、预览加录制加抓帧，运行 20 秒（`video_perf` 测试） | 采集 209 帧 = 10.00 fps，预览送出 209、跳过 0，录制 209 帧、丢 0，驱动丢帧 0；缓冲峰值 504 MB（封顶 504 MB）；内存稳态增长 +1 MB（最大 915 MB）；抓拍 5 次无丢帧 | 是 |
| 10 | **离线检查**（PLAN 第 9 节） | 静态：`src/` 里没有 socket、urllib、requests、http 相关导入；运行：禁用 `socket` 的连接、域名解析后完整跑 `--smoke-test`（登录、浏览全部页面、切换语言） | 静态检查无命中；禁用网络后返回码 0，**网络连接尝试 0 次** | 是 |

说明：

- 上表的时间是离屏平台下的墙钟时间，不含真机 GPU 绘制，也不含屏幕刷新；工控机的实际数字需要在设备上复测。
- 试验的孵育时间被压缩（时间加速 60 倍），所以“全流程”测的是软件自身的耗时和界面流畅度，不是 60 分钟孵育的真实时长。
- 批量导出 100 份的耗时受“文件是同一份副本”影响偏乐观；真实场景下文件大小相当（约 100 KB），瓶颈仍是磁盘读写和 SHA-256，余量很大。

### 3.2 依赖算法和样机的指标（本阶段无法验证）

| # | 指标 | 状态 | 原因 |
|---|---|---|---|
| 11 | 8 支试管 10 秒内完成读数 | **需算法和样机联调，本阶段无法验证** | 取决于真实的翻转机构、相机取图和算法推理耗时。模拟环境的耗时（模拟算法约 0.1 s）没有参考意义，不作为结果 |
| 12 | 判读准确率 ≥99.5% | **需算法和样机联调，本阶段无法验证** | 软件只调用算法接口，不做判读；没有真实算法和带标注的真实凝胶图像 |
| 13 | 错判率 <0.3% | **需算法和样机联调，本阶段无法验证** | 同上。软件侧的保护是：算法没有把握的试管（部分凝固、低质量、失败）一律转人工复核，绝不默认判阴性 |
| 14 | 单排分析 ≤2 秒（8 支） | **需算法联调** | 软件已实现超时告警（不中断流程）；契约测试只在耗时超过 2 秒时给警告，不校验真实模型的耗时 |
| 15 | 工业相机取图、设备翻转和控温 | **需样机联调** | 相机型号、SDK 和设备协议未定；真实驱动用适配层模板隔开，当前是模拟实现 |

联调时的接入点和命令见 [交接说明](handover.md)。

### 3.3 PLAN 第 9 节逐项

| 验证方式 | 状态 | 说明 |
|---|---|---|
| 自动测试 | 通过 | `uv run pytest` 全量通过（次数和耗时见第 6 节）；覆盖领域规则、状态机、审计哈希链、权限矩阵、报告验签与篡改检测；`pytest-qt` 走完“登录 → 新试验 → 模拟完成 → 复核 → 签署 → 导出”全流程 |
| 模拟运行 | 通过 | 整板 12 排×8 支（96 管）跑完（上表第 8 项）；故障注入场景（过期试剂锁定、温度越限告警、视频断流、算法失败转人工复核）由引擎和界面自动测试覆盖 |
| 视频管线 | 通过 | 上表第 9 项；抓帧时间、录制哈希、可播放性由 `tests/services/video` 覆盖 |
| 性能指标 | 通过 | 上表第 1–3 项 |
| 视觉检查 | 已做，需人工对照设计稿 | 每页离屏截图（用户手册配图，`docs/images/`，`scripts/capture_manual_images.py` 生成）；中英文和 1280×800 / 1024×700 的溢出检查由 `tests/ui/test_layout_languages.py` 自动测试 |
| 离线检查 | 通过 | 上表第 10 项 |
| 启动检查 | 通过 | 第 5 节。Windows 由使用方运行同一条命令验证（**未实测**） |
| 打包检查 | macOS 在 P13 通过，本阶段未重跑；Windows **未实测** | 见 [部署说明](deployment.md) 第 2 节；本阶段没有改动打包相关文件，所以没有重新构建 |
| 接口检查 | 通过 | `tests/contract` 同时校验 `MockAnalyzer` 和按适配模板写的假后端；架构测试保证主代码不导入 `simulation/` |

## 4. 全流程检查记录

### 4.1 面向用户的残留

| 检查 | 结果 |
|---|---|
| 开发阶段编号（P1–P14）出现在界面、README、部署说明里 | 在 `src/` 的注释和文档字符串里发现 19 处，已全部改成不带阶段编号的描述；README、部署说明、架构说明里的 5 处已修改。`docs/features/` 里的“对应阶段”是文档模板要求保留的 |
| `TODO`、`FIXME`、“建设中” | 无（P12 已删除占位页） |
| 硬编码测试数据 | 界面代码里没有；演示账号、示例码、模拟白名单都只在 `simulation/` 里，由配置引用。`ui/monitor_sample.py` 和 `ui/gallery.py` 是组件展示页（开发工具）的示例数据，运行 `uv run gelinspect` 不会用到 |
| 英文界面的中文残留 | 修了 5 类（见第 7 节）。仍保留的：审计追踪“操作记录”的原文；证书主题等由数据生成的内容 |
| 占位文字 | 版权方名称默认 `[版权方名称]`，是有意的占位，用户手册提示管理员在仪器设置里改 |

### 4.2 文档里的命令和路径

抽查并实际运行了 15 条：`uv run gelinspect --version`、`--simulate --smoke-test`、`--config simulation/config.example.yaml --smoke-test`、`--diagnose`；`uv run python scripts/update_translations.py --check`；`uv run pytest tests/contract`（含 `--analyzer-backend=simulation.mock_analyzer:MockAnalyzer`）；`uv run pytest tests/infrastructure/security`；`uv run pytest -s tests/infrastructure/db/test_perf.py`；`uv run pytest -m perf -s tests/services/reporting/test_batch_and_export.py`；`uv run pytest -m video_perf -v -s`；`uv run pyside6-lrelease translations/gelinspect_en.ts`；`uv run python -m simulation.image_synth --help`；`uv run python -m gelinspect.ui.gallery --help`；`uv sync --extra inference --dry-run`。**全部返回 0。** 另用脚本核对了全部文档里反引号包住的文件路径，缺失的都是发布包里才有的文件（清单、许可文本、运行时生成的 `config.yaml`、`license.json` 等）或示例名，没有过时的路径。打包命令未运行（没有改动打包文件，P13 已验证）。

### 4.3 功能文档和 CHANGELOG

`docs/features/` 的 15 篇功能文档都包含“目的、流程、权限、数据、测试”五个部分，符合模板；有 6 篇多一个“已知限制”或“待 QA 确认”小节。一处标题带阶段编号（“P12 对数据层的补充”）已改名。CHANGELOG 与代码核对一致（迁移 0001–0005、6 个快捷栏操作、五类内置模板、性能数字与实测相符）。按约束没有改动 `docs/CHANGELOG.md` 和 `docs/PLAN.md`，本阶段的变更日志文字见第 8 节。

## 5. 干净克隆

`git clone` 到临时目录后在里面运行 `QT_QPA_PLATFORM=offscreen uv run gelinspect --smoke-test`（自动创建虚拟环境并安装 55 个依赖包，依赖已在本机缓存中；无缓存时首次需联网下载）：

| 项 | 结果 |
|---|---|
| 返回码 | **0** |
| 总耗时（含创建虚拟环境和安装） | 约 28–33 s（第一次） |
| 之后再运行（环境已建好） | 约 1.5 s |
| 内容 | 首次启动创建 `data/`、运行迁移、加载模拟后端、登录演示账号、浏览全部页面、切换英文再切回、退出登录 |

最终版本（含本阶段全部改动，叠加到干净克隆上）再跑一遍：返回码 **0**，首次（含创建环境）32.5 s，再次 2.1 s。

## 6. 代码质量抽查

全量检查结果：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`（341 个源文件）、`QT_QPA_PLATFORM=offscreen uv run pytest`（1664 通过、3 跳过，其中 1 项是默认跳过的视频压测，已单独运行，见 3.1 第 9 项）**连续两次全部通过**，pytest 分别用时 8 分 4 秒和 8 分 4 秒（96 孔改动后新增整板和布局测试，耗时增加）。

**超长函数（前 10，按行数；都不是核心逻辑缺陷，未改）**：

| 行数 | 位置 |
|---|---|
| 276 | `infrastructure/db/migrations/versions/rev_0001_initial_schema.py::upgrade`（建表迁移，迁移脚本本来就是线性的） |
| 173 | `services/excel_export.py::write_results` |
| 165 | `app_context.py::build_app_context`（组装根） |
| 164 | `infrastructure/db/repositories/audit.py::verify_chain` |
| 144 | `ui/pages/settings_page.py::SettingsPage.__init__`（建界面） |
| 143 | `services/experiment_service.py::create_experiment` |
| 132 | `services/reporting/report_service.py::ReportService.sign` |
| 125 | `startup.py::run_gui` |
| 124 | `ui/pages/reports_page.py::_build_panel`（建界面） |
| 123 | `services/encrypted_zip.py::write_encrypted_zip` |

**`type: ignore`（9 处，都有理由）**：6 处 `type-abstract` 是把 `Protocol` 类传给加载函数做类型检查（`app_context.py`、`bootstrap.py`）；`replay_player.py` 的 `hideEvent` 是 PySide6 存根的签名问题；`renderer.py` 的返回值是 Qt 对象的运行时向下转型；`pdf_signing.py` 是 pyHanko 的 `SimpleCertificateStore` 缺少类型标注。

**`except Exception` / `BaseException`（67 处）**：逐处看过。都属于下列有理由的几类：线程边界（工作线程、采集线程、回调、事件监听者抛出的异常只记日志，不拖垮线程）；清理后重新抛出（`BaseException` 用于写临时文件、签署、导出失败时删除半成品再抛）；把外部后端的任意失败转成带说明的 `ConfigError`、`VideoSourceConfigError`；整体故障转成试验异常状态。发现 1 处吞掉异常没有记日志（`ui/pages/monitor_images.py` 读抓拍图像列表失败时静默变成空），已改为写日志。

**死代码**：按“全仓只出现一次”检查，有 4 个没有任何调用的公共函数：`protectors.overwrite_file`、`ui/labels.describe_settings_error`、`ui/theme/tokens.register_theme`、`ui/widgets/marks.well_state_from_interpretation`。它们不影响运行，也有可能是预留接口，**没有删除**，列入建议。

**未使用的配置项**：逐个检查了 `services/config.py` 里的字段，都有读取点，没有发现。

**重复实现**：抽查中没有发现需要合并的重复；`ui/pages/monitor_page.py`（约 1000 行）和 `services/reporting/report_service.py`（约 1250 行）体量偏大，见建议。

**建议（较大的重构，本阶段不动）**：

1. 把 `monitor_page.py`、`report_service.py`、`engine_run.py`（约 1600 行）按职责拆成更小的模块，并拆分上表中的长函数（`sign`、`create_experiment`、`write_results`）。
2. 引擎告警和审计记录是中文原文，英文界面只能显示概要或原文。若要完整英文化，需要给告警加结构化参数（告警码 + 参数），审计详情在显示时按码翻译（入库原文不变，哈希链不受影响）。
3. 删除或补测试上述 4 个无调用的函数。
4. 增加一个自动测试，禁用 socket 后跑冒烟检查，把本报告第 3.1 节第 10 项变成回归保护。
5. 把“界面响应”的测量接到 CI（`scripts/measure_acceptance.py`），在工控机上复测一次。

## 7. 本阶段发现并修复的问题

1. **英文界面里的引擎告警和视频告警仍是中文**（例如“Row03 晚于到时 181 秒才开始读数”）：新增按告警码翻译的概要文字（简体中文仍显示完整说明），覆盖 19 种引擎告警（另有舱门开、关两种说明）和 15 种视频事件。
2. **内置模板名和默认样品名在英文界面是中文**（“灵敏度复核 (2λ:1)”“阴性对照”等）：新增显示层翻译，用户自定义的名称原样显示。
3. **英文界面里的全角标点**：快捷栏、侧栏的提示（“New experiment（⌘N）”“Audit trail：…”）、管号和排号的顿号列表、签名证书主题（“GelInspect Report Signing（GelInspect）”）、关于页的字体名；新增随语言变化的标点辅助函数 `ui/punctuation.py`，证书主题改用半角括号。
4. **开发阶段编号残留**：`src/` 的 19 处注释和文档字符串，README、部署说明、架构说明里的 5 处，以及功能文档里一处标题。
5. **一处异常被静默吞掉**（`monitor_images.py`）：改为写日志。
6. 补充了英文翻译（共 45 条）；`update_translations.py --check` 通过。
7. 新增 `scripts/check_english_ui.py`：在英文模式下逐页扫描中文残留，可重复运行。

## 8. 变更日志文字（供负责人并入 `docs/CHANGELOG.md`）

```markdown
### 新增

- P14：用户手册（`docs/user-manual.md`，30 张真实截图）、验收指标报告（`docs/acceptance-report.md`）、交接说明（`docs/handover.md`）；验收脚本 `scripts/measure_acceptance.py`（全流程、界面响应、报告、权限、加密防护的一次性测量）、`scripts/capture_manual_images.py`（离屏生成手册配图）、`scripts/check_english_ui.py`（英文界面中文残留扫描）。

### 修复

- 英文界面：引擎告警和视频告警按告警码显示英文概要；内置模板名和默认样品名随语言翻译；英文里不再出现全角括号、冒号和顿号（新增 `ui/punctuation.py`，证书主题改用半角括号）；补全英文翻译 45 条。
- 监控页读取抓拍图像列表失败时写日志，不再静默。
- 清理代码注释和 README、部署说明里的开发阶段编号。
```
