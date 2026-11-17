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

### 启动方式

软件统一使用 `uv run gelinspect` 启动，账号快捷填入和试剂码示例默认可用。首次启动账号为：

| 用户名 | 密码 | 角色 |
|---|---|---|
| `admin` | `12345678` | 管理员 |
| `experimenter` | `12345678` | 普通用户（实验操作） |
| `analyst` | `12345678` | 普通用户（查看、实验与结果处理、输出和审计） |

权限已调整为开发人员、管理员、普通用户三类，普通用户由管理员在“用户管理”组合选择六组授权。实验与结果处理组包含配置、启动、停止、异常恢复、人工复核和报告签署。开发账号无默认密码，通过受控工具初始化；详见[三类账号与六组授权](docs/features/flexible-permissions.md)。本开发版本不迁移旧权限数据库，请使用空数据目录。

账号只在数据目录首次初始化时创建，已有数据目录保留原有账号和密码。

其他说明：

- 首次启动自动创建数据目录（仓库运行时是 `./data`，发布包使用平台标准位置或 `--data-dir` 指定的位置），运行数据库迁移并初始化。
- **数据存储**：试验、单管结果、复核、结论、试剂校验、报告记录、审计追踪、温度和设备日志全部存在数据目录下的一个 SQLite 数据库（`gelinspect.db`）。图像、录像和签署的 PDF 是文件，数据库里登记路径和 SHA-256。仪器设置写在数据目录的 `config.yaml`，界面语言和侧栏偏好由系统保存。
- 界面语言：简体中文、繁体中文（台湾用语）和英文，随时在顶部状态栏的语言菜单或登录页切换，立即生效并记住选择；系统语言是繁体中文时默认用繁体。报告（PDF）和 Excel 导出也支持三种语言。
- 空闲 15 分钟自动锁屏（试验在后台继续），可在配置里改。切换账号不需要重启：点右上角用户名选“退出登录”，再用另一个账号登录。

其他参数：

```bash
uv run gelinspect --config my.yaml   # 使用指定配置（完整配置，不与默认值合并）
uv run gelinspect --data-dir 目录    # 指定数据目录
uv run gelinspect --version          # 只打印版本号
```

启动失败（配置有误、必要资源缺失、数据库被占用等）会弹出说明原因的对话框，并写入 `data/logs/gelinspect.log`（滚动，单个 1 MB，保留 5 份）。部署和配置细节见 [部署说明](docs/deployment.md)。

> 开发计划见 [docs/PLAN.md](docs/PLAN.md)；日常使用见 [用户手册](docs/user-manual.md)；Windows 和 macOS 发布包见下文“发布包”。

## 发布包（Windows、macOS）

不想装 uv 的使用者用发布包：Nuitka 编译、目录模式，不依赖仓库。

```bash
uv run python scripts/build.py                    # Windows 或 macOS 单一发布包
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
| `simulation/` | 固定可重复的内部试验结果实现 |
| `tests/` | 自动化测试 |
| `docs/` | 计划、架构、功能文档与用户手册 |
| `translations/` | 界面翻译源文件（`.ts`）；`.qm` 在启动时自动生成，不纳入版本库 |
| `scripts/` | 开发脚本（更新翻译、繁体草稿、重新裁剪字体、构建发布包、验收指标测量、手册截图、未翻译文字检查） |
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
