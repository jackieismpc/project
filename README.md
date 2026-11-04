# 凝胶智检系统（GelInspect）

凝胶智检系统是一款离线运行的桌面软件，用于凝胶法细菌内毒素检测的试验管理、视频采集、算法判读、结果复核与报告输出。软件基于 Python 与 PySide6 开发，同时支持 Windows 与 macOS。

> 当前状态：开发暂停，已完成契约、数据层、模拟数据和界面组件库，尚无可运行的完整界面。开发进度见 [docs/PLAN.md](docs/PLAN.md)。

## 环境要求

- Python 3.11（由 `.python-version` 固定，`requires-python` 为 `>=3.11,<3.13`）
- [uv](https://docs.astral.sh/uv/)（用于创建环境、锁定依赖并运行命令）
- macOS 或 Windows 10/11 64 位

## 常用命令

```bash
# 安装依赖（含开发依赖组）
uv sync

# 查看版本号
uv run gelinspect --version

# 代码检查与格式化
uv run ruff check .
uv run ruff format --check .
uv run mypy

# 运行测试
uv run pytest

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
| `data/` | 运行时数据（数据库、视频、图像），不纳入版本库 |

## 文档

- [开发计划](docs/PLAN.md)
- [贡献指南](CONTRIBUTING.md)（注释规范、提交规范、阶段完成标准）
- [架构说明](docs/architecture.md)
- [算法接口](docs/algorithm-interface.md)
- [功能文档](docs/features/README.md)
- [用户手册](docs/user-manual.md)
- [部署说明](docs/deployment.md)
- [变更日志](docs/CHANGELOG.md)
