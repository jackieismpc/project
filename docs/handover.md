# 交接说明

本文件分三节：写给**算法同事**、**硬件同事**和**甲方 QA**。验收数字见 [验收指标报告](acceptance-report.md)，日常使用见 [用户手册](user-manual.md)，部署见 [部署说明](deployment.md)。

当前交接以[甲方反馈专项计划](client-feedback-remediation-plan-20261116.md)、[剩余验证登记](remaining-work.md)和[内部接入清单](internal-integration-checklist.md)为准。软件已实施三类账号及六组权限、逐管扫码与稳定排序、任意已有结果复核、签署修订版、独立实验报告及导航联动。当前后端只证明流程联调，真实算法、相机和设备仍须分别接入并做现场验收；适配层减少业务代码改动，但不能代替集成测试。以下接口说明供接入使用。

---

## 一、算法同事

完整接口说明在 [algorithm-interface.md](algorithm-interface.md)，读完它就能接入。这里只列要点。

### 1. 接入点

| 项 | 内容 |
|---|---|
| 接口 | `src/gelinspect/interfaces/analysis.py` 的 `GelAnalyzer`：`load(config_path)`、`analyze_row(request)`、`close()` |
| 输入 | `RowAnalysisRequest`：一排最多 8 支管的 `TubeSlot`，翻转前后各 ≥3 帧整排大图（H×W×3，uint8，BGR） |
| 输出 | `RowAnalysisResult`：每支管的 `status`、三态 `state`、`score`、`confidence`、`features`（ΔA、Δs、σ_A、σ_s 等）、全图尺寸的 0/255 掩码。**不输出阴阳性** |
| 实现模板 | `src/gelinspect/infrastructure/analysis/adapter_template.py`（复制后重命名） |
| 参考实现 | `simulation/mock_analyzer.py`（`MockAnalyzer`）；`docs/algorithm-interface.md` 第 8 节有可直接运行的最小示例 |
| 线程 | 软件在同一条专用线程上调用 `load`、`analyze_row`、`close`，保证不并发；不要导入 PySide6、不要联网、用 `logging` 不用 `print` |
| 依赖 | 运行环境不装 `ultralytics`（会和 PySide6 的 Qt 冲突）；用 ONNX：`uv sync --extra inference`；OpenCV 只用 `opencv-python-headless` |

软件怎么对待你的输出：完全凝固 = 阳性，未凝固 = 阴性，**部分凝固 = 待人工复核**；`state` 为空、`low_quality`、`tube_not_found`、`failed` 一律待复核。单支问题放在 `status` 里，不要抛异常；只有模型未加载等整体故障才抛 `AnalyzerError`，软件会把该排全部转为待复核。

### 2. 契约测试命令

```bash
# 只校验内置的假实现（确认环境没问题）
uv run pytest tests/contract

# 校验你的实现（类路径写成 pkg.module:Class）
uv run pytest tests/contract \
    --analyzer-backend=gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer \
    --analyzer-config=config/analyzer.yaml

# 或用环境变量，多个后端用逗号分隔
GELINSPECT_ANALYZER_BACKEND=<类路径> GELINSPECT_ANALYZER_CONFIG=<配置> uv run pytest tests/contract
```

契约测试校验协议、`load` 前后调用抛 `AnalyzerError`、管号一一对应、掩码形状和取值、全黑全白噪声图不抛异常、凝胶缺失规则、不修改输入图像、版本号一致。**它用合成图，不校验判读准确率；耗时超过 2 秒只给警告。** 准确率和 8 支 ≤2 秒需要用真实图像在工控机上测。

### 3. 配置项

```yaml
analysis:
  backend: "gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer"   # 你的类
  config_path: "config/analyzer.yaml"    # 传给 load()；阈值、权重 YAML 归算法侧，软件只记录版本
  timeout_ms: 2000                       # analyze_row 时限，超时只告警，不中断流程
interpretation:                          # 可选：三态到阴阳性的映射（默认部分凝固待复核）
  partially_gelled: pending_review
low_confidence_threshold: 0.7            # 可选：低于阈值的结果转待复核；默认不启用，等 QA 确认
```

正式配置写在数据目录下的 `config.yaml`（见部署说明第 3 节），或启动时 `--config 文件`（完整配置，不与默认值合并）。完整样例：`simulation/config.example.yaml`、发布包里的 `config/config.example.yaml`。

### 4. 示例

用你的类启动并跑完整流程（仍用模拟设备和视频）：

```bash
# 复制 simulation/config.example.yaml，只把 analysis.backend 和 config_path 换成你的
uv run gelinspect --config my.yaml
```

软件将模型版本、配置版本、耗时、特征和判定记录到数据库，供诊断与追溯使用。界面不展示内部实现来源。

### 5. 需要你确认的问题

| # | 问题 | 当前默认 | 影响 |
|---|---|---|---|
| 8 | 输入是整排大图还是单管裁剪图 | 整排大图加管位信息，切分由算法做 | 若改成单管，要改 `RowAnalysisRequest` 并在软件侧增加裁剪 |
| 9 | Score 的方向；Δs 的单位（像素还是归一化） | 软件只存储展示，不参与计算；界面不写 Δs 单位 | Score 超出 0–1 时要放宽校验；单位定了要补界面标注 |
| 10 | 凝胶缺失时如何判定 | 由算法输出 `state`，软件不补判 | 无 |
| 5 | “三类指标”与 4 个权重的关系 | 按 ΔA、Δs、σ_A、σ_s 四项 | 只影响 `GelFeatures` 字段，需同步改接口 |
| A–D | 缺失一侧的 ΔA 是否必须为 None；`position_in_row=1` 在左还是右；整图掩码内存（4000×3000 每排最多约 384 MB）；8 支 ≤2 秒是否可行 | 见 algorithm-interface.md 第 9 节 | 掩码方案和位置约定会影响接口和契约测试 |

---

## 二、硬件同事

设备控制和相机取图都隔着适配层，协议和型号确定前用模拟实现。**本阶段没有真实设备和相机，这两条链路没有实机验证。**

### 1. 接入点

| 项 | 接口 | 模板 | 参考实现 |
|---|---|---|---|
| 设备控制 | `src/gelinspect/interfaces/device.py` 的 `DeviceController`：`connect`、`move_tray`、`select_row`、`flip_row`、`restore_row`、`read_status`、`close` | `infrastructure/device/adapter_template.py`（`DeviceControllerAdapter`） | `simulation/sim_device.py` |
| 工业相机 | `src/gelinspect/interfaces/video.py` 的 `VideoSource`：`open`、`read`、`close` | `infrastructure/video/industrial_template.py`（`IndustrialCameraSource`，步骤写在文件头） | `simulation/sim_video.py`；已有 `FileVideoSource`、`ImageSequenceSource`（`infrastructure/video/`） |

要点：

- **时间**：设备和视频帧的时间都用带时区的本机时钟。`FlipResult.completed_at`（翻转完成且静止）用来定位翻转前后各 n 帧，两者必须同一时钟。
- **异常**：协议错误转成 `DeviceConnectionError`、`DeviceTimeoutError`、`DeviceFaultError`（`interfaces/device.py`）；视频错误转成 `interfaces/video.py` 里的异常。不要向上抛驱动自己的异常。
- **状态**：`read_status` 要给出温度、舱门、连接和故障；软件每秒读取一次，温度越限持续超过设定时间转异常，舱门打开时到时的排等关门后才读数。
- **图像**：相机输出要转成 H×W×3、uint8、BGR；要求 1200 万像素（4000×3000），全局快门。管线预览会缩放到长边 ≤960，录制和抓帧用原图。
- **编码器角度**：`flip_row` 返回实际角度（没有编码器时为 None），会传给算法并存档。
- **滑台**：每排翻转前 `select_row` 对准，跑完或停止后按配置退出滑台（`engine.tray_out_on_finish`）。
- 真实设备 `engine.time_scale` 必须是 1。

### 2. 测试命令

目前**没有**针对设备和相机的契约测试套件（算法有），已有的是：

```bash
uv run pytest tests/contract/test_adapter_templates.py   # 模板满足 Protocol（mypy 也检查）
uv run pytest tests/services/test_experiment_engine.py   # 引擎用 SimDevice 的行为：翻转、复位、故障、停止、恢复
uv run pytest -m video_perf -v -s                        # 视频管线 4000×3000、10 fps 压测：丢帧、内存、抓拍
```

联调建议：先把 `video.source` 指向真实相机、`device.backend` 仍用模拟设备，跑 `video_perf`（20 秒）看实测帧率和丢帧；再接设备，用 `uv run gelinspect --config my.yaml` 跑 12 排×8 支整板（96 管），看监控页告警和审计。可以仿照 `tests/contract/test_analyzer_contract.py` 增加设备契约测试。

### 3. 配置项

```yaml
video:
  source: "gelinspect.infrastructure.video.my_camera:MyCameraSource"   # 你的类（无参构造）
  device: "SN-12345678"       # 相机序列号，传给 open()
  width: 4000
  height: 3000
  fps: 10
  # 可选：read_timeout_ms（默认 1000）、open_timeout_s（30）、stop_timeout_s（5）
device:
  backend: "gelinspect.infrastructure.device.modbus:ModbusController"
engine:
  incubation_minutes: 60      # 真实孵育
  time_scale: 1               # 真实设备必须为 1（模拟配置里是 60）
  temperature_target_c: 37.0
  temperature_tolerance_c: 1.0
  temperature_hold_s: 60      # 越限持续多久转异常
  frames_per_side: 3          # 翻转前后各取几帧，≥3
```

仪器设置页（管理员）也能改孵育、取帧、温度和视频输入，保存到数据目录的用户配置，对之后的试验生效。配置格式详见 [architecture.md](architecture.md) 第 3 节。账号初始化及当前替代后端边界见内部接入清单。

### 4. 示例

`device.backend` 指向的类需要无参构造，方法签名按 `DeviceController`。最小骨架（完整的步骤注释在 `adapter_template.py`）：

```python
class ModbusController:
    def connect(self) -> None:
        """打开端口、握手、读一次状态。"""

    def move_tray(self, position: TrayPosition) -> None:
        """发指令、轮询到位，超时抛 DeviceTimeoutError。"""

    def select_row(self, row_index: int) -> None: ...

    def flip_row(self, row_index: int) -> FlipResult:
        """返回 FlipResult(actual_angle_deg, started_at, completed_at)。"""

    def restore_row(self, row_index: int) -> FlipResult: ...

    def read_status(self) -> DeviceStatus:
        """返回温度、舱门、连接和故障。"""

    def close(self) -> None: ...
```

### 5. 需要你确认的问题

| # | 问题 | 当前默认 | 影响 |
|---|---|---|---|
| 11 | 工业相机型号和 SDK；设备通信协议（串口、Modbus 等）；工控机操作系统和配置 | 视频和图片输入可用，设备控制用模拟实现 | 决定适配层怎么写；工控机配置决定打包目标（Windows 包尚未实测） |
| 16 | 12 排同时到点时串行读取的实际时间窗口 | 当前串行读取，不再提供迟读告警 | 需以实际规程和硬件节拍确认，不用估算代替实测 |
| — | 实际翻转时间、取图耗时 | 未知 | 决定“8 支 10 秒”能否达标，需样机实测 |

---

## 三、甲方 QA

实际检测方法和待确认输入见[需甲方确认事项](client-confirmation.md)，避免沿用旧交接表中已废止的模板、权限和报告要求。

- 当前内置方法为待甲方确认的实现假设；灵敏度布局为18管，限度试验包含复试，通用模板只提供逐管结果。其他内置模板及结论规则已启用，不能把旧版“默认不显示结论”说明当作当前配置。
- 普通用户按六组授权组合，“实验与结果处理”包含运行、任意已有结果复核和签署。复核原因必填，原算法结果与复核历史保留；已签署后复核产生待重新签署的修订内容，原签署文件保留。
- 报告以独立实验为单位，可按批号查询并批量导出多份实验报告。输出采用操作开始时的客户端语言；其他语言正式版本须重新签署。
- 报告要求中文宋体、英文及数字Times New Roman；依赖目标机字体。字体可用性、嵌入表现、许可来源及Windows显示缩放需要目标环境验收。
- 自动化测试和当前替代后端运行不能证明真实检测准确率、设备时序及现场吞吐指标达成。现场确认范围、软件验证证据及未完成项目分别见专项计划、验收报告和剩余验证登记。
