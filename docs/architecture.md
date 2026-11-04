# 架构说明

本文定义 GelInspect 的分层、依赖方向、线程模型、后端配置和架构决策。接口的权威定义是 `src/gelinspect/interfaces/` 的代码；本文和代码不一致时，以代码为准并修正本文。

## 1. 分层

| 层 | 路径 | 职责 | 可以依赖 |
|---|---|---|---|
| 界面 | `src/gelinspect/ui/` | 页面、组件、主题 | `services`、`domain`、PySide6 |
| 服务 | `src/gelinspect/services/` | 试验引擎、报告、溯源、权限检查、审计 | `domain`、`interfaces`、PySide6 的 `QtCore` |
| 领域 | `src/gelinspect/domain/` | 实体、状态机、模板、权限矩阵、三态映射 | 只有标准库 |
| 接口 | `src/gelinspect/interfaces/` | `Protocol` 和数据契约 | `domain`、numpy、Pydantic、标准库 |
| 基础设施 | `src/gelinspect/infrastructure/` | 数据库、视频、设备和算法适配 | `domain`、`interfaces`、各类驱动库 |
| 模拟 | `simulation/` | 模拟数据和模拟实现 | `domain`、`interfaces`、numpy、cv2 |

依赖方向：

```text
ui → services → interfaces → domain
                    ↑
       infrastructure、simulation（实现接口）
```

规则：

- `services` 只依赖接口，不直接导入 `infrastructure` 的具体类。具体后端在启动时按配置创建并注入。
- `domain` 和 `interfaces` 不依赖 Qt、数据库或 cv2。
- `domain` 不依赖 numpy 和 Pydantic，所以算法枚举（`GelState`、`TubeAnalysisStatus`）定义在 `domain.enums`，由 `interfaces.analysis` 重新导出，算法同事只需要导入 `interfaces.analysis`。

`tests/test_architecture.py` 用 AST 强制 `domain`、`interfaces` 的导入范围，并禁止主代码导入 `simulation`。

## 2. 线程模型

| 线程 | 数量 | 运行内容 | 不允许做的事 |
|---|---|---|---|
| Qt 主线程 | 1 | 全部界面对象 | 调用设备、算法、视频源；执行耗时数据库操作 |
| 采集线程 | 1 | `VideoSource` 的全部方法、写环形缓冲 | 访问界面对象、等待设备或算法 |
| 试验引擎线程 | 1 | 状态机、10 排调度、`DeviceController` 的全部方法、结果写库 | 访问界面对象、直接调用算法 |
| 分析工作线程 | 1 | `GelAnalyzer` 的全部方法 | 访问界面对象、数据库和设备 |

通信方式：

```text
           Signal（排队连接）                      命令队列
Qt 主线程 ←─────────────────── 试验引擎线程 ←──────────────── Qt 主线程（启动、停止、复核）
    ↑                             │    ↑
    │ 预览帧（只保留最新一帧）      │    │ Future：RowAnalysisResult 或异常
    │                             ↓    │
采集线程 → 环形缓冲 ──抓帧──→ 试验引擎线程 ──RowAnalysisRequest──→ 分析工作线程
```

- **界面和引擎**：引擎是移到工作线程的 `QObject`。界面通过 Slot 发送命令，引擎通过 `Signal` 发布不可变的快照，连接类型都是排队连接。界面不持有 `Experiment` 对象，也不直接修改它。
- **状态所有权**：`Experiment` 只在引擎线程上读写，因此领域层不需要加锁。
- **采集和其他线程**：环形缓冲是唯一的共享结构，内部加锁，并限制内存上限。超过上限时丢预览帧，不丢抓拍帧。预览帧缩放后通过 `Signal` 送到界面，约 15 fps，只保留最新一帧。
- **引擎和算法**：引擎把 `RowAnalysisRequest` 提交给单线程执行器，得到 `Future`。`load`、`analyze_row`、`close` 都在这条线程上串行执行，所以算法实现不需要处理并发。
- **超时**：`analyze_row` 超过配置的时限（默认 2 秒）时写告警和审计事件，但不中断，继续等待结果。Python 线程无法安全地强制终止。引擎等待期间继续响应停止命令和其他排的倒计时。
- **设备调用**：`DeviceController` 的方法同步阻塞，由引擎线程调用。P6 需要保证翻转等待期间，其他排的倒计时仍然按真实时钟计算（使用截止时间，而不是累减计数）。
- **帧的所有权**：`VideoFrame.image` 写入环形缓冲后不再被改写。`CapturedFrame.image` 可以直接引用同一个数组，不需要复制；任何消费者都不得修改。
- **时钟**：`VideoFrame.captured_at` 和 `FlipResult` 的时间使用同一个本机时钟，抓帧按此定位翻转前后的帧。

## 3. 后端配置

配置放在 YAML 中。三个后端用同一种格式：`包.模块:类名`。

```yaml
analysis:
  backend: "gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer"
  config_path: "config/analyzer.yaml"   # 传给 GelAnalyzer.load，可为空
  timeout_ms: 2000                      # 超时只告警
video:
  source: "gelinspect.infrastructure.video.opencv:OpenCvCameraSource"
  device: "0"                           # 对应 VideoConfig.device
device:
  backend: "gelinspect.infrastructure.device.modbus:ModbusController"
```

约定：

- 后端类必须能**无参构造**，构造时不做耗时操作，也不连接硬件。资源在 `load`、`open`、`connect` 中获取，参数通过配置传入。
- 启动时按配置导入类，并用 `isinstance` 检查是否满足对应的 `Protocol`（`runtime_checkable` 只检查方法名，签名由 mypy 和契约测试保证）。不满足或导入失败时，启动报错，不回退到模拟实现。
- 配置项没有默认的模拟后端。只有 `--simulate` 才加载模拟配置。
- 加载配置的代码由 P6 实现，本阶段只确定约定。

上面示例中的 `yolo_onnx`、`opencv`、`modbus` 模块是后续阶段的产物，当前还不存在。

## 4. `simulation/` 隔离规则

1. 所有模拟数据和模拟实现都放在 `simulation/`。
2. `src/gelinspect/` 不得导入 `simulation`，只能通过配置字符串使用它。此规则由 `tests/test_architecture.py` 强制。
3. `simulation/` 只通过 `interfaces` 和 `domain` 与主代码交互，不依赖 `services` 或 `ui`。
4. 删除 `simulation/` 后，主代码仍能导入和启动（使用真实后端配置）。
5. 测试专用的假实现放在 `tests/`，不放进 `simulation/`，例如 `tests/contract/fake_analyzer.py`。
6. 界面、报告和数据库不设模拟标记（`docs/PLAN.md` 第 3 节）。

## 5. 架构决策记录

### ADR-001 只使用 PySide6-Essentials

- **决定**：只依赖 `PySide6-Essentials`，不安装 `PySide6` 整包和 `PySide6-Addons`。
- **原因**：Addons 中的 QtCharts 等模块是 GPL 许可，与闭源交付冲突；Essentials 是 LGPL。
- **后果**：不能使用 QtCharts，图表改用 pyqtgraph。打包必须使用目录模式，保留 Qt 动态库可替换。

### ADR-002 自建组件库

- **决定**：用 QSS 和主题 token 自建组件库。
- **原因**：PySide6-Fluent-Widgets 免费版是 GPL，同样与闭源交付冲突。另外，孔板、Row 倒计时等组件没有现成的实现。
- **后果**：P2 需要投入组件开发，但外观和许可都完全可控。

### ADR-003 算法使用单一入口

- **决定**：软件只依赖 `GelAnalyzer` 一个 `Protocol`，一次调用处理一排。不把分割、特征、判定拆成多个接口。
- **原因**：算法方案的交付物是 Python 模块。内部如何分割和提取特征，属于算法侧的决定，也是最可能变化的部分。入口越小，算法迭代时软件需要改动的地方越少。
- **后果**：软件拿不到中间步骤的控制权，所以必须通过 `TubeAnalysis` 取回特征和掩码用于展示和存档。

### ADR-004 算法不输出阴阳性

- **决定**：算法输出三态、评分和特征。阴阳性由软件按 `GelStateMapping` 得出。
- **原因**：三态是图像上的观察结论，阴阳性是业务结论。后者可能随 QA 规则变化，而且需要记录和复核。
- **后果**：修改映射规则不需要重新交付算法。无法判定的试管固定转为待复核，这条规则不开放配置，保证失败的试管不会被自动判为阴性或阳性。

### ADR-005 领域层使用标准库 dataclass，接口层使用 Pydantic

- **决定**：`domain` 使用 `dataclass`，`interfaces` 使用 Pydantic 2。
- **原因**：领域层要求纯 Python。接口层的数据来自外部（算法同事、驱动），需要运行时校验。
- **后果**：`TubeAnalysis` 和 `TubeResult` 之间需要转换，由 `TubeResult.from_analysis` 承担。特征在领域层只保存快照，不重复定义结构。

### ADR-006 ndarray 字段只做 O(1) 校验

- **决定**：Pydantic 校验器只检查 ndarray 的类型、维度和 dtype，不复制也不遍历。掩码的取值检查放在 `check_row_result`。
- **原因**：一排有 8 支试管、每支 4 个掩码。按 4000×3000 计算，遍历像素会明显占用 2 秒的预算。
- **后果**：运行时不保证掩码取值正确，由契约测试把关。
