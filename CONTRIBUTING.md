# 贡献指南

## 开发流程

1. 安装依赖：`uv sync`。
2. 按阶段完成功能，每个阶段结束前执行全部检查（见下文「阶段完成标准」）。
3. 提交前确认 `git status` 中没有参考素材、运行数据或证书文件。

## 注释与文档规范

- 文档字符串使用 **Google 风格**，内容使用**中文**。
- 标识符（模块、类、函数、变量）使用**英文**。
- 每个模块、公共类和公共函数都需要 docstring；`tests/` 目录不强制。
- 行宽上限 100 字符，格式化与检查由 ruff 执行，类型检查由 mypy（strict 模式）执行。
- 功能性文档放在 `docs/features/`，统一包含：目的、流程、权限、数据、测试。

示例：

```python
def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
    """分析一排试管的翻转前后图像。

    Args:
        request: 一排的分析请求，包含管位信息与翻转前后各 n 帧。

    Returns:
        每支试管的分析结果，单支失败通过 ``status`` 表达，不抛异常。

    Raises:
        AnalyzerError: 模型未加载等整体故障。
    """
```

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```text
<type>(<scope>): <简述>
```

- `type`：`feat`、`fix`、`docs`、`refactor`、`test`、`build`、`chore`、`perf`。
- `scope` 建议使用模块名，例如 `plate`、`video`、`report`、`deps`。
- 示例：`feat(plate): 增加 8×10 孔板状态展示`。
- 仅在本地提交，不推送。提交由负责人审查后执行。

## 阶段完成标准

每个阶段（见 `docs/PLAN.md` 第 5 节）结束前须满足：

1. `uv run ruff check .` 与 `uv run ruff format --check .` 通过。
2. `uv run mypy` 通过。
3. `uv run pytest` 通过，并覆盖本阶段新增逻辑。
4. 更新 `docs/PLAN.md` 的进度表、`docs/CHANGELOG.md`，以及对应的功能文档或架构文档。
5. 完成一次本地提交，并在 `docs/PLAN.md` 的「提交」列记录提交哈希。

## 目录约定

- 源码只放在 `src/gelinspect/`，模拟内容只放在 `simulation/`。
- 主代码不得导入 `simulation/` 以外的模拟逻辑，模拟实现只能通过接口注入。
- 参考素材（根目录的 `*.mp4`、`*.jpg`、`*.docx` 等）只读，不得移动、修改或删除。
