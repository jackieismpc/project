# 一键启动、登录权限与多语言

对应阶段：P7（外壳与权限），繁体中文于 2026-11-07 补充。代码：`src/gelinspect/startup.py`、`bootstrap.py`、`logging_setup.py`、`services/auth.py`、`services/queries.py`、`ui/main_window.py`、`ui/pages/`、`ui/bridge.py`、`ui/i18n.py`。

## 目的

让软件在仓库里一条命令就能用起来，并把“谁能做什么”“界面说什么语言”落到真实界面上：

- `uv run gelinspect` 不带参数即打开主窗口，首次启动自动初始化，启动失败有清楚的说明。
- 登录、失败锁定、空闲锁屏、改密，所有账号动作写审计。
- 权限在界面禁用入口，并在服务层再校验一次。
- 简体中文（源语言）、繁体中文（台湾用语）和英文，运行中切换立即生效；系统语言是繁体中文时默认用繁体中文。

面向全部用户；创建管理员页面向首次部署的管理员。

## 流程

### 启动

```text
uv run gelinspect
 ├─ 读配置（默认 DEFAULT_CONFIG，指向 simulation/；--config 指定文件）
 ├─ （P13）判断运行模式：仓库模式用 ./data；发布包用平台标准数据目录，见 deployment.md
 ├─ 配置日志 data/logs/gelinspect.log（滚动 1 MB × 5）+ 未捕获异常兜底
 ├─ 创建 QApplication → 主题 → 语言（读取 QSettings；没有保存过时看系统语言，繁体中文地区用繁体；缺 .qm 时调用 lrelease 编译）
 ├─ build_app_context：加载后端 → 打开数据库（自动建 data/ 与迁移）→ 启用审计链 HMAC、
 │    加密迁移签名私钥（P13，见 security.md）→ 补全内置模板
 │    └─ 用户表为空：运行 bootstrap.hook（模拟配置创建三个演示账号）
 ├─ 启动恢复：停留在运行中的试验标记为异常
 ├─ （P13）程序完整性校验：发布包按签名清单校验，不一致时提示并写审计；仓库模式跳过并写日志
 ├─ MainWindow：有账号 → 登录页；没有账号 → 创建管理员页
 └─ 事件循环 → 退出：确认 → 仪器监视 → 引擎 → 视频管线 → 设备 → 数据库 → 日志
```

任何一步失败都弹出 `ErrorDialog`（标题、怎么办、技术细节、日志位置）并写日志，退出码：1 其他错误，2 配置有误，3 数据库问题（被占用、版本过新、无法读写）。

| 失败 | 对话框 |
|---|---|
| 配置不是合法 YAML、有未知字段 | 配置有误，无法启动（附原文） |
| `simulation/` 被移走又没改配置 | 配置有误，指出是哪一项（`device.backend`、`bootstrap.hook` 等）和怎么改 |
| 另一个进程占着数据库 | 数据库被占用 |
| 数据库由更新版本创建 | 数据库版本不兼容 |
| 其他意外 | 启动失败（技术细节和日志位置） |

运行中未捕获的异常（主线程和工作线程）写日志；主线程的会弹出“程序遇到未处理的错误”，同一时间只弹一个。

### 首次运行与账号

| 配置 | 首次启动（用户表为空） |
|---|---|
| 默认 / 模拟（有 `bootstrap.hook`） | 钩子（`simulation/demo_accounts.py`）创建 `admin`、`experimenter`、`analyst`，密码公开、不要求改密；登录页的“演示账号”提示列出它们，点一下填入 |
| 真实（没有 `bootstrap.hook`） | 显示“创建管理员”页，用 `seed_defaults` 创建管理员。密码是管理员自己设的，**直接生效，首次登录不再强制改密**（`seed_defaults(..., must_change_password=False)` 为默认；别人代设初始密码时传 `True`，该账号首次登录仍须改密）；数据库里没有任何默认账号和默认密码 |

再次启动时用户表不为空，不再运行钩子，内置模板只补缺失的，不重复写审计。主代码只持有钩子的类路径字符串（`interfaces/bootstrap.py` 定义 `BootstrapHook` 协议），不导入 `simulation/`。

“创建管理员”只在系统里没有任何账号时可用，`AuthService.create_first_admin` 在服务层校验，已有账号后拒绝。

### 登录、锁定与锁屏

1. 登录页输入用户名和密码，`AuthService.login` 用 argon2 校验。用户不存在时也做一次哈希运算，响应时间不泄露用户是否存在。
2. 连续失败 5 次（可配置）锁定 5 分钟（可配置）。锁定期间连正确的密码也拒绝，登录页显示倒计时并禁用输入。每次失败、锁定都写 `login_failed` 审计；成功写 `login`；退出写 `logout`。
3. 标记为“必须改密”的账号（由他人代设初始密码时才有，创建管理员页建的管理员没有），登录后弹出不能取消的改密对话框（取消等于退出登录），改密前服务层拒绝一切其他操作。
4. 空闲（无鼠标、键盘、触摸）超过设定时长自动锁屏，默认 15 分钟，配置项 `security.idle_lock_minutes`，0 表示不锁。也可以从用户菜单手动“锁定屏幕”。锁屏时会话保留、服务层拒绝一切操作，**试验在后台继续运行**；输入密码解锁，失败同样计入锁定次数。锁屏时若有试验在进行，关窗被拒绝，必须先解锁。
5. 用户菜单（状态栏右侧）：修改密码、锁定屏幕、退出登录。

锁定计数保存在内存里，重启进程后清零（每次失败都有审计可事后追查）。

**数据库忙**：登录等写审计的操作等写锁超时时，仓储抛 `StorageBusyError`（不再是原始 SQLAlchemy 异常），`AuthService` 转成 `AuthStorageBusyError`，界面提示“数据库正忙，请稍后再试”，不崩溃。登录和解锁因此不成功（没有留痕不能成功）；拒绝、锁屏、退出、登录失败是保护性结果，数据库忙时照常生效，审计缺失只记日志。

### 页面与导航

设计稿的 12 个页面都有路由（`ui/routes.py`）：登录、工作台、新试验、试验监控、单管详情、历史记录、模板管理、试剂溯源、报告中心、审计追踪、用户与权限、仪器设置；另有“关于”。

| 页面 | 状态 |
|---|---|
| 登录、锁屏、创建管理员 | 已实现 |
| 工作台 | P9 接上真实数据：温度曲线、进行中的试验（孔板缩略图和进度）、最近结果、需要处理，见 [monitor-and-review.md](monitor-and-review.md) |
| 试验监控、单管详情、历史记录 | P9 已实现，见 [monitor-and-review.md](monitor-and-review.md) |
| 关于 | 已实现：版本、运行环境、后端、开源许可 |
| 报告中心 | P11 已实现，见 [reports.md](reports.md) |
| 历史记录（升级）、审计追踪、用户与权限、仪器设置 | P12 已实现，见 [admin-pages.md](admin-pages.md)；至此没有占位页了。仪器设置对所有角色开放（只读），修改需要 `instrument_configure` |

快捷栏操作和侧栏菜单项导航到对应页面；无权限的入口置灰并提示“当前角色无此权限”。状态栏右侧的仪器连接状态、语言、当前用户都是真实数据：

- 仪器：空闲时由 `InstrumentMonitor` 每 2 秒通过 `ExperimentEngine.probe_device()` 探测，试验运行期间改用引擎的温度事件。`probe_device` 与运行中的试验互斥，不会与引擎线程同时访问设备。
- 语言：点击弹出语言菜单（简体中文、繁體中文、English）。
- 用户：显示名加角色，点击弹出用户菜单。

### 线程与桥接

`ui/` 只依赖 `services`，不直接碰数据库或引擎线程。`EngineBridge`（`ui/bridge.py`）订阅引擎事件和仪器状态，它们在 `engine-events`、`instrument-monitor` 线程上回调；桥发出内部信号，接收槽属于主线程，Qt 用排队连接投递，再按类型分发成带类型的信号（`stateChanged`、`alertRaised`、`temperatureChanged`、`wellUpdated`、`rowCountdown`、`instrumentChanged`）。`close()` 取消订阅并丢弃之后到达的事件。

### 多语言

三种界面语言，语言代码就是 `translations/` 里的文件名后缀：

| 语言 | 代码 | 显示名（不翻译） | 翻译文件 |
|---|---|---|---|
| 简体中文（源语言） | `zh_CN` | 简体中文 | `gelinspect_zh_CN.ts`，译文留空，界面直接显示源文本 |
| 繁体中文（台湾用语） | `zh_TW` | 繁體中文 | `gelinspect_zh_TW.ts`，每一条都有译文 |
| 英文 | `en` | English | `gelinspect_en.ts`，每一条都有译文 |

- 源文本全部是简体中文，用 `tr()`（类内）或 `QCoreApplication.translate("上下文", "文字")` / `QT_TRANSLATE_NOOP`（类外）。带参数的文字用 `{name}` 占位，译文保持同样的占位符。
- `.qm` 不入库。`LanguageManager` 启动时检查：缺失或比 `.ts` 旧，就调用 PySide6 自带的 `lrelease` 编译到 `translations/`（不可写时放到系统临时目录），所以干净克隆后直接运行就能切换语言。
- 切换：`LanguageManager.set_language` 安装或移除 `QTranslator`（简体和繁体还会装上 Qt 自带的 `qtbase_zh_CN` / `qtbase_zh_TW`，让标准对话框和输入框右键菜单跟着变），Qt 向所有控件发 `LanguageChange`；每个有文字的组件在 `changeEvent` 里调用自己的 `retranslate`。选择保存在 `QSettings`（键 `ui/language`，值是语言代码），下次启动恢复。
- 切换入口：状态栏的语言菜单（下拉，三种语言各一项，当前项打勾）；登录页、锁屏页、创建管理员页底部的语言行（三个文字按钮，当前语言加粗）。
- 默认语言：没有保存过选择时，`startup.py` 用 `detect_system_language()` 看系统区域：中文且文字是繁体（`zh_Hant`），或地区是台湾、香港、澳门，就用繁体中文；其余（包括英文系统）仍用简体中文。已保存的选择优先于系统语言。该语言的翻译文件缺失时退回简体中文。
- 语言名“简体中文”“繁體中文”“English”用各自的文字显示，不翻译。服务层的异常信息、审计记录、日志是简体中文（数据，不是界面）；界面按错误分类（`AuthErrorCode`）重新生成当前语言的提示。繁体界面里审计追踪的“操作记录”列也是保存时的简体原文。
- 报告、Excel 导出的语言见下文；`ui/punctuation.py` 的 `annotate`、`colon`、`join_items` 的半角处理来自英文译文（`{text} ({note})`、`, `），繁体译文保持全角（`{text}（{note}）`、`、`），代码里没有按语言写死标点。

#### 繁体中文的写法

- **用词**用台湾习惯（见下面的术语表）；**标点**用全角：，。、；：？！（）。
- **引号用弯引号“”**（用户明确要求，台湾习惯的直角引号不用）。全仓库一致，翻译文件、文档和注释里都不出现直角引号，测试强制。
- 记录作名词写“紀錄”（审计纪录、操作紀錄），作动词才写“記錄”；记录条数的量词用“筆”，告警用“則”。
- 数字、单位、型号、公式（λ、EU/mL、SHA-256）保持原样，中英文之间的空格沿用源文本。

#### 术语表

简体中文源文本、繁体中文译文、英文译文。全局统一，新增文字请按此表翻译；表里没有的词参考 Microsoft 台湾地区的惯用译法。

| 简体中文 | 繁体中文 | 英文 | 说明 |
|---|---|---|---|
| 鲎试剂 | 鱟試劑 | LAL reagent | 鱟字生僻，内置字体已包含 |
| 内毒素 | 內毒素 | endotoxin | |
| 凝胶 | 凝膠 | gel | 凝胶法、凝胶缺失 |
| 灵敏度 | 靈敏度 | sensitivity | 标示灵敏度 λ、实测灵敏度 λc |
| 阳性 / 阴性 | 陽性 / 陰性 | positive / negative | 阴性对照 → 陰性對照 |
| 复核 | 複核 | review | 待复核 → 待複核；复试 → 複試 |
| 试管 / 孔板 / 管位 | 試管 / 孔板 / 管位 | tube / plate / position | 孔板 12 排 × 8 支 |
| 试验 | 試驗 | experiment | |
| 样品 / 批号 | 樣品 / 批號 | sample / batch number | |
| **审计追踪** | **稽核軌跡** | audit trail | **选定“稽核軌跡”，不用“審計追蹤”**：台湾电子纪录和 GMP 文件里 audit trail 译作稽核軌跡；“审计”统一写“稽核”（稽核紀錄、稽核鏈）。Excel 工作表名、侧栏、权限、提示都用它 |
| 签署 / 签名 / 验签 | 簽署 / 簽名 / 驗簽 | sign / signature / verify | 证书写“證書”（簽名證書） |
| 报告 | 報告 | report | 报告中心 → 報告中心 |
| 软件 / 程序 | 軟體 / 程式 | software / program | |
| 数据 / 数据库 | 資料 / 資料庫 | data / database | 数据目录 → 資料目錄 |
| 视频 | 影片 | video | 视频源 → 影片來源；视频断流 → 影片串流中斷 |
| 帧 | 影格 | frame | 帧率 → 影格率；抓拍 → 擷取 |
| 打印 | 列印 | print | |
| 用户 / 用户名 / 登录 / 退出登录 | 使用者 / 使用者名稱 / 登入 / 登出 | user / username / sign in / sign out | 登录名 → 登入名稱 |
| 账号 / 权限 / 角色 | 帳號 / 權限 / 角色 | account / permission / role | 管理员、实验员、分析员 → 管理員、實驗員、分析員 |
| 配置 / 配置文件 | 設定 / 設定檔 | settings / configuration file | |
| 模板 | 範本 | template | 检测模板 → 檢測範本；内置 → 內建；自定义 → 自訂 |
| 向导 | 精靈 | wizard | 新试验向导 → 新試驗精靈 |
| 校验 | 驗證 | verify / check | 试剂校验 → 試劑驗證；通过 → 通過 |
| 告警 | 警示 | alert | |
| 质量 / 图像 | 品質 / 影像 | quality / image | |
| 掩码 | 遮罩 | mask | |
| 置信度 | 信賴度 | confidence | |
| 扫码 | 掃描 | scan | 扫码枪 → 掃描槍 |
| 当前 / 实时 / 默认 | 目前 / 即時 / 預設 | current / live / default | |
| 列表 / 重置 | 清單 / 重設 | list / reset | |
| 工作台 | 工作台 | Dashboard | 不用“臺”；也不改成“儀表板” |
| 会话 | 工作階段 | session | |
| 接口 / 接入 | 介面 / 串接 | interface / integrate | |

#### 字体

繁体字不能靠系统字体回退：回退字体的字形和字重与内置的 Noto Sans SC 不一致，PDF 里还会混进系统字体。采用**方案（a）：重新裁剪 Noto Sans SC，补入繁体字**（不另加 Noto Sans TC）。

| 项 | 做法 |
|---|---|
| 字符集 | 原有 7687 个码位（GB2312、拉丁、希腊、标点）∪ GB2312 全部汉字 ∪ **Big5 一级字 5401 个**（台湾常用字）∪ 三种语言的翻译文件和源码里实际出现的字符（含报告模板）∪ 全角符号区、注音符号、数学减号 U+2212 等，共 9895 个码位 |
| 与教育部字表的关系 | 要求的是教育部常用国字标准字体表（约 4808 字）。仓库里没有可核对来源的电子版，所以用台湾标准的 Big5 一级字（5401 字，据公开资料包含该字表的绝大部分）近似；没有逐字比对。超出部分（人名地名生僻字）由系统字体回退链补全 |
| 字形 | 三个字重（400/500/700）仍是同一族 Noto Sans SC，简繁界面字形统一。**取舍**：同一码位的字形是简体（SC）区域的写法，少数字（如“骨”“直”）与台湾标准字形有细微差别；若验收要求台湾标准字形，改用方案（b）另加 Noto Sans TC 子集（三个字重约 +5 MB，需按语言切换字体族，改动 `ui/theme/fonts.py`、样式表和报告渲染） |
| 体积 | 每个字重 2.45 MB → 3.29 MB，三个字重合计增加约 2.5 MB（Noto 7.35 MB → 9.86 MB，字体目录总计约 10 MB），小于 8 MB 的上限 |
| 许可 | 仍是 SIL OFL 1.1，`OFL-NotoSansSC.txt` 不变；名称表沿用原文件，保留保留字体名 “Source” 的声明 |
| 重新裁剪 | `scripts/build_fonts.py`：用 fontTools（只在开发期用，`uv run --with fonttools`）从 google/fonts 的可变字体 `NotoSansSC[wght].ttf` 先裁剪再固定字重。下载地址见脚本文件头 |
| 保证 | `tests/ui/test_traditional_chinese.py` 遍历繁体全部译文，断言每个字符都在内置字体的 cmap 里；还检查 GB2312 6763 字和 Big5 一级 5401 字全部在内 |

注意：翻译文件里新用的生僻字如果不在字体里，该测试会失败，请重新运行 `scripts/build_fonts.py`（字符集会自动包含新字符）。

PDF 里页脚的“内容摘要”标签以前用等宽字体（没有汉字）绘制，Qt 会回退到系统字体；现已改为标签用内置中文字体、摘要值用等宽字体，三种语言的 PDF 都只含内置字体（`NotoSansSC`、`IBMPlexMono`，测试检查）。

#### 报告和导出的语言

- 报告语言是 `ReportLanguage`（`zh_CN`、`zh_TW`、`en`），报告中心的“报告语言”下拉有三项，默认跟随界面语言；签署时按所选语言生成并写入 `reports.language`，**签署后固定**，已签署试验的预览、重新验签、导出都用签署时的语言。
- 报告文字不经过 `QCoreApplication`，直接读 `translations/gelinspect_<语言>.ts`（`services/reporting/texts.py`），缺译文时回退到简体中文并写警告日志。内置模板名和默认样品名（如“阴性对照”）在表格里也按报告语言翻译，用户自己输入的样品名保持原样。
- Excel 表头、枚举值、工作表名用界面当前语言（`ui/export_labels.py`），所以繁体界面导出的是繁体表头；用户填的样品名称、复核备注是保存的原文。
- 引擎和视频告警的概要（`Alert` 上下文）、内置模板名（`TemplateName`）、错误对话框（`Startup`、`ErrorDialog`）、首次启动和锁屏页、演示账号提示都在 `translations/` 里，繁体已覆盖。繁体和英文一样只显示告警概要，具体数值和原因见审计追踪（简体原文）。

#### 更新翻译的步骤

1. 在代码里新增或修改界面文字（用 `tr()`）。
2. `uv run python scripts/update_translations.py`：调用 `pyside6-lupdate` 重新提取，保留已有译文，删除已不存在的条目。
3. 打开 `translations/gelinspect_zh_TW.ts` 和 `translations/gelinspect_en.ts`，给新增的条目（`type="unfinished"`）填写译文，删掉 `type` 属性；也可以用 `uv run pyside6-linguist translations/gelinspect_zh_TW.ts`。繁体按上面的术语表和写法翻译；大量新增时可以用 OpenCC 打草稿（见下）。
4. `uv run python scripts/update_translations.py --check` 确认**三种语言**都没有缺失、多余或未翻译的条目（`zh_CN` 是源语言，只查缺失和多余；`zh_TW`、`en` 还查译文是否为空）。测试 `tests/ui/test_i18n.py` 和 `tests/ui/test_traditional_chinese.py` 也会检查。
5. 如果新译文里用了字体没有的字，运行 `scripts/build_fonts.py` 重新裁剪字体（见上）。
6. `QT_QPA_PLATFORM=offscreen uv run python scripts/check_english_ui.py --language zh_TW`（或 `en`）逐页扫描界面，列出没走 `tr()` 的文字：繁体下含简体独有字的、英文下含中文的。审计追踪的操作记录是保存的原文，会列出来，属于预期。
7. `.qm` 不需要手动生成；想手动生成：`uv run pyside6-lrelease translations/gelinspect_zh_TW.ts`。

**用 OpenCC 打繁体草稿（只在开发期，不是项目依赖）**：

```bash
uv run --no-sync --with opencc-python-reimplemented python scripts/draft_traditional.py
```

`scripts/draft_traditional.py` 只填还没翻译的条目（已有译文不覆盖），对每条源文本做 `OpenCC('s2twp').convert()`（简体转台湾繁体并换用语），再把 OpenCC 输出的直角引号改回弯引号“”，按术语表做替换（审計→稽核、模板→範本、校驗→驗證、幀→影格等 OpenCC 没覆盖或不统一的词），最后**逐条人工校对**。2026-11-07 首次翻译的 1166 条全部这样处理：机器草稿、术语替换、逐条通读并改写别扭的句子（如“上位機”改为“主機端”、“打成 ZIP”改为“壓縮成 ZIP”）。OpenCC 的已知误转要留意：“通过”会变成“透過”、“签的”会变成“籤”、“溯源码”会变成“溯原始碼”。

英文文字要短（繁体中文通常比简体略长，但不超过英文，已通过 1280×800 和 1024×700 的布局测试）：快捷栏在 1280 宽下要容纳全部 6 个操作，所以英文的“仪器设置”在快捷栏里写成 `Settings`，连接状态写成 `Connected` / `Disconnected`（完整说法在无障碍名称里）。

## 权限

权限矩阵见 [permissions.md](permissions.md)。P7 让它贯穿三层：

| 层 | 做法 |
|---|---|
| 界面 | 快捷栏按钮、侧栏项、“新建试验”按钮按 `session.can(permission)` 禁用或置灰；进入页面前 `navigate` 再判断一次 |
| 服务 | `AuthService.authorize(session, permission)`：会话必须已登录、没锁屏、不处于“必须改密”，并**重新读取用户**（账号被停用、改了角色立即生效），再查矩阵；拒绝时写 `permission_denied` 审计。`QueryService` 的每个入口先调用它；`ExperimentEngine` 的命令本来就校验权限 |
| 数据 | 仓储与权限无关 |

所以直接调用服务（界面被绕过）、伪造会话、锁屏中、账号停用后，都会被拒绝。

## 数据

| 内容 | 位置 |
|---|---|
| 账号、密码哈希（argon2）、必须改密标记 | `users` 表 |
| 审计 | `audit_events` 表。新增事件类型 `password_changed`、`screen_locked`、`screen_unlocked`、`permission_denied`；已有 `login`、`login_failed`、`logout` |
| 日志 | `data/logs/gelinspect.log` 与 `.1`–`.5` |
| 界面设置 | `QSettings("GelInspect", "GelInspect")`：`ui/language`、`ui/nav_mode`（macOS 在 `~/Library/Preferences`，Windows 在注册表） |
| 配置 | `security.idle_lock_minutes`（15）、`security.max_login_attempts`（5）、`security.lockout_minutes`（5）、`bootstrap.hook`、`bootstrap.options` |

## 测试

自动化（均离屏运行）：

| 文件 | 覆盖 |
|---|---|
| `tests/test_main.py` | 命令行参数；`--version` 不导入 Qt；首次启动创建 `data/`、数据库、日志、账号和模板；再次启动不重复初始化、不重置密码；真实配置没有任何账号；配置错、缺 `simulation/`、数据库被占用（真实加锁）的错误对话框和退出码；日志滚动；未捕获异常兜底；子进程冒烟 |
| `tests/services/test_auth.py` | 登录成功、失败、未知用户、锁定与解除、停用、退出、`authorize` 与审计、角色变更即时生效、锁屏与解锁、改密、必须改密、首个管理员、`QueryService` 权限与伪造会话 |
| `tests/services/test_instrument_monitor.py` | 监视线程启停、监听者异常隔离、引擎探测与运行互斥 |
| `tests/ui/test_main_window.py` | 登录页与演示账号、登录成功与失败、锁定倒计时、各角色入口禁用、拒绝越权导航、快捷栏导航、全部路由（每条路由都是真正的页面）、工作台实时数据、用户菜单、改密、锁屏与解锁、空闲计时、创建管理员与强制改密 |
| `tests/ui/test_bridge.py` | 事件来自工作线程、槽在主线程、顺序保持、`close` 后丢弃、真实引擎事件 |
| `tests/ui/test_shutdown.py` | 退出确认、取消不打断、确认后有序关闭并标记异常、锁屏时拒绝、无线程残留 |
| `tests/ui/test_i18n.py` | 切换后文字变化（登录页、外壳、屏幕上的错误提示）、语言菜单和登录页按钮有三项、持久化、`.ts` 完整且与源码同步、`.qm` 缺失或过期时自动编译、干净目录可切换、恰好三种语言 |
| `tests/ui/test_traditional_chinese.py` | 繁体 `.ts` 完整、占位符和首尾空格一致、没有简体独有字（GB2312 有而 Big5 没有）、引号只用“”、汉字旁没有半角标点、术语表和禁用词、**繁体全部译文的每个字符都在内置字体 cmap 里**、字体覆盖 GB2312 和 Big5 一级、字体体积和 OFL、三种语言循环切换和持久化、系统语言检测、默认语言只在没保存过选择时生效、窗口文字立即变繁体、标点辅助函数保持全角 |
| `tests/services/reporting/test_traditional_report.py` | 繁体 PDF 能生成、签名、验签，文字含繁体关键字、没有简体字；PDF 里只有内置字体，所有字符都在内置字体里；签署后语言固定；报告文字都有繁体译文 |
| `tests/services/test_excel_traditional.py` | 繁体界面导出的 Excel 表头、工作表名、枚举值是繁体，字符都在内置字体里 |
| `tests/ui/test_layout_languages.py` | 简体、繁体、英文 × 1280×800 与 1024×700 × 三种角色下，所有页面、登录、锁屏、对话框没有控件超界、被挤压或文字截断（检查器自检）；监控页和报告中心另有同样的三语言布局测试 |
| `tests/test_architecture.py` | `ui/` 只依赖 services、domain、interfaces；不碰数据库和线程；新服务不依赖 Qt 与 infrastructure |

`--smoke-test`（隐藏参数）：完整启动、创建数据库、加载后端、登录演示账号、浏览全部页面、切换语言、退出登录并关窗，返回码 0。干净克隆验证用它：

```bash
QT_QPA_PLATFORM=offscreen uv run gelinspect --smoke-test
```

需要人工验证（尚未验证）：

- 真实窗口：登录页和工作台的实际观感、触摸屏、高 DPI、窗口缩放；自动锁屏的真实计时（测试用注入时钟）。
- Windows：`uv run gelinspect` 启动、`QSettings` 写注册表、`lrelease.exe` 编译、中文路径。
- macOS 真机上的快捷键提示和语言菜单。
- 英文文案的措辞需要英文母语者或业务方审校；繁体中文的用词需要台湾用户审校（术语表是我方按台湾惯例确定，未经台湾业务方确认；字形见上文“字体”的取舍）。
- 发布包（P13）：构建时用 `lrelease` 生成 `.qm`，与 `.ts` 一起放进程序目录的 `translations/`；发布包里不再调用 `lrelease`，直接用构建时的 `.qm`。macOS 发布包已实际验证切换英文（冒烟测试）。
