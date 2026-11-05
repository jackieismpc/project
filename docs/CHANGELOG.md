# 变更日志

本文件记录项目的重要变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- P0：工程骨架，包括 uv 项目配置、ruff/mypy/pytest 工具配置、源码分层目录、命令行入口 `gelinspect --version`、冒烟测试与中文文档。
- P1：领域模型（实体、8×10 管位、试验状态机、权限矩阵、五类内置模板、三态映射）；算法、视频、设备、溯源接口契约；三个适配模板；算法契约测试和领域单元测试；架构、算法接口、试验生命周期和权限文档。
- P3：SQLite + SQLAlchemy 数据层、Alembic 迁移（不依赖 alembic.ini）、仓储接口与实现；SHA-256 哈希链审计日志，可定位被篡改或删除的第一条记录；`seed_defaults()` 写入内置模板和首次登录须改密的管理员。
- P4：`simulation/` 模拟数据目录（主代码不导入）：背光试管图合成器、`MockAnalyzer`、`SimDevice`、`SimVideoSource`、试剂白名单和示例数据，支持时间加速和故障注入。
- P2：Qt 设计系统（token、QSS 生成、内置 Noto Sans SC 子集与 IBM Plex Mono、Lucide 图标）和组件库（含 8×10 PlateGrid、TubeStrip、Sparkline）；组件展示页 `python -m gelinspect.ui.gallery`，支持离屏截图。
- 顶部快捷栏 `QuickToolbar`（6 个带权限和键盘快捷键的操作，窄屏收进「更多」）和应用外壳 `AppShell`（侧栏默认自动隐藏，可固定，模式持久化）。
- P5：视频管线：采集线程、带字节上限和 pin 保护的环形缓冲、实时预览、按排分段录制 MP4（SHA-256、丢帧计数）、翻转前后抓帧、黑屏/过曝/卡帧/断流检查和自动重连；输入支持视频文件、图片文件夹和 `load_row_capture`（暂不实现实时摄像头）；中文路径读写。4000×3000、10 fps 压测：约 10 fps，驱动丢帧 0，缓冲 504 MB 封顶，抓拍无丢帧。
- P6：试验引擎：10 排按截止时间调度，串联设备、视频和算法；算法超时只告警，`AnalyzerError` 不打断其他排并可重试；停止、设备故障、视频断流、温度持续越限都有明确行为并写审计；启动时把中断的试验标记为异常。工作单元让试验状态、单管结果、媒体记录和审计同一事务提交。配置按类路径加载后端，默认指向 `simulation/`。`DeviceController` 新增 `select_row`。
- P7：主窗口、登录、锁屏、权限（界面禁用加服务层校验）、简体中文和英文切换；`uv run gelinspect` 一键启动，首次自动初始化 `data/`，默认加载 `simulation/`，启动失败给出说明；退出有序关闭。模拟配置自动创建演示账号，真实配置首次启动创建管理员。其余页面暂为占位。
