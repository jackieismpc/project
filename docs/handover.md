# 交接说明

本文件分三节：写给**算法同事**、**硬件同事**和**甲方 QA**。验收数字见 [验收指标报告](acceptance-report.md)，日常使用见 [用户手册](user-manual.md)，部署见 [部署说明](deployment.md)。

当前状态：软件的全部业务流程用 `simulation/` 里的模拟设备、模拟视频和模拟算法跑通。**接入真实算法和设备只需要写适配类并改配置，界面和业务层不用改。** 架构上的保证是：主代码不导入 `simulation/`（`tests/test_architecture.py` 强制），后端类路径写在配置里，加载失败时报错并说明改哪一项，**不会悄悄回退到模拟实现**。

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

软件每次调用都会把模型版本、配置版本、耗时、特征和判定写入数据库和审计，试验监控页“判读来源”能看到。

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
  row_late_warn_s: 120        # 读数晚于到时多久告警（PLAN 第 7 节第 16 项待 QA 确认）
  frames_per_side: 3          # 翻转前后各取几帧，≥3
```

仪器设置页（管理员）也能改孵育、取帧、温度和视频输入，保存到数据目录的用户配置，对之后的试验生效。配置格式详见 [architecture.md](architecture.md) 第 3 节。**不要写 `bootstrap` 项**（那是演示账号）。

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
| 16 | 12 排同时到点时串行读取，第 12 排约晚 110 秒，已接近 120 秒告警线（这也是 QA 问题） | 串行读取，晚于 120 秒告警 | 若规程时限更严，需要并行读取或更快的翻转机构 |
| — | 实际翻转时间、取图耗时 | 未知 | 决定“8 支 10 秒”能否达标，需样机实测 |

---

## 三、甲方 QA

下面是需要你们确认的问题，**按优先级排序**（影响放行判断的在前）。每项都先按默认方案实现，改起来只涉及配置或少量代码，不阻塞使用；但在确认之前，对应的结论不能作为放行依据。序号对应 [PLAN](PLAN.md) 第 7 节。

### 高优先级：直接影响结论和放行

| 优先级 | # | 问题 | 当前默认 | 影响什么 |
|---|---|---|---|---|
| 1 | 4 | **干扰、复试、半定量、限度四类试验的结论规则**（药典通则 1143 的条文逐字核对） | 目前这四类**一律给“待定”**，且不能签署 | 这四类试验现在都无法出合格或不合格的结论、无法签署报告。灵敏度复核的规则条文也需要核对原文 |
| 2 | 14 | 上述四类模板的**管位布局**（视频里只看清了灵敏度模板） | 只有连续管号，用途和数值留空，标“布局待确认” | 布局不确认，规则无法启用；内置的 28/10/16/8 管只是按管数推定 |
| 3 | 2 | **部分凝固算什么** | 部分凝固为“待复核”，人工确认前不能签署 | 决定有多少试管要人工复核，直接影响实验效率和判读风险 |
| 4 | 19 | 灵敏度复核在有效前提下，λc 必然落在 0.5λ–2λ，**不可能判“不合格”**，实际不达标都是“无效” | 按此实现 | 结论卡上只会出现合格、无效、待定；如果预期要有“不合格”，规则和布局需要改 |
| 5 | 20 | 不连续系列（如 + − + −）如何判定 | 算出 λc 作参考，结论“待定” | 这类试验目前需要人工判定，无法自动放行 |
| 6 | 17 | 低置信度结果是否转待复核，阈值多少 | 目前只存储，不转 | 阈值可以在配置里设（`low_confidence_threshold`），确认后启用可降低错判风险 |
| 7 | 21 | 结论为“待定”的试验能否签署 | 不能签署 | 防止未判定的结果被当作放行依据；放宽会降低保护 |
| 8 | 13 | **权限矩阵**：实验员操作试验；分析员复核、签署；管理员管系统和模板，不碰试验和结果 | 如左 | 影响谁能放行；参考视频里是 admin 跑试验，与默认不同 |

### 中优先级：影响流程和数据

| 优先级 | # | 问题 | 当前默认 | 影响什么 |
|---|---|---|---|---|
| 9 | 15 | **读数失败的排能否重新翻转**（翻动过的凝胶可能已受影响） | 人工恢复后会重新翻转，确认框已提示风险 | 影响重复读数是否合规；若不允许，需要改成该排作废 |
| 10 | 16 | 12 排同时到时串行读取，第 12 排约晚 110 秒（按每排约 10 秒估算），是否符合规程时限 | 串行读取，晚于到时超过 120 秒告警 | 若超限不可接受，需要改调度或机构；告警阈值可在配置里改 |
| 11 | 23 | 单批次报告的签署条件 | 批次内所有试验都已签署后才能签署 | 影响批次放行的节奏 |
| 12 | 24 | **数据保留期限和到期处理** | 默认不自动删除；自动清理尚未实现，界面已注明 | 磁盘会持续增长；确认期限后才能实现自动清理 |
| 13 | 12 | 视频全程录制还是只录翻转过程；保留多久 | 只录每排翻转前后的片段，保留时长可配置，默认不自动删除 | 影响追溯证据和存储空间 |
| 14 | 22 | 旧自定义灵敏度模板是否转成 λ 倍数 | 不自动转，与 λ 不符时结论待定，管理员可手动填写 | 影响旧模板能否直接用于灵敏度复核 |
| 15 | 18 | 模型整体失效时是否立刻暂停 | 继续读完所有排并告警，原图保留可重试 | 影响其他排会不会错过读数时间 |

### 低优先级：产品和合规

| 优先级 | # | 问题 | 当前默认 | 影响什么 |
|---|---|---|---|---|
| 16 | 6 | **甲方备案产品清单和试剂码格式**未提供 | 用本地模拟白名单，解析器可替换 | 当前的“已备案、有效期”校验是模拟的；正式使用前必须换成真实清单和码格式，否则试剂校验没有意义 |
| 17 | 27 | 程序完整性校验不通过时是否阻止启动 | 只提示并写审计 | 影响防篡改力度 |
| 18 | 28 | 授权启用后的限制策略和设备指纹来源 | 接口已留，默认不启用 | 影响试用和授权功能 |
| 19 | 7、29 | 软件正式名称、Logo、应用图标；清单签名密钥 | 暂用“凝胶智检系统”/ GelInspect 和占位图标；每次构建临时生成密钥 | 影响界面、安装包和发布流程；正式分发前要定 |
| 20 | 26 | 是否购买 Apple Developer ID 和 Windows 代码签名证书 | 未签名，文档写明首次打开的放行步骤 | 影响首次安装体验（被系统拦截提示） |
| 21 | 25 | **法务确认**：LGPL 源代码书面承诺的联系方式；PySide6 部分纯 Python 代码编译进主程序是否合规 | 目录模式，Qt 库可替换，包内附许可声明 | 影响正式发布的合规性 |
| 22 | — | Ultralytics（AGPL-3.0）合规路径：导出的权重是否受约束 | 软件只加载 ONNX，不打包 ultralytics | 正式交付前需甲方或法务确认，见 PLAN 第 8 节 |

另外两项说明（不需要决定，但请知晓）：

- **数字签名使用本机自签证书**，能防篡改并验真，**没有第三方 CA 的法律效力**；需要时可换成 CA 证书。
- **模拟结果不加水印**（按要求）：用模拟配置（`uv run gelinspect` 和演示包）导出的报告看起来和真实结果一样，请勿当作真实检测记录。演示账号密码是公开的。
