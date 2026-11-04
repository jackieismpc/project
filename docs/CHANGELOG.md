# 变更日志

本文件记录项目的重要变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- P0：工程骨架，包括 uv 项目配置、ruff/mypy/pytest 工具配置、源码分层目录、命令行入口 `gelinspect --version`、冒烟测试与中文文档。
- P1：领域模型（实体、8×10 管位、试验状态机、权限矩阵、五类内置模板、三态映射）；算法、视频、设备、溯源接口契约；三个适配模板；算法契约测试和领域单元测试；架构、算法接口、试验生命周期和权限文档。
- P3：SQLite + SQLAlchemy 数据层、Alembic 迁移（不依赖 alembic.ini）、仓储接口与实现；SHA-256 哈希链审计日志，可定位被篡改或删除的第一条记录；`seed_defaults()` 写入内置模板和首次登录须改密的管理员。
- P4：`simulation/` 模拟数据目录（主代码不导入）：背光试管图合成器、`MockAnalyzer`、`SimDevice`、`SimVideoSource`、试剂白名单和示例数据，支持时间加速和故障注入。
- P2：Qt 设计系统（token、QSS 生成、内置 Noto Sans SC 子集与 IBM Plex Mono、Lucide 图标）和组件库（含 8×10 PlateGrid、TubeStrip、Sparkline）；组件展示页 `python -m gelinspect.ui.gallery`，支持离屏截图。
