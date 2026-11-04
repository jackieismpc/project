r"""算法适配模板，供算法同事接入。

接入步骤（详见 ``docs/algorithm-interface.md``）：

1. 复制本文件，例如改名为 ``infrastructure/analysis/yolo_onnx.py``，并重命名类。
2. 把模型和阈值 YAML 放到 ``load`` 能读取的位置，运行时只用 ONNX 推理，
   不要导入 ultralytics，也不要在依赖中加入非 headless 的 OpenCV。
3. 实现下面三个方法。推理库在 ``load`` 内部导入，保证未安装 ``[inference]`` 时模块仍可导入。
4. 运行契约测试自检::

       uv run pytest tests/contract \
           --analyzer-backend=gelinspect.infrastructure.analysis.yolo_onnx:YoloOnnxAnalyzer

5. 在配置中设置 ``analysis.backend`` 指向新类。界面和业务层不需要改动。
"""

from __future__ import annotations

from pathlib import Path

from gelinspect.interfaces.analysis import AnalyzerInfo, RowAnalysisRequest, RowAnalysisResult


class GelAnalyzerAdapter:
    """``GelAnalyzer`` 的实现模板。必须能无参构造，构造时不要加载模型。"""

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        """加载模型并预热。

        Args:
            config_path: 算法配置文件路径，为 None 时使用默认配置。

        Returns:
            模型和阈值配置的版本。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：读取 YAML → 创建 onnxruntime.InferenceSession → 用空白图推理一次完成预热
        # → 返回版本。任何加载失败都转换为 AnalyzerError。
        raise NotImplementedError("请按模块说明实现模型加载")

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        """分析一排试管。

        Args:
            request: 一排的分析请求，不得修改其中的图像。

        Returns:
            每支试管一条结果，单支问题放在 ``status`` 中。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：未加载时抛 AnalyzerError → 按 request.tubes 切分试管 → 分割和提取特征
        # → 判定三态 → 组装结果。把单支处理包在 try/except 中，异常转换为 status=failed。
        # 不要输出阴阳性。
        raise NotImplementedError("请按模块说明实现分析逻辑")

    def close(self) -> None:
        """释放模型资源，可重复调用。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：释放 InferenceSession，并把内部状态恢复为未加载。
        raise NotImplementedError("请按模块说明实现资源释放")
