# 凝胶智检系统（GelInspect）

用于凝胶法细菌内毒素检测的实验管理、视频采集、算法判读、人工复核和报告输出。采用Python 3.11与PySide6，面向Windows与macOS，运行时离线。

## 启动与构建

安装uv后，在仓库目录运行：

```bash
uv sync
uv run gelinspect
```

只有一种启动／发行路径。首次准备依赖需要联网；依赖准备完成后可离线运行。默认账号可从登录页快捷填入，仅在新数据目录初始化：

| 账号 | 初始密码 | 能力 |
|---|---|---|
| `admin` | `12345678` | 管理员 |
| `experimenter` | `12345678` | 实验与结果处理 |
| `analyst` | `12345678` | 查看、实验与结果处理、输出、审计 |

已有目录保留原账号及数据。普通用户按六组授权，实验操作、复核和签署属于同一组；服务层重新检查权限。维护身份的受控初始化见待接入文档。

```bash
uv run gelinspect --config /absolute/config.yaml
uv run gelinspect --data-dir /absolute/data
uv run python scripts/build.py
```

自定义配置为完整配置，不与默认替代后端隐式合并。发行构建及产物测试仅在目标机执行，Mac开发环境本轮不构建。构建采用Nuitka目录模式：Windows输出EXE目录及ZIP，macOS输出APP及DMG（无hdiutil时为ZIP）；构建报告为`dist/build-report.json`。配置样例在`simulation/config.example.yaml`和`packaging/config/release.yaml`。目标平台验证和正式签名状态以未完成任务文档为准。

默认后端的接入边界、正式算法／设备／试剂接口需求见[待接入文档](docs/internal-integration-checklist.md)，不能用合成测试证明真实检测准确率和整机节拍。

## 当前操作约定

- 侧栏是工作台、历史记录、模板管理、试剂记录、报告中心、审计追踪；管理账号另有用户管理。顶部提供新建实验、报告中心、运行中的当前实验及设置。
- 新建实验可从模板或空白配置开始，支持逐管批号扫码、稳定排序和每管λ倍数。96管按12排×8支编号。
- 任何已有结果都可由获授权用户复核，原因必填，保留原算法结果及历次复核。已签署原件保留，后续复核或其他语言正式输出需生成并签署修订版。
- 三种界面语言为简体中文、繁体中文和英文；报告／导出采用操作开始时的界面语言。历史原文和已签署文件不随语言切换改写。
- 界面所有角色统一使用内置Noto Sans SC，个人颜色和字号按账号保存。手动与跟随系统字号均限制在12–22逻辑像素，窄窗口通过收纳、换行或滚动适配；报告字体独立。
- 空闲默认15分钟锁屏，实验继续运行；账号切换、撤权及停用会影响后续人工操作。

## 数据与维护

仓库运行默认使用`data/`；发行包默认使用Windows的`%LOCALAPPDATA%/GelInspect`或macOS的`~/Library/Application Support/GelInspect`。可用`--data-dir`或`GELINSPECT_DATA_DIR`指定目录。

签署报告采用原子不覆盖保存；报告目录需可写并支持同目录硬链接（默认本机APFS／NTFS满足这一要求）。不支持的目录会明确拒绝签署，不覆盖原件或偷偷回退。

数据库、媒体、签署报告、日志及密钥均属于业务数据。日志按大小归档，默认不自动删除。维护人员可导出带校验清单的诊断包。关闭软件后备份完整数据目录；跨电脑／系统用户迁移前必须验证密钥保护和恢复方案，不能只拷贝数据库。

启动故障记录到数据目录的`logs/gelinspect.log`。发行包可用`--diagnose`检查配置与完整性，`--smoke-test --data-dir 临时目录`检查隔离首次启动。不要把现有业务目录当作测试清理目录。

## 开发与验证

本轮Mac仅开发与静态检查；所有动态测试、构建及产物验证在目标机执行。以下前三项及翻译检查为静态检查，pytest命令仅供目标机使用。

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
QT_QPA_PLATFORM=offscreen uv run pytest
uv run python scripts/update_translations.py --check
```

Windows默认使用原生Qt后端，勿强制用offscreen测试打印。12MP视频压测可单独执行`uv run pytest -m video_perf -s`；真实钥匙串、DPAPI、打印和硬件验收按目标环境执行。新增界面文字须用显式翻译标记并补齐三语，详见[贡献指南](CONTRIBUTING.md)。

架构分为`ui → services → interfaces/domain`，基础设施和替代后端实现接口，组装根负责创建和注入。领域规则不依赖Qt或数据库；设备、算法、视频各有专用工作线程，Qt对象只在主线程操作。架构规则由`tests/test_architecture.py`及契约测试约束。报告渲染单独使用Qt打印设施。

## 文档

`docs/`仅保留两份当前文档，旧阶段计划、截图和设计稿由Git历史留存：

- [待接入文档](docs/internal-integration-checklist.md)：外部资料、适配器约定、算法可执行示例及交付依赖。
- [未完成任务](docs/remaining-work.md)：尚未完成的工作、目标机器跳过项、复查范围和最终验证结果。
