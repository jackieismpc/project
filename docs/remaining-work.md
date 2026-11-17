# 剩余验证登记（2026-11-17）

- 等 item 8–13 并行修改停止、源码冻结并形成最终提交后，重新运行完整 `uv run pytest`、`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy` 和三语翻译检查；本轮因主 agent 调度要求未启动全量 pytest。此前全仓 mypy 剩 9 个 UI 类型错误，分布在 `appearance_dialog.py`、`main_window.py`、`test_templates_page.py`、`test_new_experiment.py`，已通知对应 owner，最终回归时复核是否清零。
- 使用最终冻结提交重新运行 Windows 构建，并确认 build ID 与提交/工作区干净状态一致；本轮完成的是并行编辑期间的预构建。当前目录包、ZIP 及 `dist/build-report.json` 是工具链与首次启动证据，不能作为最终提交制品。
- 在目标 Windows 干净环境验证 ZIP 展开和断网安装/首次运行；本轮 smoke test 使用联网环境及隔离临时数据目录，未测试断网。
- macOS 实机构建、首次启动、公证与真实签名仍未验证；Inno Setup 安装程序和 Windows 代码签名也未生成。
