# 部署说明

本文说明在 Windows 和 macOS 上运行和发布 GelInspect：仓库内一键启动、构建发布包、首次运行、数据目录、备份和已知限制。安全措施（密钥保护、审计链 HMAC、导出加密、完整性校验、授权接口）见 [features/security.md](features/security.md)。

## 1. 在仓库里一键启动

1. 安装 [uv](https://docs.astral.sh/uv/)：
   - macOS：`brew install uv`，或 `curl -LsSf https://astral.sh/uv/install.sh | sh`。
   - Windows（PowerShell）：`winget install --id=astral-sh.uv -e`，或
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`。
2. 取得代码后，在仓库目录运行 `uv run gelinspect`。第一次会自动准备 Python 3.11 并安装依赖（需要联网，约几百 MB）；之后可以离线运行。

不带参数的行为（P13 前后一致）：

| 项 | 行为 |
|---|---|
| 数据目录 | 仓库下的 `data/`（配置 `data_dir`、`--data-dir` 或环境变量 `GELINSPECT_DATA_DIR` 可改），首次启动自动创建 |
| 数据库 | `data/gelinspect.db`（SQLite，WAL），启动时自动运行迁移；数据库比程序新则拒绝启动并说明 |
| 日志 | `data/logs/gelinspect.log`，滚动，单个 1 MB，保留 5 份 |
| 后端 | 加载 `simulation/` 下的模拟设备、视频和算法，时间加速 60 倍 |
| 账号 | 首次启动创建三个演示账号，见 README |
| 密钥 | `data/keys/`：签名证书和私钥、审计链密钥。macOS 能访问钥匙串时用钥匙串保护，否则用安装密钥（见 security.md） |
| 完整性校验 | 开发模式没有清单，跳过并在日志里写明 |
| 界面设置 | 语言和侧栏模式保存在 `QSettings`（macOS 在 `~/Library/Preferences`，Windows 在注册表 `HKEY_CURRENT_USER\Software\GelInspect`） |

## 2. 构建发布包

发布包用 [Nuitka](https://nuitka.net/) 编译成本机代码，**standalone 目录模式**（不是单文件），以便 Qt 等 LGPL 库保持为可替换的动态库（见第 7 节）。

```bash
uv sync                                          # 含开发依赖组里的 Nuitka
uv run python scripts/build.py                   # 正式包：不含 simulation/，无演示账号
uv run python scripts/build.py --with-simulation # 演示包：带模拟后端和演示账号
```

| 选项 | 说明 |
|---|---|
| `--with-simulation` | 演示包。**正式包默认排除 `simulation/`**（不跟随导入，并检查编译报告里没有它的模块） |
| `--manifest-key KEY.pem` | 文件清单的 Ed25519 签名私钥；默认每次构建临时生成、不落盘（也可用环境变量 `GELINSPECT_MANIFEST_KEY`） |
| `--jobs N` | 并行编译数，默认 CPU 核数 |
| `--no-archive` | 只生成 `.app` 或目录，不打 dmg/zip |

构建过程：生成构建信息（版本、构建标识、清单公钥，编译进主程序）→ `lrelease` 生成 `.qm` → Nuitka 编译（约 5 分钟）→ 复制数据文件（迁移脚本、翻译、默认配置、演示包的模拟算法配置）→ 写第三方许可声明 → 检查变体 → 生成并签名文件清单 →（macOS）ad-hoc 签名 → 打包 → `dist/build-report-<变体>.json`（体积、耗时、SHA-256）。

发布包内容（程序目录）：

| 路径 | 内容 |
|---|---|
| 主程序 | macOS `GelInspect.app/Contents/MacOS/gelinspect-app`；Windows `GelInspect\GelInspect.exe` |
| `gelinspect/` | 字体（OFL）、Lucide 图标（ISC）、数据库迁移脚本（`.py`，Alembic 按文件加载） |
| `translations/` | `.ts` 和构建时生成的 `.qm` |
| `config/gelinspect.yaml` | 默认配置：正式包是「未接入」占位后端；演示包是模拟后端 + 演示账号 |
| `config/config.example.yaml` | 正式配置的样例 |
| `licenses/` | `THIRD_PARTY_NOTICES.txt`（全部运行时依赖的版本、许可证和许可文本）、`LGPL-3.0.txt`、`GPL-3.0.txt`、`LGPL-NOTICE.md`、字体 OFL、Lucide ISC、Nuitka 运行库例外、CPython 的 PSF 许可（`Python-LICENSE.txt`） |
| `gelinspect-manifest.json` / `.sig` | 文件清单和签名，启动时校验 |
| `PySide6/`、`cv2/`、`lib*.dylib` 等 | Qt、OpenCV（含 FFmpeg）等动态库，可替换 |

### 2.1 macOS

前提：Xcode 命令行工具（`xcode-select --install`），uv。在仓库目录：

```bash
uv sync
uv run python scripts/build.py                    # → dist/GelInspect.app、dist/GelInspect-<版本>-macos-arm64.dmg
uv run python scripts/build.py --with-simulation  # → dist/GelInspect Demo.app、…-demo.dmg
```

- 产物是 `.app`，再用 `hdiutil` 打成 `.dmg`（`hdiutil` 不可用时打 `.zip`）。只构建本机架构（Apple 芯片为 arm64）；Intel Mac 需要在 Intel Mac 上构建。
- **没有 Apple 开发者签名和公证**，只做了 ad-hoc 签名。从网上下载或拷贝来的 `.dmg`/`.app` 首次打开会被 Gatekeeper 拦下（「无法验证开发者」或「已损坏」）。放行步骤（任选其一）：
  1. 把 `GelInspect.app` 拖到「应用程序」，在 Finder 里**右键（或按住 Control 点按）→ 打开 → 打开**。只需一次。
  2. macOS 15 及以后：先双击一次被拦下，再到「系统设置 → 隐私与安全性」，在底部点「仍要打开」，输入密码确认。
  3. 终端：`xattr -dr com.apple.quarantine /Applications/GelInspect.app`。
- 需要正式分发时，用 Developer ID 证书签名并公证（Nuitka 的 `--macos-sign-identity`、`--macos-sign-notarization`），本仓库没有证书。
- 在禁止写 `/var/folders` 的受限沙盒里构建会因为 `xcrun` 不能写缓存而失败，要在普通终端里运行。Nuitka 自动下载的 ccache 是 x86_64 版，在 Apple 芯片上会让 `xcrun` 失败，所以 PATH 里没有 `ccache` 时脚本会关掉它（`brew install ccache` 后可加快重复构建）。

**macOS 实测**（2026-11-06，Apple 芯片 arm64，macOS 27，Nuitka 4.3.4，Python 3.11.15，`--jobs` 默认）：

| 项 | 正式包 | 演示包 |
|---|---|---|
| 构建耗时（全部 / 其中 Nuitka 编译） | 304 s / 294 s | 298 s / 290 s |
| `.app` 体积 | 387 MB（370 MiB） | 388 MB（370 MiB） |
| `.dmg` 体积 | 149 MB | 150 MB |
| 清单文件数、启动时校验耗时 | 245 个、0.10 s | 246 个、0.10 s |
| `--smoke-test`（全新数据目录） | 返回 0，约 1.0 s；进入「创建管理员」，用户表为空 | 返回 0，约 1.6 s；三个演示账号，登录、浏览全部页面、切换英文 |
| `--diagnose` | `simulation_importable: false`；编译报告里 0 个 `simulation` 模块 | `simulation_importable: true` |
| 改动包内一个 `.qm` 后 | `--diagnose` 报 `mismatch` 并列出文件；`--smoke-test` 返回 1；审计里有 `integrity_checked` | — |
| 钥匙串 | 普通终端：密钥以 `GIKP1:keychain:` 保存；受限沙盒里写钥匙串被拒，自动回退到安装密钥 | 同左 |

### 2.2 Windows

**开发机只有 macOS，Windows 包没有在本机构建或运行过**，需要在 Windows 上按下面的步骤实测。也可以用 GitHub Actions（见 2.3）。

前提：Windows 10/11 64 位；[uv](https://docs.astral.sh/uv/)；C 编译器——推荐 Visual Studio 2022 Build Tools（勾选「使用 C++ 的桌面开发」）。没有 MSVC 时 Nuitka 会提示下载 MinGW64（`--assume-yes-for-downloads` 已打开，需要联网）。

```powershell
uv sync
uv run python scripts/build.py                    # → dist\GelInspect\GelInspect.exe、dist\GelInspect-<版本>-windows-x64.zip
uv run python scripts/build.py --with-simulation  # → dist\GelInspect-Demo\…
dist\GelInspect\GelInspect.exe --diagnose          # 运行模式、数据目录、完整性校验
dist\GelInspect\GelInspect.exe --smoke-test --data-dir %TEMP%\gi-smoke   # 返回码 0
```

可选安装程序（[Inno Setup 6](https://jrsoftware.org/isinfo.php)）：

```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\windows\gelinspect.iss
```

生成 `dist\GelInspect-<版本>-windows-setup.exe`：按当前用户安装到 `%LOCALAPPDATA%\Programs\GelInspect`，不需要管理员权限；卸载不删除数据目录。没有代码签名证书，SmartScreen 会提示「Windows 已保护你的电脑」，点「更多信息 → 仍要运行」。

Windows 验证清单：zip 解压后双击能打开；正式包首次显示「创建管理员」；演示包三个演示账号能登录；切换英文；`%LOCALAPPDATA%\GelInspect` 下有数据库、日志、`keys\`（私钥以 `GIKP1:dpapi:` 开头）；`uv run pytest tests/infrastructure/security` 在 Windows 上会运行 DPAPI 测试；改动程序目录里任一 `.dll` 后启动出现「程序文件校验未通过」。

### 2.3 GitHub Actions

`.github/workflows/build.yml`：在 `windows-latest` 和 `macos-latest` 上各构建正式包和演示包，先跑 ruff 和 mypy，构建后运行 `--diagnose` 和 `--smoke-test`，Windows 正式包另外尝试 Inno Setup，最后上传 `dist/` 里的 zip、dmg、安装程序和构建报告。手动触发（Actions → build → Run workflow）或推送 `v*` 标签时运行。仓库目前只在本地提交，推送到 GitHub 后才能使用。

## 3. 首次运行

| 包 | 首次启动 |
|---|---|
| 正式包 | 显示「**创建管理员**」页，由部署人员设置管理员密码；数据库里没有任何默认账号。设备、相机、算法尚未接入时用「未接入」占位后端：仪器状态显示未连接，启动试验会明确报错（不会产生假结果），用户、模板、历史、审计、设置都能用 |
| 演示包 | 与 `uv run gelinspect` 相同：模拟后端、三个公开密码的演示账号。**不要用于真实检测** |

接入真实后端：不要改程序目录里的 `config/gelinspect.yaml`（会触发完整性告警），而是把完整配置写到**数据目录下的 `config.yaml`**（格式见程序目录的 `config/config.example.yaml` 和 `simulation/config.example.yaml`），或启动时 `--config` 指定。配置要点：

- `analysis.backend`、`video.source`、`device.backend` 改成真实实现的类路径；不要写 `bootstrap`（不要演示账号）。
- `security`：`idle_lock_minutes`（15）、`max_login_attempts`（5）、`lockout_minutes`（5）、`key_protection`（`auto`/`os`/`file`）、`integrity_check`（true）。
- `exports.encrypt`：敏感导出是否默认加密（也可在仪器设置里开）。
- `license.enabled`：授权检查，默认 false（只留接口，见 security.md）。
- 后端加载失败时报错并说明改哪一项，**不会回退到模拟实现**。

## 4. 数据目录

| 运行方式 | 默认位置 |
|---|---|
| 仓库（`uv run gelinspect`） | 仓库下的 `data/`（不变） |
| 发布包，Windows | `%LOCALAPPDATA%\GelInspect`（例如 `C:\Users\<用户>\AppData\Local\GelInspect`） |
| 发布包，macOS | `~/Library/Application Support/GelInspect` |

优先级：`--data-dir` > 环境变量 `GELINSPECT_DATA_DIR` > 配置文件的 `data_dir` > 上表默认位置。发布包**不依赖仓库路径**：资源从程序目录读取，数据只写到数据目录，不往程序目录写任何东西。

数据目录内容：`gelinspect.db`（及 `-wal`、`-shm`）、`logs/`、`keys/`（`report-signing.pem/.key`、`audit-chain.key`、可能有 `install.secret`）、`reports/`、`videos/`、`images/`、`masks/`、`user_settings.json`（仪器设置）、可选的 `config.yaml`（部署配置）和 `license.json`。

## 5. 备份与升级

- **备份**：关闭软件后复制整个数据目录。注意密钥：
  - 同一台电脑、同一系统用户下恢复：直接复制回去即可。
  - **换电脑或换系统用户**：DPAPI（Windows）和钥匙串（macOS）保护的私钥和审计链密钥在新环境里解不开。迁移前在原机器上用 `GELINSPECT_KEY_PROTECTION=file` 启动一次（密钥改用数据目录里的安装密钥保护），关闭后复制整个数据目录；在新机器上正常启动（`auto`）会自动改回系统凭据保护。没这样做时：已签署的报告仍可验签，但不能再签名（需要移走旧证书重新生成），审计链旧分段校验会报「密钥不可用」，软件会开新的一段继续记录。
  - 安装密钥模式下，备份里同时有密钥和密文，备份介质要妥善保管。
- **升级**：替换程序（重新解压或重新安装）后启动即可，迁移自动运行；P13 首次启动会把明文私钥加密、把审计链切换到 HMAC（都写审计）。降级不受支持：旧程序会拒绝打开新程序创建的数据库。
- 审计链可在审计页「校验完整性」，结果写明链的类型（SHA-256、HMAC-SHA256 或两段）。

## 6. 排错

启动失败会弹出对话框，说明原因和怎么办，技术细节和日志位置也在其中。发布包可以用 `--diagnose` 在终端打印运行模式、路径、配置和完整性校验结果。

| 现象 | 处理 |
|---|---|
| 配置有误 | 按提示修改配置文件；仓库里不带 `--config` 启动会使用默认的模拟配置 |
| 找不到 `simulation` | 仓库里恢复 `simulation/`；正式包本来就不含它，配置要指向真实后端或占位后端 |
| 数据库被占用 | 关闭另一个正在运行的 GelInspect 或其他打开数据库文件的程序 |
| 无法打开数据库或数据目录 | 检查数据目录是否存在、是否有读写权限、磁盘是否已满 |
| 「程序文件校验未通过」 | 程序目录被改动或损坏（审计里列出了文件）：重新解压或安装；按 LGPL 有意替换了 Qt 库时可设 `security.integrity_check: false` |
| `security.key_protection` 报错 | 配置为 `os` 但系统凭据不可用（无图形会话的 macOS、沙盒）：改成 `auto` |
| 签名时「无法解密私钥」 | 换了电脑或系统用户，见第 5 节 |
| 英文界面显示成中文 | 仓库：`.qm` 生成失败（看日志），运行 `uv sync`；发布包：检查 `translations/` 是否完整 |
| macOS「已损坏，无法打开」 | 见 2.1 的放行步骤 |

## 7. 许可证核实结论

### Nuitka（AGPL-3.0）——可以用于闭源发布

- PyPI 和包元数据把 Nuitka 标为 `GNU Affero General Public License v3`（本地 `nuitka-4.3.4.dist-info/METADATA`：`License: GNU Affero General Public License v3`，许可文件 `LICENSE.txt`、`LICENSE-RUNTIME.txt`、`NOTICE.txt`）。
- 同一个包里的 **`LICENSE-RUNTIME.txt`（Nuitka Runtime Library Exception, Version 1.0）** 是 AGPLv3 第 7 节下的附加许可：允许把 Nuitka 运行库与「独立模块」经编译过程组合成的目标代码（可执行文件或库）**以你选择的条款发布**；明确写明其目的是允许编译非 AGPL（包括专有）的 Python 程序，产物不必按 AGPLv3 授权。例外不覆盖 Nuitka 编译器本身（修改 Nuitka 再分发仍受 AGPL 约束）。Nuitka 仓库 README 的 License 一节也指向这份例外。
- 出处：本地 `.venv/lib/python3.11/site-packages/nuitka-4.3.4.dist-info/licenses/LICENSE-RUNTIME.txt`；上游 <https://github.com/Nuitka/Nuitka/blob/develop/LICENSE-RUNTIME.txt>、<https://github.com/Nuitka/Nuitka/blob/develop/README.rst>（License 一节）。
- 结论：**用 Nuitka，不改用 PyInstaller**。我们只使用、不修改、不分发 Nuitka 本身；发布包里附上了 `Nuitka-LICENSE-RUNTIME.txt`。Nuitka 构建时内联的 zlib、zstd 等组件是宽松许可证。Nuitka 只在 dev 依赖组里，不进运行时依赖。

### PySide6 / Qt（LGPL-3.0）——满足要求的做法

PySide6-Essentials、Shiboken6 的包元数据为 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`，我们按 LGPL-3.0 使用（只用 Essentials，不含 Addons 里的 GPL 模块）。LGPL-3.0 对分发的要求和本项目的做法：

| 要求 | 做法 |
|---|---|
| 用户能替换为修改过的库版本（第 4 节 d） | standalone **目录模式**：Qt 框架、PySide6/Shiboken6 的绑定扩展模块都是独立的动态库文件，可以替换；不用 onefile（onefile 会把库打进自解压包） |
| 随附 LGPL 和 GPL 全文、说明哪些部分受其约束 | `licenses/LGPL-3.0.txt`、`GPL-3.0.txt`（取自 gnu.org）、`LGPL-NOTICE.md`、`THIRD_PARTY_NOTICES.txt` |
| 提供对应源代码或书面承诺 | `LGPL-NOTICE.md` 给出了版本和上游源码位置；Qt 官方 FAQ 认为分发方要自己提供源码或书面承诺，只给上游链接不够——**书面承诺需要权利人补充，见第 8 节** |
| 不得限制用户为调试修改而逆向工程 | 软件许可条款里不要写禁止逆向 LGPL 库的条款（属于法务审核项） |

还有两点需要法务确认：

1. PySide6 的少量**纯 Python 部分**（`PySide6/__init__.py` 等）被 Nuitka 编译进了主程序；Qt 库本身和绑定扩展模块是独立文件。社区有观点认为 Nuitka 打包的 PySide6 难以完全满足「可替换」要求，没有官方结论。
2. opencv-python-headless 附带的 **FFmpeg 是 LGPL-2.1+**，同样以独立动态库随包，许可文本在 `THIRD_PARTY_NOTICES.txt`。

参考：<https://www.gnu.org/licenses/lgpl-3.0.html>；Qt 授权 FAQ 关于源代码义务的说明（PySide 邮件列表引用：<https://lists.qt-project.org/pipermail/pyside/2023-January/003256.html>）；社区讨论 <https://forum.qt.io/post/790144>。

## 8. 已知限制

- **Windows 包未在本机构建和运行**，脚本、工作流、Inno Setup 脚本都需要在 Windows 上实测（第 2.2 节清单）。DPAPI 代码只在 Windows 测试里运行。
- macOS 包没有 Apple 签名和公证；只构建本机架构。
- 应用图标是占位（PLAN 第 7 节第 7 项，名称和 Logo 未定），Nuitka 会提示没有指定图标。
- LGPL 源代码书面承诺、软件许可条款需要权利人和法务补充。
- 完整性校验只能发现改动、不能阻止；替换主程序可以绕过（见 security.md「不能防什么」）。
- 发布包体积较大，主要是 Qt 和 OpenCV（含 FFmpeg）的动态库。
