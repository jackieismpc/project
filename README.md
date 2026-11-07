# 凝胶智检系统（GelInspect）

凝胶智检系统是一款离线运行的桌面软件，用于凝胶法细菌内毒素检测的试验管理、视频采集、算法判读、结果复核与报告输出。软件基于 Python 与 PySide6 开发，同时支持 Windows 与 macOS。

## 一键启动

只需要 [uv](https://docs.astral.sh/uv/)，Python 3.11 和全部依赖由它自动准备：

```bash
# 1. 安装 uv（一次即可）
#    macOS：  brew install uv    或    curl -LsSf https://astral.sh/uv/install.sh | sh
#    Windows（PowerShell）：  winget install --id=astral-sh.uv -e
#             或    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. 在仓库目录里启动（第一次会自动下载依赖，需要联网；之后离线可用）
uv run gelinspect
```

不带任何参数即打开主窗口：

- 首次启动自动创建 `data/`（数据库、日志），运行数据库迁移并初始化，默认加载 `simulation/` 下的模拟设备、模拟视频和模拟算法（时间加速 60 倍）。
- 第一次启动会创建三个**演示账号**（密码公开，不要求改密），也显示在登录页的“演示账号”提示里，点一下就会填入：

  | 用户名 | 密码 | 角色 |
  |---|---|---|
  | `admin` | `Admin@2026` | 管理员 |
  | `experimenter` | `Experimenter@2026` | 实验员 |
  | `analyst` | `Analyst@2026` | 分析员 |

  账号定义在 `simulation/demo_accounts.py`，由配置里的 `bootstrap.hook` 引用。**真实配置不写这一项**：首次启动时界面会要求创建管理员并设置密码，数据库里不会有任何默认密码。
- 界面语言：简体中文和英文，随时在顶部状态栏的语言菜单或登录页切换，立即生效并记住选择。
- 空闲 15 分钟自动锁屏（试验在后台继续），可在配置里改。

可选参数：

```bash
uv run gelinspect --config my.yaml   # 使用指定配置（完整配置，不与默认值合并）
uv run gelinspect --simulate         # 显式使用默认的模拟配置，与不带参数相同
uv run gelinspect --version          # 只打印版本号
```

启动失败（配置有误、`simulation/` 缺失、数据库被占用等）会弹出说明原因的对话框，并写入 `data/logs/gelinspect.log`（滚动，单个 1 MB，保留 5 份）。部署和配置细节见 [部署说明](docs/deployment.md)。

> 开发计划见 [docs/PLAN.md](docs/PLAN.md)；日常使用见 [用户手册](docs/user-manual.md)；Windows 和 macOS 发布包见下文“发布包”。

## 发布包（Windows、macOS）

不想装 uv 的使用者用发布包：Nuitka 编译、目录模式，不依赖仓库。

```bash
uv run python scripts/build.py                    # 正式包：不含 simulation/，首次启动“创建管理员”
uv run python scripts/build.py --with-simulation  # 演示包：模拟后端 + 演示账号
```

- macOS 生成 `dist/GelInspect.app` 和 `.dmg`（没有 Apple 签名，首次打开要右键“打开”放行）；Windows 生成 `dist\GelInspect\GelInspect.exe` 和 `.zip`，可选 Inno Setup 安装程序。Windows 包也可以用 GitHub Actions（`.github/workflows/build.yml`）构建。
- 发布包的数据放在平台标准位置：Windows `%LOCALAPPDATA%\GelInspect`，macOS `~/Library/Application Support/GelInspect`（可用 `--data-dir` 或 `GELINSPECT_DATA_DIR` 改）。仓库里 `uv run gelinspect` 仍用 `./data`。
- 基础防护：签名私钥和审计链密钥由系统凭据（DPAPI / 钥匙串）加密保护，审计链改用 HMAC-SHA256，敏感导出可选 AES-256 加密 ZIP，启动时按签名清单校验程序文件。能防什么、不能防什么见 [安全与基础防护](docs/features/security.md)。

步骤、首次运行、备份和许可证结论见 [部署说明](docs/deployment.md)。

## 环境要求

- Python 3.11（由 `.python-version` 固定，`requires-python` 为 `>=3.11,<3.13`，uv 会自动准备）
- [uv](https://docs.astral.sh/uv/)（用于创建环境、锁定依赖并运行命令）
- macOS 或 Windows 10/11 64 位

## 开发常用命令

```bash
# 安装依赖（含开发依赖组）
uv sync

# 代码检查与格式化
uv run ruff check .
uv run ruff format --check .
uv run mypy

# 运行测试（无显示器时加 QT_QPA_PLATFORM=offscreen）
uv run pytest

# 修改了界面文字后，更新翻译（见 docs/features/auth-and-i18n.md）
uv run python scripts/update_translations.py

# 可选：安装 ONNX 推理依赖
uv sync --extra inference
```

## 目录结构

| 路径 | 说明 |
|---|---|
| `src/gelinspect/` | 应用源码，分为 ui、services、domain、interfaces、infrastructure 五层 |
| `simulation/` | 模拟数据与模拟实现，与主代码隔离 |
| `tests/` | 自动化测试 |
| `docs/` | 计划、架构、功能文档与用户手册 |
| `translations/` | 界面翻译源文件（`.ts`）；`.qm` 在启动时自动生成，不纳入版本库 |
| `scripts/` | 开发脚本（更新翻译、构建发布包、验收指标测量、手册截图、英文界面检查） |
| `packaging/` | 发布包的入口、默认配置、许可文本、Inno Setup 脚本 |
| `data/` | 运行时数据（数据库、视频、图像、日志），不纳入版本库 |

## 文档

- [开发计划](docs/PLAN.md)
- [贡献指南](CONTRIBUTING.md)（注释规范、提交规范、阶段完成标准）
- [架构说明](docs/architecture.md)
- [算法接口](docs/algorithm-interface.md)
- [功能文档](docs/features/README.md)
- [用户手册](docs/user-manual.md)
- [验收指标报告](docs/acceptance-report.md)
- [交接说明](docs/handover.md)（算法、硬件、甲方 QA）
- [部署说明](docs/deployment.md)
- [登录、权限与多语言](docs/features/auth-and-i18n.md)
- [安全与基础防护](docs/features/security.md)
- [变更日志](docs/CHANGELOG.md)
