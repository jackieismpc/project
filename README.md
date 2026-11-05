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
- 第一次启动会创建三个**演示账号**（密码公开，不要求改密），也显示在登录页的「演示账号」提示里，点一下就会填入：

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

> 当前状态：外壳、登录与权限、中英文切换已完成；工作台为骨架，其余页面显示「建设中」。开发进度见 [docs/PLAN.md](docs/PLAN.md)。

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
| `scripts/` | 开发脚本（更新翻译） |
| `data/` | 运行时数据（数据库、视频、图像、日志），不纳入版本库 |

## 文档

- [开发计划](docs/PLAN.md)
- [贡献指南](CONTRIBUTING.md)（注释规范、提交规范、阶段完成标准）
- [架构说明](docs/architecture.md)
- [算法接口](docs/algorithm-interface.md)
- [功能文档](docs/features/README.md)
- [用户手册](docs/user-manual.md)
- [部署说明](docs/deployment.md)
- [登录、权限与多语言](docs/features/auth-and-i18n.md)
- [变更日志](docs/CHANGELOG.md)
