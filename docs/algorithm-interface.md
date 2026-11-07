# 算法接口

写给 YOLO 算法同事。读完本文就能接入。接口代码在 `src/gelinspect/interfaces/analysis.py`。

## 1. 你需要做什么

实现一个类，它有 `load`、`analyze_row`、`close` 三个方法。软件在一排试管翻转、抓帧之后调用一次 `analyze_row`，你返回每支试管的三态、评分、特征和掩码。

**算法不输出阴阳性。** 三态到阴阳性的映射、试验结论、人工复核、存储和报告都由软件负责。

```python
class GelAnalyzer(Protocol):
    def load(self, config_path: Path | None) -> AnalyzerInfo: ...
    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult: ...
    def close(self) -> None: ...
```

| 方法 | 调用时机 | 要求 |
|---|---|---|
| `load` | 软件启动时一次 | 加载模型和阈值 YAML，完成预热。可以在 `close` 后再次调用。失败时抛 `AnalyzerError` |
| `analyze_row` | 每排一次 | 同步返回。未加载或已关闭时抛 `AnalyzerError` |
| `close` | 软件退出时 | 释放资源，可以重复调用 |

类必须能**无参构造**，构造时不要加载模型。

## 2. 输入

### `RowAnalysisRequest`：一排一次

| 字段 | 类型 | 说明 |
|---|---|---|
| `experiment_id` | `str` | 追溯用，算法无需理解 |
| `row_index` | `int` | 排号 1–12，追溯用，算法无需理解 |
| `tubes` | `list[TubeSlot]` | 本排**有样品**的管位，1–8 个。可能不满 8 支，也不保证按孔号排序 |
| `frames_before` | `list[CapturedFrame]` | 翻转前的帧，至少 3 帧，按时间升序 |
| `frames_after` | `list[CapturedFrame]` | 翻转后的帧，至少 3 帧，按时间升序 |
| `flip_angle_deg` | `float \| None` | 编码器读到的实际角度，用于校正坐标系。没有编码器时为 `None` |
| `tube_spec` | `str \| None` | 管型，用于选择标定配置 |
| `reagent_batch` | `str \| None` | 鲎试剂批号，用于选择标定配置 |

`request.image_size` 返回 `(高, 宽)`，也就是掩码必须具有的形状。

### `TubeSlot`

| 字段 | 类型 | 说明 |
|---|---|---|
| `tube_no` | `int` | 管号 1–96。按排编号：Row01 是 1–8，Row02 是 9–16，依此类推，Row12 是 89–96 |
| `position_in_row` | `int` | 排内位置 1–8。例如 11 号管属于 Row02，位置是 3 |

### `CapturedFrame`

| 字段 | 类型 | 说明 |
|---|---|---|
| `image` | `np.ndarray` | 整排图像，H×W×3，`uint8`，**BGR** 通道顺序 |
| `captured_at` | `datetime` | 采集时间，带时区 |
| `source_path` | `Path \| None` | 原图路径，未落盘时为 `None` |

## 3. 输出

### `RowAnalysisResult`

| 字段 | 类型 | 说明 |
|---|---|---|
| `analyzer_info` | `AnalyzerInfo` | 模型版本 `model_version`，阈值配置版本 `config_version`，都不能为空 |
| `elapsed_ms` | `float` | 算法自测耗时，单位毫秒 |
| `tubes` | `list[TubeAnalysis]` | 与输入 `tubes` **一一对应**：不缺、不多、不重复。顺序不限 |

### `TubeAnalysis`

| 字段 | 类型 | 说明 |
|---|---|---|
| `tube_no` | `int` | 与输入的管号对应 |
| `status` | 枚举 | `ok`、`gel_missing`、`tube_not_found`、`low_quality`、`failed` |
| `state` | 枚举或 `None` | `fully_gelled`、`partially_gelled`、`not_gelled`；无法判定时为 `None` |
| `score` | `float \| None` | 融合评分，范围 0–1 |
| `confidence` | `float \| None` | 置信度，范围 0–1 |
| `features` | `GelFeatures` | 特征，默认全空 |
| `masks` | `TubeMasks \| None` | 掩码，用于界面叠加和存档 |
| `message` | `str \| None` | 失败原因，会显示给复核人员 |

各状态的字段要求（构造对象时自动校验，不满足会抛 `pydantic.ValidationError`）：

| `status` | 含义 | `state` | `score`、`confidence` | `masks` | `message` | 软件的处理 |
|---|---|---|---|---|---|---|
| `ok` | 分析成功 | **必填** | 可选 | **必填** | 可选 | 按映射规则判读 |
| `gel_missing` | 翻转前或翻转后凝胶缺失 | 可选 | 可选 | 可选 | 可选 | 有 `state` 时按映射规则判读，否则待复核 |
| `low_quality` | 图像质量不足 | 可选，软件不采信 | 可选 | 可选 | **必填** | 待复核 |
| `tube_not_found` | 未找到试管 | 必须为 `None` | 必须为 `None` | 必须为 `None` | **必填** | 待复核 |
| `failed` | 该支分析失败 | 必须为 `None` | 必须为 `None` | 必须为 `None` | **必填** | 待复核 |

### `GelFeatures`

软件只存储和展示，不用于计算，也不会补算。

| 字段 | 类型 | 单位和坐标系 |
|---|---|---|
| `area_before`、`area_after` | `float \| None` | 像素，≥0 |
| `delta_area_pct` | `float \| None` | 面积变化率 ΔA，% |
| `centroid_before`、`centroid_after` | `(float, float) \| None` | 图像坐标 `(x, y)`，像素 |
| `axial_before`、`axial_after` | `float \| None` | 试管坐标系的轴向位置 |
| `delta_s` | `float \| None` | 轴向质心偏移 Δs，试管坐标系，单位待确认（见第 9 节） |
| `sigma_area`、`sigma_axial` | `float \| None` | 时序标准差 σ_A、σ_s，≥0 |
| `gel_missing_before`、`gel_missing_after` | `bool` | 凝胶是否缺失，默认 `False` |

**凝胶缺失规则：**

- `status == gel_missing`，当且仅当 `gel_missing_before` 或 `gel_missing_after` 至少有一个为 `True`。
- 缺失的一侧：`area_*`、`centroid_*`、`axial_*` 和 `masks.gel_*` 都必须为 `None`。
- 任意一侧缺失：`delta_area_pct` 和 `delta_s` 都必须为 `None`。
- `sigma_area`、`sigma_axial` 由算法决定是否给出。
- 未凝固样本常见的凝胶缺失是正常结果，不是失败。请用 `gel_missing` 加 `state=not_gelled` 表达。

### `TubeMasks`

| 字段 | 类型 | 说明 |
|---|---|---|
| `tube_before`、`tube_after` | `np.ndarray` | 翻转前后代表帧的试管掩码，必填 |
| `gel_before`、`gel_after` | `np.ndarray \| None` | 翻转前后代表帧的凝胶掩码，缺失时为 `None` |

## 4. 数据格式

| 项 | 约定 |
|---|---|
| 输入图像 | H×W×3，`uint8`，BGR。部署时 ≥1920×1080。一次请求内所有帧尺寸相同 |
| 掩码 | H×W，`uint8`，取值**只有 0 和 255**，尺寸与原图相同（**不是**裁剪图，也不是 `imgsz=1024` 的尺寸） |
| 图像坐标 | 原点在左上角，x 向右，y 向下，单位像素 |
| 排内位置 | `position_in_row=1` 位于图像最左侧，向右递增（暂定，见第 9 节） |
| 翻转后的图像 | 软件给的是相机原始图像，**不做**旋转校正。坐标校正由算法用 `tube_mask` 和 `flip_angle_deg` 完成 |
| 试管坐标系 | 按算法方案第 5 节：原点在管口端，轴向 s 指向管底为正 |
| 输入图像的所有权 | **不得修改**输入数组，需要改动时先复制。同一个数组可能同时被录制和界面使用 |

## 5. 异常约定

| 情况 | 正确做法 |
|---|---|
| 单支试管有问题（找不到试管、推理报错等） | 放在该支的 `status` 中，**不抛异常** |
| 整张图都不可用（全黑、过曝等） | 每支都返回 `low_quality` 或 `failed`，**不抛异常** |
| 未 `load`、已 `close`、模型损坏、推理库崩溃 | 抛 `AnalyzerError` |

建议把每支试管的处理放在 `try/except` 里，把意外异常转换成 `status=failed`，并把异常信息写入 `message`。

软件把失败的试管标记为待人工复核，绝不默认判阴性。`analyze_row` 抛出的 `AnalyzerError`，会使该排全部试管进入待复核。

## 6. 耗时和线程

- **耗时**：8 支试管的一次 `analyze_row` 应 ≤2 秒。超时时软件记录告警，不中断流程，也不重试。
- **线程**：软件在**同一条专用工作线程**上调用 `load`、`analyze_row` 和 `close`，并保证不并发。你不需要加锁。可以在内部使用线程池或 ONNX Runtime 的多线程。
- **禁止**：不要导入 PySide6 或操作界面；不要联网（软件离线运行，权重不能在运行时下载）；不要使用 `print`，日志请使用标准库 `logging`。
- **确定性**：相同输入和相同配置应该得到相同结果，以保证结果可追溯。

## 7. 环境和依赖约束

- **Python 3.11**（`requires-python = ">=3.11,<3.13"`），用 `uv` 管理。
- **不要在运行环境依赖 `ultralytics`。** 它依赖非 headless 的 OpenCV，会与 PySide6 的 Qt 冲突。训练在独立环境中进行，交付时导出 ONNX。
- **推理走 ONNX：** 运行环境使用 `onnxruntime`，它是可选依赖组 `[inference]`，用 `uv sync --extra inference` 安装。请在 `load` 内部导入 `onnxruntime`，这样没有安装它时模块仍能导入。
- **OpenCV 只能用 `opencv-python-headless`**（项目已依赖，`>=4.10,<5`）。不要引入 `opencv-python` 或 `opencv-contrib-python`。
- 新增其他依赖前请先沟通，并说明许可证。Ultralytics 的 AGPL-3.0 合规路径另见 `docs/PLAN.md` 第 8 节，导出的权重是否受约束需要在正式交付前确认。
- 模型文件和阈值 YAML 归算法侧管理，软件只记录你在 `AnalyzerInfo` 中返回的版本号。

## 8. 接入步骤

1. 复制 `src/gelinspect/infrastructure/analysis/adapter_template.py`，例如复制为 `yolo_onnx.py`，并重命名类。
2. 实现三个方法。下一节的示例可以直接运行。
3. 运行契约测试（命令见下）。
4. 在配置中设置 `analysis.backend`：

   ```yaml
   analysis:
     backend: "gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer"
     config_path: "config/analyzer.yaml"
   ```

界面和业务层不需要改动。

### 契约测试

```bash
# 只校验内置的假实现
uv run pytest tests/contract

# 校验你的实现（类路径可写成 pkg.module:Class 或 pkg.module.Class）
uv run pytest tests/contract \
    --analyzer-backend=gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer \
    --analyzer-config=config/analyzer.yaml

# 也可以用环境变量，多个后端用逗号分隔
GELINSPECT_ANALYZER_BACKEND=gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer \
GELINSPECT_ANALYZER_CONFIG=config/analyzer.yaml \
    uv run pytest tests/contract
```

契约测试校验：满足 `Protocol`；`load` 之前和 `close` 之后调用抛 `AnalyzerError`；`close` 可重复调用，之后可以再次 `load`；管号一一对应（整排、单支、乱序的部分管位）；掩码形状、dtype 和取值；全黑、全白、噪声图不抛异常；`gel_missing` 时相应字段为 `None`；不修改输入图像；结果中的版本与 `load` 返回的一致。

耗时超过 2 秒只产生 pytest 警告，因为它取决于运行机器。性能验收要在工控机上做。契约测试使用合成图，不校验判读准确率。

### 最小示例

这个示例只依赖 numpy，用亮度阈值代替模型，可以直接通过契约测试（`tests/contract/test_doc_example.py` 会运行它）。把 `_segment` 换成 ONNX 推理，把判定逻辑换成你的评分，就是真实实现的骨架。

```python
"""GelAnalyzer 最小示例：用亮度阈值代替分割模型。"""

import time
from pathlib import Path

import numpy as np

from gelinspect.interfaces.analysis import (
    AnalyzerError,
    AnalyzerInfo,
    GelFeatures,
    GelState,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeAnalysis,
    TubeAnalysisStatus,
    TubeMasks,
    TubeSlot,
)

MIN_GEL_AREA = 50  # 算法方案第 5 节：面积低于 50 像素记为凝胶缺失
SLOTS_PER_IMAGE = 8


def _segment(image: np.ndarray, position: int) -> tuple[np.ndarray, np.ndarray]:
    """返回一支试管的 (tube_mask, gel_mask)，尺寸与原图相同，取值 0 或 255。"""
    gray = image.mean(axis=2)
    width = image.shape[1] // SLOTS_PER_IMAGE
    left, right = (position - 1) * width, position * width
    tube, gel = np.zeros((2, *gray.shape), dtype=np.uint8)
    tube[:, left:right][gray[:, left:right] > 20] = 255
    gel[:, left:right][gray[:, left:right] > 128] = 255
    return tube, gel


def _centroid(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


class MinimalAnalyzer:
    """最小的 GelAnalyzer 实现。"""

    def __init__(self) -> None:
        self._info: AnalyzerInfo | None = None

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        # 真实实现：读取 YAML，创建 onnxruntime.InferenceSession，推理一次完成预热。
        # 任何加载失败都要转换为 AnalyzerError。
        self._info = AnalyzerInfo(model_version="demo-1", config_version="demo-1")
        return self._info

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        if self._info is None:
            raise AnalyzerError("模型未加载")
        started = time.perf_counter()
        tubes = [self._analyze_tube_safely(slot, request) for slot in request.tubes]
        return RowAnalysisResult(
            analyzer_info=self._info,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            tubes=tubes,
        )

    def close(self) -> None:
        self._info = None

    def _analyze_tube_safely(self, slot: TubeSlot, request: RowAnalysisRequest) -> TubeAnalysis:
        try:
            return self._analyze_tube(slot, request)
        except Exception as exc:  # 单支问题不向外抛，转换为 status
            return TubeAnalysis(
                tube_no=slot.tube_no, status=TubeAnalysisStatus.FAILED, message=str(exc)
            )

    def _analyze_tube(self, slot: TubeSlot, request: RowAnalysisRequest) -> TubeAnalysis:
        # 以最后一帧作为代表帧。不要修改 request 中的图像。
        before = request.frames_before[-1].image
        after = request.frames_after[-1].image
        tube_before, gel_before = _segment(before, slot.position_in_row)
        tube_after, gel_after = _segment(after, slot.position_in_row)

        if not tube_before.any() or not tube_after.any():
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.TUBE_NOT_FOUND,
                message="未检测到试管",
            )

        area_before = float(np.count_nonzero(gel_before))
        area_after = float(np.count_nonzero(gel_after))
        missing_before = area_before < MIN_GEL_AREA
        missing_after = area_after < MIN_GEL_AREA

        if missing_before or missing_after:
            # 凝胶缺失是正常结果。缺失一侧的字段和掩码必须为 None，ΔA、Δs 也为 None。
            return TubeAnalysis(
                tube_no=slot.tube_no,
                status=TubeAnalysisStatus.GEL_MISSING,
                state=GelState.NOT_GELLED,
                confidence=0.5,
                features=GelFeatures(
                    area_before=None if missing_before else area_before,
                    area_after=None if missing_after else area_after,
                    centroid_before=None if missing_before else _centroid(gel_before),
                    centroid_after=None if missing_after else _centroid(gel_after),
                    gel_missing_before=missing_before,
                    gel_missing_after=missing_after,
                ),
                masks=TubeMasks(
                    tube_before=tube_before,
                    tube_after=tube_after,
                    gel_before=None if missing_before else gel_before,
                    gel_after=None if missing_after else gel_after,
                ),
            )

        delta_area_pct = abs(area_before - area_after) / area_before * 100
        state = GelState.FULLY_GELLED if delta_area_pct < 10 else GelState.PARTIALLY_GELLED
        return TubeAnalysis(
            tube_no=slot.tube_no,
            status=TubeAnalysisStatus.OK,
            state=state,  # 只输出三态，不输出阴阳性
            score=max(0.0, 1.0 - delta_area_pct / 100),
            confidence=0.9,
            features=GelFeatures(
                area_before=area_before,
                area_after=area_after,
                delta_area_pct=delta_area_pct,
                centroid_before=_centroid(gel_before),
                centroid_after=_centroid(gel_after),
            ),
            masks=TubeMasks(
                tube_before=tube_before,
                tube_after=tube_after,
                gel_before=gel_before,
                gel_after=gel_after,
            ),
        )
```

## 9. 待算法同事确认的问题

下面的问题都先按“当前默认”开发，不阻塞接入。前 4 项来自 `docs/PLAN.md` 第 7 节。

| # | 问题 | 当前默认 | 如果答案不同 |
|---|---|---|---|
| PLAN-5 | 算法方案说“三类指标”，权重却有 4 个（ω1–ω4） | 按 ΔA、Δs、σ_A、σ_s 四项 | 只影响 `GelFeatures` 的字段，需要同步修改接口 |
| PLAN-8 | 输入是整排大图还是单管裁剪图？文档未写明 | 整排大图加管位信息，单管切分由算法完成 | 要在软件侧增加裁剪，并修改 `RowAnalysisRequest` |
| PLAN-9 | Score 的方向是什么（越大越凝固，还是相反）？Δs 的单位是像素还是归一化？ | 软件只存储和展示，不用于计算。Score 按 0–1 校验 | Score 超出 0–1 时需要放宽校验 |
| PLAN-10 | 凝胶缺失时如何判定？未凝固样本的 Δs 无法计算 | 算法输出 `state`，软件不补判 | 无 |
| A | 缺失一侧的面积和 ΔA 必须为 `None`，这是否可接受？算法方案只说缺失帧不参与 Δs 计算，而 ΔA 是主判据，算法内部可能仍然需要它 | 按 `docs/PLAN.md` 要求，对外输出 `None`；算法内部可以照常使用 | 如需存档 ΔA，可放宽校验器 |
| B | `position_in_row=1` 在图像的哪一侧？翻转后左右顺序是否变化？这取决于相机安装和翻转方向 | 暂定位置 1 在最左侧，翻转前后顺序不变 | 需要在标定配置中声明方向 |
| C | 掩码按整张原图输出，4000×3000 下每排最多约 384 MB（8 支×4 个×12 MB）。是否要改成“裁剪框加局部掩码”？ | 按整图输出，软件收到后尽快压缩存档 | 修改 `TubeMasks`，契约测试同步调整 |
| D | 8 支 ≤2 秒是否可行？算法方案写的是“2 支 / 5–6 秒”，其中包含翻转和取图 | 按 2 秒告警，不中断流程 | 调整 `analysis.timeout_ms` |
