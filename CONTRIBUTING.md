# 贡献指南

## 开发流程

1. 安装依赖：`uv sync`。
2. 围绕已确认需求修改，按当前任务约定执行检查；本轮Mac仅开发和静态检查，测试与构建只在目标机执行。
3. 提交前确认 `git status` 中没有参考素材、运行数据或证书文件。

## 注释与文档规范

- 文档字符串使用 **Google 风格**，内容使用**中文**。
- 标识符（模块、类、函数、变量）使用**英文**。
- 每个模块、公共类和公共函数都需要 docstring；`tests/` 目录不强制。
- 行宽上限 100 字符，格式化与检查由 ruff 执行，类型检查由 mypy（strict 模式）执行。
- `docs/`只维护待接入和未完成任务两份文档；当前行为和开发入口写入README，公共接口在源码docstring中说明。避免重新生成过期阶段记录。
- 引号一律用中文弯引号“”，不使用「」（界面文字、报告模板、错误提示、docstring 和注释都一样）。
- 界面可见文字一律用 `tr()`（源语言为简体中文）；修改后运行 `uv run python scripts/update_translations.py` 并补全英文译文，并运行 `--check` 核对。

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
- 示例：`feat(plate): 增加 12 排×8 支孔板状态展示`。
- 提交与推送遵循当前任务授权；用户已明确要求推送时，完成检查后推送当前任务分支，不擅自合并主分支。

## 阶段完成标准

提交前须满足：

1. `uv run ruff check .` 与 `uv run ruff format --check .` 通过。
2. `uv run mypy` 通过。
3. 目标机执行`uv run pytest`并覆盖新增逻辑；本轮允许先提交，未执行测试须如实记录为目标机待验收，不能标记通过。
4. 更新当前两份文档中的接入边界、未完成事项及验证结果，修正失效引用。
5. 提交中不得夹带数据、私钥或临时产物；按任务授权推送，报告实际提交及验证边界。

## 目录约定

- 源码只放在 `src/gelinspect/`，模拟内容只放在 `simulation/`。
- 主代码不得导入 `simulation/` 以外的模拟逻辑，模拟实现只能通过接口注入。
- 参考素材（根目录的 `*.mp4`、`*.jpg`、`*.docx` 等）只读，不得移动、修改或删除。
