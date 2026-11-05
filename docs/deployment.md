# 部署说明

本文说明在 Windows 和 macOS 上运行 GelInspect。安装包（Nuitka）在 P13 补充。

## 在仓库里运行

1. 安装 [uv](https://docs.astral.sh/uv/)：
   - macOS：`brew install uv`，或 `curl -LsSf https://astral.sh/uv/install.sh | sh`。
   - Windows（PowerShell）：`winget install --id=astral-sh.uv -e`，或
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`。
2. 取得代码后，在仓库目录运行 `uv run gelinspect`。第一次会自动准备 Python 3.11 并安装依赖（需要联网，约几百 MB）；之后可以离线运行。

不带参数的行为：

| 项 | 行为 |
|---|---|
| 数据目录 | 仓库下的 `data/`（`data_dir` 可配置），首次启动自动创建 |
| 数据库 | `data/gelinspect.db`（SQLite，WAL），启动时自动运行迁移；数据库比程序新则拒绝启动并说明 |
| 日志 | `data/logs/gelinspect.log`，滚动，单个 1 MB，保留 5 份 |
| 后端 | 加载 `simulation/` 下的模拟设备、视频和算法，时间加速 60 倍 |
| 账号 | 首次启动创建三个演示账号，见 README |
| 界面设置 | 语言和侧栏模式保存在 `QSettings`（macOS 在 `~/Library/Preferences` 下的偏好设置文件，Windows 在注册表 `HKEY_CURRENT_USER` 的 `Software\GelInspect` 下） |

## 切换到真实设备

写一份**完整**的配置文件（不与默认值合并），格式见 `simulation/config.example.yaml`：

```bash
uv run gelinspect --config /path/to/real.yaml
```

要点：

- `analysis.backend`、`video.source`、`device.backend` 改成真实实现的类路径；`engine.time_scale` 删掉（真实设备不加速）。
- **删掉整个 `bootstrap` 段**。真实配置没有任何默认账号：首次启动时界面显示「创建管理员」页，由部署人员设置管理员密码，该管理员首次登录须再改一次密码。
- 可选 `security`：`idle_lock_minutes`（默认 15，0 表示不自动锁屏）、`max_login_attempts`（5）、`lockout_minutes`（5）。
- 后端加载失败时报错并说明改哪一项，**不会回退到模拟实现**。

## 备份与升级

- 备份：关闭软件后复制整个 `data/`（数据库、视频、图像、日志）。
- 升级：替换代码后启动即可，迁移自动运行。降级不受支持：旧程序会拒绝打开新程序创建的数据库。
- 审计链可在后续的审计页校验，数据库文件被改动能检出。

## 排错

启动失败会弹出对话框，说明原因和怎么办，技术细节和日志位置也在其中。常见情况：

| 现象 | 处理 |
|---|---|
| 配置有误 | 按提示修改配置文件；不带 `--config` 启动会使用默认的模拟配置 |
| 找不到 `simulation` | 恢复 `simulation/` 目录，或改用真实后端的配置 |
| 数据库被占用 | 关闭另一个正在运行的 GelInspect 或其他打开数据库文件的程序 |
| 无法打开数据库或数据目录 | 检查 `data/` 是否存在、是否有读写权限、磁盘是否已满 |
| 英文界面显示成中文 | `.qm` 生成失败（看日志），运行 `uv sync` 确认 PySide6 完整，再重新启动 |

## 验证清单

在干净的克隆里（没有 `.venv` 和 `data/`）：

```bash
QT_QPA_PLATFORM=offscreen uv run gelinspect --smoke-test   # 返回码 0
```

在 Windows 上由使用者运行 `uv run gelinspect`，逐项确认：能打开登录页；三个演示账号能登录；切换英文立即生效；工作台显示温度；锁屏与解锁；关窗退出；`data/` 下有数据库和日志。

## 发布包（P13）

安装包会把 `translations/` 作为数据文件带上，并在构建时先用 `lrelease` 生成 `.qm`；发布包可以选择不包含 `simulation/`，此时配置必须指向真实后端且不写 `bootstrap`。
