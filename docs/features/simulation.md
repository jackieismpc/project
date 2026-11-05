# 模拟数据（simulation）

对应阶段：P4。

## 目的

真实算法、相机和设备协议都还没有交付，模拟数据让全流程可以先跑通：合成试管图像和视频、按契约工作的模拟算法、模拟设备和试剂白名单、示例试验数据。面向开发和演示人员。

约束（`docs/PLAN.md` 第 3 节）：

- 全部放在根目录 `simulation/`，主代码（`src/gelinspect`）不导入它，只通过接口和配置字符串使用。此规则由 `tests/test_architecture.py` 和 `tests/simulation/test_reagents_and_sample_data.py` 验证。
- 界面、报告和数据库没有模拟标记，图像上没有水印。
- 大文件（预览图、导出的视频）放在 `simulation/generated/`，该目录被 git 忽略，代码里不存二进制文件。

| 文件 | 内容 |
|---|---|
| `image_synth.py` | 合成一排 8 支试管的背光图和真值掩码；可作为脚本生成预览图 |
| `mock_analyzer.py` | `MockAnalyzer`（`GelAnalyzer`）：阈值分割、特征、评分、三态，支持故障注入 |
| `sim_device.py` | `SimDevice`（`DeviceController`）：滑台、翻转、温度、舱门、连接、故障，时间加速 |
| `sim_video.py` | `SimVideoSource`（`VideoSource`）：按当前排和翻转状态生成帧流，含过渡帧，可注入断流 |
| `scene.py`、`scenario.py` | 相机场景（排号、翻转进度），以及每支试管的真实状态（按管号指定、按孔板内容的剂量关系，或比例） |
| `clock.py` | 可注入时钟，`ManualClock` 让测试不真实 `sleep` |
| `reagents.py` | 试剂码解析器、备案白名单（含已过期、未备案样例）、溯源平台占位 |
| `sample_data.py` | 灵敏度复核示例数据 |
| `config.example.yaml`、`analyzer.example.yaml` | 后端配置示例 |

## 流程

### 使用

1. 把 `simulation/config.example.yaml` 复制为实际配置，三个后端分别是：

   ```yaml
   analysis:
     backend: "simulation.mock_analyzer:MockAnalyzer"
     config_path: "simulation/analyzer.example.yaml"
   video:
     source: "simulation.sim_video:SimVideoSource"
   device:
     backend: "simulation.sim_device:SimDevice"
   ```

   三个类都可以无参构造。同一进程里 `SimVideoSource` 默认跟随最近创建的 `SimDevice`，因此设备翻转时视频画面同步翻转。配置由 `services/config.py` 加载，不指定文件时的默认配置就是这份内容，架构约定见 `docs/architecture.md` 第 3 节。

2. 代码中直接使用：

   ```python
   from simulation.clock import ManualClock
   from simulation.sample_data import sensitivity_scenario
   from simulation.sim_device import SimDevice
   from simulation.sim_video import SimVideoSource

   device = SimDevice(time_scale=60.0)  # 60 倍加速：6 秒翻转约 0.1 秒
   video = SimVideoSource(scene=device, scenario=sensitivity_scenario())
   ```

3. 生成预览图：`uv run python -m simulation.image_synth`，写入 `simulation/generated/preview.png`（上为翻转前，下为翻转后）。`--width 4000 --height 3000` 可生成 1200 万像素的图。

### 合成图像

背光明亮，管壁透明，样品团的透光率低于空管，所以阈值即可分割。翻转前管口朝上，翻转后管口朝下，凝胶的位置在试管坐标系中不变。三种状态：

| 状态 | 翻转后 | ΔA（典型） | Δs（典型，按管长归一化） |
|---|---|---|---|
| 完全凝固 | 凝胶仍在管底 | 0–4 % | < 0.01 |
| 部分凝固 | 管底保留一部分，一小团滑向管口 | 25–55 % | 0.04–0.09 |
| 未凝固 | 只在管口留下一小团，或看不到（凝胶缺失） | 70–95 % | 0.6–0.85 |

相同参数和随机种子得到相同像素。`SynthFrame` 同时给出真值：`tube_labels`、`gel_labels`（值为管位号），可用 `tube_mask(position)`、`gel_mask(position)` 取出。

### MockAnalyzer

- **测量模式（默认）**：用合成器已知的布局定位试管，检查亮度、噪声和管壁；在管内用阈值分割样品团并取最大连通域；面积小于 50 像素记为凝胶缺失。Score = 0.5·f(ΔA) + 0.3·f(Δs) + 0.1·f(σ_A) + 0.1·f(σ_s)，每项线性映射到 0–1，越大越凝固；≥ 0.85 完全凝固，≤ 0.25 未凝固。ΔA、Δs 取绝对值；Δs 和轴向位置按管长归一化。这些口径是模拟约定，不代表真实算法（见 `docs/algorithm-interface.md` 第 9 节待确认项）。
- **脚本化模式**：`scripted: true` 时按 `Scenario` 给出状态（`tube_states` 按管号指定，`state_ratios` 按比例抽取），特征按状态合成，掩码仍按图像计算。
- **故障注入**：`tube_faults`（`low_quality`、`failed`、`tube_not_found`）、`fail_load`、`fail_all`（抛 `AnalyzerError`）、`delay_ms`（计入 `elapsed_ms`，并通过 `sleep` 等待）。全部可在 `analyzer.example.yaml` 配置。
- 对任意输入图像都满足契约（全黑、过曝、噪声、非合成图），非合成图通常得到 `low_quality` 或 `tube_not_found`。

### SimDevice

- 动作耗时按设备时间配置（`tray_duration_s`、`flip_duration_s`），实际等待为耗时除以 `time_scale`（默认 60）。时钟可注入，`FlipResult` 的时间与 `SimVideoSource` 的帧时间使用同一时钟。
- 温度为 37 ℃ 加约 ±0.15 ℃ 的缓慢波动和微小噪声，随机种子固定。
- 注入：`inject_temperature_excursion(delta)`、`inject_fault(code, message)`、`inject_timeout()`、`inject_disconnect()`、`refuse_connection()`、`set_door(closed)`，`clear_faults()` 清除。
- `begin_flip(row)` 只开始动作不等待，便于观察翻转过程中的画面。

### SimVideoSource

按 `fps` 产出帧，等待通过时钟完成；翻转过程中输出试管逐渐压缩再展开的过渡帧，翻转完成后样品团按 `settle_duration_s` 流动到稳定。无设备时可用 `static_scene.set(row, flipped=...)` 手动设置。`inject_disconnect()` 让 `read` 抛 `VideoDisconnectedError`（需要 `close` 再 `open` 恢复），`inject_timeouts(n)` 让接下来 n 次读超时。无噪声底图会缓存，只有噪声每帧重新生成。

### 试剂和示例数据

试剂码格式（模拟约定）：`产品编码|批号|种类|有效期|序列号`。`reagents.SAMPLE_CODES` 含有效、已过期（`LAL2501B`）、未备案（`LAL9999X`）等样例。`sample_data` 提供灵敏度复核示例：管 1–6 完全凝固（阳性），7 部分凝固（待复核），8–17 未凝固（阴性）；λ=0.5 时外加内毒素 1 / 0.5 / 0.25 / 0.125 各 4 管加 1 支 0，7 号复核为阳性时 λc≈0.59。

**默认场景的剂量关系**：引擎启动时把孔板内容（`PlatePlan`）交给 `SimDevice.load_plate_plan`，`SimVideoSource` 据此绘制：≥2λ 完全凝固，≤λ/4 和阴性对照未凝固，中间按每个系列抽到的真实终点（λ 或 λ/2）决定，边界附近有部分凝固，由随机种子决定（`simulation/scenario.py` 的 `dose_response_states`）。显式的 `tube_states` 优先；推定模板的管没有剂量可依，仍按 `state_ratios` 抽取。规则见 [conclusions.md](conclusions.md)。

### 替换为真实实现

把配置中的三个类路径换成真实实现（例如 `gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer`），业务和界面不改。启动时不会回退到模拟实现：配置文件是完整配置，不与默认的模拟配置合并。真实算法接入后，用同一套契约测试自检：

```bash
uv run pytest tests/contract --analyzer-backend=simulation.mock_analyzer:MockAnalyzer   # 模拟实现
uv run pytest tests/contract --analyzer-backend=<真实类路径>                            # 真实实现
```

### 从发布包排除

`pyproject.toml` 的 `[tool.hatch.build.targets.wheel] packages` 目前包含 `simulation`。发布包不需要模拟时：

1. 从 `packages` 中去掉 `"simulation"`（P13 的 Nuitka 脚本提供排除选项）；
2. 确认配置不指向 `simulation.*`；
3. 可直接删除 `simulation/` 和 `tests/simulation/`，主代码仍能导入，`uv run pytest` 其余测试照常通过（`mypy` 的 `files` 里也去掉 `simulation`）。

## 权限

不涉及用户权限：模拟实现只在开发和演示配置下加载，不暴露界面入口。

## 数据

- 不读写数据库，不写审计日志。
- 不存储图像文件；`python -m simulation.image_synth` 的输出写到 `simulation/generated/`（git 忽略）。
- 示例试验用 `domain` 的 `Experiment`、`ReagentBatch` 等实体构造，字段与正式试验一致。

## 测试

`tests/simulation/`：

| 文件 | 覆盖 |
|---|---|
| `test_image_synth.py` | 种子可复现、格式、4000×3000、空管位、掩码与图像对齐、三态面积关系、过渡帧 |
| `test_mock_analyzer.py` | 通过 `tests/contract`（子进程运行，带和不带配置）、像素测量结果、ΔA/Δs/Score 排序、掩码与真值 IoU、凝胶缺失、故障注入、脚本化模式、YAML 配置、模拟耗时 |
| `test_sim_device.py` | 状态、翻转、时间加速、温度和越限、故障、超时、断线、场景进度 |
| `test_sim_video.py` | 帧格式和帧率、种子复现、手动场景、跟随设备翻转、断流和读超时 |
| `test_reagents_and_sample_data.py` | 白名单三类样例、解析错误、示例数据、移走 `simulation` 后主代码仍可导入（子进程屏蔽导入）、依赖方向 |

所有时间相关测试使用 `ManualClock`，不真实等待。需要人工检查：`simulation/generated/preview.png` 的画面观感。
