# 一键启动、登录权限与多语言

对应阶段：P7（外壳与权限）。代码：`src/gelinspect/startup.py`、`bootstrap.py`、`logging_setup.py`、`services/auth.py`、`services/queries.py`、`ui/main_window.py`、`ui/pages/`、`ui/bridge.py`、`ui/i18n.py`。

## 目的

让软件在仓库里一条命令就能用起来，并把「谁能做什么」「界面说什么语言」落到真实界面上：

- `uv run gelinspect` 不带参数即打开主窗口，首次启动自动初始化，启动失败有清楚的说明。
- 登录、失败锁定、空闲锁屏、改密，所有账号动作写审计。
- 权限在界面禁用入口，并在服务层再校验一次。
- 简体中文（源语言）和英文，运行中切换立即生效。

面向全部用户；创建管理员页面向首次部署的管理员。

## 流程

### 启动

```text
uv run gelinspect
 ├─ 读配置（默认 DEFAULT_CONFIG，指向 simulation/；--config 指定文件）
 ├─ 配置日志 data/logs/gelinspect.log（滚动 1 MB × 5）+ 未捕获异常兜底
 ├─ 创建 QApplication → 主题 → 语言（读取 QSettings，缺 .qm 时调用 lrelease 编译）
 ├─ build_app_context：加载后端 → 打开数据库（自动建 data/ 与迁移）→ 补全内置模板
 │    └─ 用户表为空：运行 bootstrap.hook（模拟配置创建三个演示账号）
 ├─ 启动恢复：停留在运行中的试验标记为异常
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

运行中未捕获的异常（主线程和工作线程）写日志；主线程的会弹出「程序遇到未处理的错误」，同一时间只弹一个。

### 首次运行与账号

| 配置 | 首次启动（用户表为空） |
|---|---|
| 默认 / 模拟（有 `bootstrap.hook`） | 钩子（`simulation/demo_accounts.py`）创建 `admin`、`experimenter`、`analyst`，密码公开、不要求改密；登录页的「演示账号」提示列出它们，点一下填入 |
| 真实（没有 `bootstrap.hook`） | 显示「创建管理员」页，用 `seed_defaults` 创建管理员，**首次登录强制改密**；数据库里没有任何默认账号和默认密码 |

再次启动时用户表不为空，不再运行钩子，内置模板只补缺失的，不重复写审计。主代码只持有钩子的类路径字符串（`interfaces/bootstrap.py` 定义 `BootstrapHook` 协议），不导入 `simulation/`。

「创建管理员」只在系统里没有任何账号时可用，`AuthService.create_first_admin` 在服务层校验，已有账号后拒绝。

### 登录、锁定与锁屏

1. 登录页输入用户名和密码，`AuthService.login` 用 argon2 校验。用户不存在时也做一次哈希运算，响应时间不泄露用户是否存在。
2. 连续失败 5 次（可配置）锁定 5 分钟（可配置）。锁定期间连正确的密码也拒绝，登录页显示倒计时并禁用输入。每次失败、锁定都写 `login_failed` 审计；成功写 `login`；退出写 `logout`。
3. 首次登录标记为「必须改密」的账号，登录后弹出不能取消的改密对话框（取消等于退出登录），改密前服务层拒绝一切其他操作。
4. 空闲（无鼠标、键盘、触摸）超过设定时长自动锁屏，默认 15 分钟，配置项 `security.idle_lock_minutes`，0 表示不锁。也可以从用户菜单手动「锁定屏幕」。锁屏时会话保留、服务层拒绝一切操作，**试验在后台继续运行**；输入密码解锁，失败同样计入锁定次数。锁屏时若有试验在进行，关窗被拒绝，必须先解锁。
5. 用户菜单（状态栏右侧）：修改密码、锁定屏幕、退出登录。

锁定计数保存在内存里，重启进程后清零（每次失败都有审计可事后追查）。

### 页面与导航

设计稿的 12 个页面都有路由（`ui/routes.py`）：登录、工作台、新试验、试验监控、单管详情、历史记录、模板管理、试剂溯源、报告中心、审计追踪、用户与权限、仪器设置；另有「关于」。

| 页面 | 状态 |
|---|---|
| 登录、锁屏、创建管理员 | 已实现 |
| 工作台 | 骨架：检测室温度（真实读数和曲线）、进行中的试验、最近结果、需要处理；数据经 `AppServices` 读取，没有数据显示空状态 |
| 关于 | 已实现：版本、运行环境、后端、开源许可 |
| 其余页面 | 统一「建设中」占位，说明将在哪个阶段实现（P8 新试验流程、P9 监控与结果、P11 报告、P12 数据管理页） |

快捷栏操作和侧栏菜单项导航到对应页面；无权限的入口置灰并提示「当前角色无此权限」。状态栏右侧的仪器连接状态、语言、当前用户都是真实数据：

- 仪器：空闲时由 `InstrumentMonitor` 每 2 秒通过 `ExperimentEngine.probe_device()` 探测，试验运行期间改用引擎的温度事件。`probe_device` 与运行中的试验互斥，不会与引擎线程同时访问设备。
- 语言：点击弹出语言菜单。
- 用户：显示名加角色，点击弹出用户菜单。

### 线程与桥接

`ui/` 只依赖 `services`，不直接碰数据库或引擎线程。`EngineBridge`（`ui/bridge.py`）订阅引擎事件和仪器状态，它们在 `engine-events`、`instrument-monitor` 线程上回调；桥发出内部信号，接收槽属于主线程，Qt 用排队连接投递，再按类型分发成带类型的信号（`stateChanged`、`alertRaised`、`temperatureChanged`、`wellUpdated`、`rowCountdown`、`instrumentChanged`）。`close()` 取消订阅并丢弃之后到达的事件。

### 多语言

- 源文本全部是简体中文，用 `tr()`（类内）或 `QCoreApplication.translate("上下文", "文字")` / `QT_TRANSLATE_NOOP`（类外）。带参数的文字用 `{name}` 占位，译文保持同样的占位符。
- 英文在 `translations/gelinspect_en.ts`；`gelinspect_zh_CN.ts` 由工具生成，译文留空，界面直接显示源文本。不做繁体。
- `.qm` 不入库。`LanguageManager` 启动时检查：缺失或比 `.ts` 旧，就调用 PySide6 自带的 `lrelease` 编译到 `translations/`（不可写时放到系统临时目录），所以干净克隆后直接运行就能切换语言。
- 切换：`LanguageManager.set_language` 安装或移除 `QTranslator`，Qt 向所有控件发 `LanguageChange`；每个有文字的组件在 `changeEvent` 里调用自己的 `retranslate`。选择保存在 `QSettings`（键 `ui/language`），下次启动恢复。
- 语言名「简体中文」「English」用各自的文字显示，不翻译。服务层的异常信息、审计记录、日志是简体中文（数据，不是界面）；界面按错误分类（`AuthErrorCode`）重新生成当前语言的提示。

#### 更新翻译的步骤

1. 在代码里新增或修改界面文字（用 `tr()`）。
2. `uv run python scripts/update_translations.py`：调用 `pyside6-lupdate` 重新提取，保留已有译文，删除已不存在的条目。
3. 打开 `translations/gelinspect_en.ts`，给新增的条目（`type="unfinished"`）填写英文译文，删掉 `type` 属性；也可以用 `uv run pyside6-linguist translations/gelinspect_en.ts`。
4. `uv run python scripts/update_translations.py --check` 确认没有缺失、多余或未翻译的条目（测试 `tests/ui/test_i18n.py` 也会检查）。
5. `.qm` 不需要手动生成；想手动生成：`uv run pyside6-lrelease translations/gelinspect_en.ts`。

英文文字要短：快捷栏在 1280 宽下要容纳全部 6 个操作，所以英文的「仪器设置」在快捷栏里写成 `Settings`，连接状态写成 `Connected` / `Disconnected`（完整说法在无障碍名称里）。

## 权限

权限矩阵见 [permissions.md](permissions.md)。P7 让它贯穿三层：

| 层 | 做法 |
|---|---|
| 界面 | 快捷栏按钮、侧栏项、「新建试验」按钮按 `session.can(permission)` 禁用或置灰；进入页面前 `navigate` 再判断一次 |
| 服务 | `AuthService.authorize(session, permission)`：会话必须已登录、没锁屏、不处于「必须改密」，并**重新读取用户**（账号被停用、改了角色立即生效），再查矩阵；拒绝时写 `permission_denied` 审计。`QueryService` 的每个入口先调用它；`ExperimentEngine` 的命令本来就校验权限 |
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
| `tests/ui/test_main_window.py` | 登录页与演示账号、登录成功与失败、锁定倒计时、各角色入口禁用、拒绝越权导航、快捷栏导航、全部路由、占位页、工作台实时数据、用户菜单、改密、锁屏与解锁、空闲计时、创建管理员与强制改密 |
| `tests/ui/test_bridge.py` | 事件来自工作线程、槽在主线程、顺序保持、`close` 后丢弃、真实引擎事件 |
| `tests/ui/test_shutdown.py` | 退出确认、取消不打断、确认后有序关闭并标记异常、锁屏时拒绝、无线程残留 |
| `tests/ui/test_i18n.py` | 切换后文字变化（登录页、外壳、占位页、屏幕上的错误提示）、持久化、`.ts` 完整且与源码同步、`.qm` 缺失或过期时自动编译、干净目录可切换、只有两种语言 |
| `tests/ui/test_layout_languages.py` | 中英文 × 1280×800 与 1024×700 × 三种角色下，所有页面、登录、锁屏、对话框没有控件超界、被挤压或文字截断（检查器自检） |
| `tests/test_architecture.py` | `ui/` 只依赖 services、domain、interfaces；不碰数据库和线程；新服务不依赖 Qt 与 infrastructure |

`--smoke-test`（隐藏参数）：完整启动、创建数据库、加载后端、登录演示账号、浏览全部页面、切换语言、退出登录并关窗，返回码 0。干净克隆验证用它：

```bash
QT_QPA_PLATFORM=offscreen uv run gelinspect --smoke-test
```

需要人工验证（尚未验证）：

- 真实窗口：登录页和工作台的实际观感、触摸屏、高 DPI、窗口缩放；自动锁屏的真实计时（测试用注入时钟）。
- Windows：`uv run gelinspect` 启动、`QSettings` 写注册表、`lrelease.exe` 编译、中文路径。
- macOS 真机上的快捷键提示和语言菜单。
- 英文文案的措辞需要英文母语者或业务方审校。
- 发布包（P13）需要把 `translations/` 作为数据文件带上，并在构建时先生成 `.qm`。
