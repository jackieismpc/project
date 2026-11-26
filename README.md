# 凝胶智检系统（GelInspect）

用于凝胶法细菌内毒素检测的实验管理、视频采集、算法判读、人工复核和报告输出。采用Python 3.11与PySide6，面向Windows与macOS，运行时离线。

## 启动与构建

安装uv后，在仓库目录运行：

```bash
uv sync --frozen
uv run gelinspect
```

只有一种启动／发行路径。首次准备依赖需要联网；依赖准备完成后可离线运行。默认账号可从登录页快捷填入，仅在新数据目录初始化：

| 账号 | 初始密码 | 能力 |
|---|---|---|
| `admin` | `12345678` | 管理员 |
| `experimenter` | `12345678` | 实验与结果处理 |
| `analyst` | `12345678` | 查看、实验与结果处理、输出、审计 |
| `developer` | `12345678` | 全部权限，包括版权方配置（随新数据目录自动创建，不显示在登录页快捷填入与普通用户管理列表中） |

已有目录保留原账号及数据。普通用户按六组授权，实验操作、复核和签署属于同一组；服务层重新检查权限。维护身份的受控初始化见待接入文档。

### 开发人员账号与权限测试

演示模式下新数据目录首次初始化会自动创建 `developer` 账号，初始密码为 `12345678`（不会显示在登录页快捷填入卡片中，也不会出现在普通用户管理列表里）。

若为无 `DemoAccounts` 初始化钩子的正式环境，或已在旧数据目录中缺少 `developer` 账号，可先关闭软件，再对**实际要登录的数据目录**执行一次受控创建工具：

```powershell
# 测试安装后的Windows程序：使用实际安装目录及当前数据目录
$gelinspectInstallDir = "$env:LOCALAPPDATA\Programs\GelInspect"
uv run python scripts/provision_developer.py --data-dir "$gelinspectInstallDir\data" --username developer
```

命令会要求两次输入自定密码，至少8位且不能与用户名相同；之后用`developer`和该密码在登录页登录。安装时选了其他位置，请修改`$gelinspectInstallDir`；如果已在设置中更改数据目录，`--data-dir`必须指向更改后的实际目录。源码运行默认数据目录是仓库的`data`，对应命令为`uv run python scripts/provision_developer.py --data-dir ./data --username developer`。macOS安装包默认目录对应`--data-dir "$HOME/Library/Application Support/GelInspect"`。账号只存在于指定目录中。已有开发人员账号时命令拒绝覆盖，请使用原先设定的密码。

| 测试身份 | 建议核查 |
|---|---|
| `developer` | 全权限；可修改版权方名称，普通账号管理不能查看或修改此身份 |
| `admin` | 可管理账号、模板和仪器设置；版权方配置受限 |
| `experimenter` | 可查看、运行、复核和签署；默认不可管理模板、配置仪器或导出打印 |
| `analyst` | 具备实验、输出和审计能力；默认不可管理模板或配置仪器 |
| 管理员另建的普通用户 | 分别只授予查看、实验、模板、输出、审计、维护六组之一，再测试组合；无授权时应无业务能力 |

重点检查菜单可见性、按钮可用性和实际提交时的拒绝行为；已登录账号被撤权／停用后，后续操作也应受限。个人配色和字号是账号偏好，不因缺少仪器维护权限而禁用。

### 自定义启动与构建入口

```bash
uv run gelinspect --config /absolute/config.yaml
uv run gelinspect --data-dir /absolute/data
uv run python scripts/build.py
```

自定义配置为完整配置，不与默认替代后端隐式合并。发行构建及产物测试仅在目标机执行，Mac开发环境本轮不构建。构建采用Nuitka目录模式：Windows输出EXE目录及ZIP，macOS输出APP及DMG（无hdiutil时为ZIP）；构建报告为`dist/build-report.json`。配置样例在`simulation/config.example.yaml`、`simulation/config.inspection.yaml`（本地`inspection/`真实算法样本回放）、`packaging/config/hikvision.yaml`（海康工业相机现场配置）、`packaging/config/production.yaml`（海康相机 + `yolo_onnx` 真实推理生产配置）、`packaging/config/analyzer.yolo_onnx.yaml` 和 `packaging/config/release.yaml`。生产权重文件需手动放置在运行目录的 `ckpt/gel-seg-v1.onnx`（仓库不跟踪 `ckpt/`；Windows GPU 环境如使用 CUDA 需自行安装 `onnxruntime-gpu`）。`--diagnose` 同时输出海康 MVS SDK 跨平台自动发现状态（`hikvision_sdk`）及 ONNX Runtime / 模型状态（`onnxruntime`、`analysis_backend`、`analysis_model`）。目标平台验证和正式签名状态以未完成任务文档为准。

默认后端的接入边界、正式算法／设备／试剂接口需求见[待接入文档](docs/internal-integration-checklist.md)，不能用合成测试证明真实检测准确率和整机节拍。

### 使用 `inspection/` 真实算法样本回放

> 注意：设置页中的 **“视频输入 → 图片模式”**（`images`）仅用指定文件夹的图片替换摄像头视频帧，判读仍运行默认模拟算法 `MockAnalyzer`；若要同时使用 `inspection/` 中的真实算法结果 JSON、掩码和拼接图进行全流程联调，必须通过配置启用成对的 `InspectionVideoSource` 与 `InspectionReplayAnalyzer`。
>
> 当前开发阶段，`inspection/` 已临时纳入仓库，并在 `scripts/build.py` 编译打包时自动打包进发布包目录，同时生成 `config/config.inspection.yaml` 供直接切换（正式版本发布前将统一清理）。

1. **源码开发运行**：直接指定回放配置启动：

   ```bash
   uv run gelinspect --config simulation/config.inspection.yaml
   ```

2. **编译打包后运行（最简便方式）**：
   - 打开软件 → 进入 **设置 → 启动配置与数据目录 → 配置文件**；
   - 选择程序安装目录下的 `config/config.inspection.yaml`（“检测配置文件（可选）”留空或选择 `simulation/analyzer.inspection.yaml`）；
   - 关闭并重启软件，即可直接加载随包打包的 `inspection/` 样本进行全流程联调。
   - 如需切回默认模拟数据：清空设置页中的“配置文件”或改选 `config/gelinspect.yaml` 后重启。
   - 也支持命令行直接启动：`& "C:\...\GelInspect.exe" --config "C:\...\config\config.inspection.yaml"`；若需在外部使用独立更新的 `inspection/` 目录，可参考 `simulation/config.inspection.yaml` 在外部新建配置文件并将 `analysis.options.inspection_dir` 与 `video.options.inspection_dir` 指向该目录（**不要**直接修改安装目录下的文件，以免触发完整性校验）。

## 当前操作约定

- 侧栏是工作台、历史记录、模板管理、试剂记录、报告中心、审计追踪；管理账号另有用户管理。顶部提供新建实验、报告中心、运行中的当前实验及设置。
- 新建实验可从模板或空白配置开始，点击孔板增删试管，也可在样品表右键删除选中的试管；点击“序号／管号”标题排序，再次点击切换升降序。支持按序连续扫码和单行覆盖扫码，每管λ倍数独立配置。确认页默认直接启动；配置相对来源模板有实质改变时，有权限的用户可另存新模板后启动。仅改批号或排序不会启用保存模板；调整不覆盖原模板。96管按12排×8支编号。
- 实验结束后，具有模板管理权限的用户可将本次保存的配置另存为新模板，保留物理管位和λ定义。开发阶段不兼容还原旧实验；缺少完整配置快照时不能另存。
- 内置模板只提供起始配置，实际方法和管数由使用人员确定；软件记录逐管结果和人工复核，不按固定药典布局自动判断整次实验是否合规。自定义模板记录创建人和创建时间，后续编辑保留原创建信息。
- 任何已有结果都可由获授权用户复核，原因必填，保留原算法结果及历次复核。已签署原件保留，后续复核或其他语言正式输出需生成并签署修订版。
- 三种界面语言为简体中文、繁体中文和英文；报告／导出采用操作开始时的界面语言。历史原文和已签署文件不随语言切换改写。
- 界面所有角色统一使用内置Noto Sans SC；设置页直接提供个人外观、仪器和数据配置。外观选择后按账号自动保存并生效，无单独保存按钮或预览弹窗；配色仅提供石青、沉静蓝两套完整风格，字号仅提供跟随系统、较小、较大三项；报告字体独立。
- 仪器设置在输入完成并校验通过后自动保存，启动路径选择同样自动保存、重启生效；不再提供保存和还原按钮。工作台四卡片随窗口布局，最近结果和待办在卡片内滚动，顶部快捷栏提供新建实验。
- 空闲默认15分钟锁屏，实验继续运行；账号切换、撤权及停用会影响后续人工操作。

## 数据与维护

仓库运行默认使用`data/`；Windows发行包首次默认使用**实际安装目录下的`data/`**（便携ZIP为EXE所在目录下的`data/`），macOS仍使用`~/Library/Application Support/GelInspect`。Windows安装向导允许更改安装位置，默认位置为当前用户的`%LOCALAPPDATA%\Programs\GelInspect`。

安装后在“设置 → 启动配置与数据目录”选择可写的数据目录，校验通过即自动保存，关闭并重新启动软件后使用新位置；实验运行中不能切换。切换位置不会自动搬移、合并或删除原数据，新空目录会初始化为独立数据集。需要保留原历史时，先关闭软件，按备份恢复方案复制完整数据（包括密钥、媒体及报告），再切换并验证。也可用`--data-dir`或`GELINSPECT_DATA_DIR`显式指定目录。

安装目录和默认`data/`须允许当前用户写入，不要求日常以管理员身份启动。若用户选择受保护的安装位置，须先配置目录权限，或安装到用户可写的位置；无法写入时程序应明确报错，不静默换用另一个数据目录。Windows的启动路径记忆文件独立保存在`%LOCALAPPDATA%\GelInspect\runtime-selection.json`，用于重启后定位已选择的数据目录。

首启因默认目录不可写而无法进入设置时，可临时指定另一个可写目录启动，再在设置中选定该目录以保存下次启动位置：

```powershell
# 替换为自己的安装路径和数据路径
& "$gelinspectInstallDir\GelInspect.exe" --data-dir "D:\GelInspectData"
```

数据目录可放在程序目录外，或安装目录的`data/`内；不允许把程序目录本身、字体／翻译／模块等资源目录混作业务数据目录。

签署报告采用原子不覆盖保存；报告目录需可写并支持同目录硬链接（默认本机APFS／NTFS满足这一要求）。不支持的目录会明确拒绝签署，不覆盖原件或偷偷回退。

数据库、媒体、签署报告、日志及密钥均属于业务数据。诊断日志按本地日期保存；默认全部保留，设置有限保留天数后仅按受控规则清理。维护人员可按日期导出带校验清单的诊断包，详见 [诊断与数据保留](docs/diagnostics.md)。关闭软件后备份完整数据目录；跨电脑／系统用户迁移前必须验证密钥保护和恢复方案，不能只拷贝数据库。

启动故障记录到数据目录的`logs/gelinspect.log`。发行包可用`--diagnose`检查配置与完整性，`--smoke-test --data-dir 临时目录`检查隔离首次启动。不要把现有业务目录当作测试清理目录。

## 开发与验证

本轮Mac仅开发与静态检查；所有动态测试、构建及产物验证在目标机执行。开发机只执行以下静态检查；目标机完整流程见下一节。

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/update_translations.py --check
```

新增界面文字须用显式翻译标记并补齐三语，详见[贡献指南](CONTRIBUTING.md)。

架构分为`ui → services → interfaces/domain`，基础设施和替代后端实现接口，组装根负责创建和注入。领域规则不依赖Qt或数据库；设备、算法、视频各有专用工作线程，Qt对象只在主线程操作。架构规则由`tests/test_architecture.py`及契约测试约束。报告渲染单独使用Qt打印设施。

## 目标机器：全量测试、构建与安装

以下步骤在实际交付平台、架构的测试机器上执行。Windows命令使用PowerShell；构建不能跨平台替代目标机验证。准备Git、uv、Python 3.11及可供Nuitka使用的C编译工具链（Windows可准备MSVC C++构建工具，macOS需Xcode命令行工具）。首次下载依赖及构建工具需要联网。正式报告还需目标系统具备合法可用的SimSun和Times New Roman字体。

### 1. 准备源码和依赖

在单独测试目录检出待验版本。发布验收应固定提交，不要一边测试一边更新分支：

```powershell
git clone --branch feat/client-feedback-20261116 https://github.com/jackieismpc/project.git GelInspect-source
cd GelInspect-source
git rev-parse HEAD
git status --short
uv python install 3.11
uv sync --frozen --python 3.11
New-Item -ItemType Directory -Force artifacts
```

已有测试检出可直接使用，先确认提交正确且工作区干净；不要覆盖其他人的未提交改动。测试使用临时目录，不使用业务数据目录。保留提交号、系统／架构、依赖安装结果和后续测试日志。

### 2. 运行完整自动化与覆盖率

在有桌面会话的Windows目标机执行，不强制设置Qt离屏模式。`GELINSPECT_PERF_VIDEO=1`将默认跳过的12MP视频压力项纳入全量运行；普通性能测试本来就在全量测试中。

```powershell
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
$env:GELINSPECT_PERF_VIDEO = "1"
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/update_translations.py --check
uv run --with coverage coverage run --branch --source=gelinspect,simulation -m pytest -ra --junitxml=artifacts/pytest.xml
uv run --with coverage coverage report -m
uv run --with coverage coverage html -d artifacts/coverage
Remove-Item Env:GELINSPECT_PERF_VIDEO -ErrorAction SilentlyContinue
```

每条检查都应成功退出；有失败先修复再复验，不继续将该源码作为已验收版本构建。`-ra`会列出失败及跳过项，需逐项记录原因；“全量运行”不表示平台专属、真实硬件或凭据测试已全部通过。覆盖率报告在`artifacts/coverage/index.html`，JUnit结果在`artifacts/pytest.xml`，不要把未执行分支写成已覆盖。

macOS目标机使用同一套检查和覆盖率命令；准备目录使用`mkdir -p artifacts`，视频压力环境变量使用`export GELINSPECT_PERF_VIDEO=1`，结束后`unset GELINSPECT_PERF_VIDEO`。仓库测试在macOS默认离屏运行，因此还须用原生桌面做下文的人工验收；当前Mac开发机不执行这些动态步骤。自动化默认使用测试文件密钥保护，不替代真实DPAPI／钥匙串验收。

### 3. 在目标机生成发行包

自动化及必要人工验收通过后，从同一份干净源码构建：

```powershell
uv run python scripts/build.py
Get-Content dist/build-report.json
```

Windows生成`dist/GelInspect/GelInspect.exe`及完整目录ZIP；macOS生成`dist/GelInspect.app`及DMG，无法生成DMG时使用ZIP。具体压缩包名称、构建ID和SHA-256记录在`dist/build-report.json`。构建会生成翻译和完整性清单；不要只拷贝EXE，也不要手改构建后的文件。最终用户使用完整发行包，无需再安装Python或uv。

不要一边运行`dist/`里的程序一边重新构建。若曾在旧Windows产物旁生成`data/`，构建会拒绝覆盖非空数据目录；先关闭旧程序，备份并移出数据，再重新构建，不能用删除整个`dist/`的方式绕过保护。

在Windows上检查产物，使用全新临时数据目录：

```powershell
$gelinspectCheckDir = Join-Path $env:TEMP ("GelInspect-check-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $gelinspectCheckDir
& .\dist\GelInspect\GelInspect.exe --diagnose --data-dir (Join-Path $gelinspectCheckDir "diagnose")
& .\dist\GelInspect\GelInspect.exe --smoke-test --data-dir (Join-Path $gelinspectCheckDir "smoke")
```

两项均须成功退出。macOS对应程序路径是`dist/GelInspect.app/Contents/MacOS/gelinspect-app`，可用`mktemp -d`建立临时目录后执行同样的`--diagnose`和`--smoke-test`。这些检查仅验证配置、完整性及隔离启动，不能替代实际操作和硬件验收。

### 4. 安装并以普通用户启动

Windows可直接将整个ZIP解压到独立程序目录，运行其中的`GelInspect.exe`。如需开始菜单和卸载入口，在构建机安装Inno Setup 6，再生成安装程序：

```powershell
$gelinspectVersion = uv run python -c "import gelinspect; print(gelinspect.__version__)"
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DAppVersion=$gelinspectVersion" packaging\windows\gelinspect.iss
```

运行`dist/GelInspect-<版本>-windows-setup.exe`，在“选择安装位置”页保留默认目录或选择自己的目录，再从开始菜单以普通用户启动。首次默认数据保存在所选安装位置下的`data/`，随后可在设置中改数据存储位置；已保存的明确路径选择优先。安装器支持当前用户安装，升级和卸载均保留业务数据，切勿手动把含`data/`的整个程序目录删除。macOS打开DMG，将完整`GelInspect.app`复制到“应用程序”后启动；当前脚本仅做ad-hoc签名，正式交付仍需Developer ID签名与公证，不将关闭系统安全检查作为安装步骤。

首次使用按上文账号表登录，按交付方案修改初始密码并配置用户权限。核对仪器编号、数据目录、外观、报告版权方和视频源。设置完成编辑后自动保存；启动路径变更须重启生效。默认模拟已加速至600倍，60分钟孵育约6秒，后续采集、判读仍另需时间。正式使用前关闭软件并备份完整数据目录，确认报告、密钥及日志均可写。

### 5. 安装后的真实使用验收

在实际安装的产物上完成一次新建实验、扫码／手填批号、管位调整、运行、逐管复核、签署、导出和打印；退出重启后核对历史、权限、模板创建信息和已签署原件。Windows还需分别测试默认安装位置、自选路径（含空格和中文）、默认`data/`创建、改数据位置并重启、不可写目录报错及升级／卸载后数据保留。检查三语、两套配色、各字号及Windows系统缩放；工作台卡片内部滚动、设置自动保存失败提示、启动按钮间距也需实机确认。用独立验收数据验证断流、设备异常、停止／恢复、长时运行以及完整备份恢复。

**默认发行包仍使用模拟设备、模拟视频和替代算法，安装成功不代表已接入真实仪器。** 真机使用前按[待接入文档](docs/internal-integration-checklist.md)接入正式控制器、相机SDK、算法、试剂数据及完整配置，并确认所需模块／原生库已包含在发行包。真实设备的`engine.time_scale`必须为`1`，不能沿用模拟600倍；接入后重新执行契约测试、目标机全量测试、构建和安装验收。系统密钥保护、真实打印与检测准确率须分别验收。

将最终提交、测试结果／跳过原因、覆盖率、构建报告和哈希、安装平台、真实适配器／算法版本及验收记录登记到[未完成任务](docs/remaining-work.md)。只有目标机实际执行并通过的项目才能标记完成。

## 文档

`docs/`仅保留两份当前文档，旧阶段计划、截图和设计稿由Git历史留存：

- [待接入文档](docs/internal-integration-checklist.md)：外部资料、适配器约定、算法可执行示例及交付依赖。
- [未完成任务](docs/remaining-work.md)：尚未完成的工作、目标机器跳过项、复查范围和最终验证结果。
