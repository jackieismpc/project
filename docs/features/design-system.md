# Qt 设计系统与组件展示页

对应阶段：P2。把 `docs/design/*.dc.html` 的视觉规格落到 PySide6，供后续 12 个页面复用。

## 目的

- 提供 token、全局 QSS、内置字体与图标，保证离线、Windows 与 macOS 显示一致。
- 提供 Card、StatCard、ReadoutCard、Button、StatusBadge、DataTable、Toast、ConfirmDialog、StepIndicator、SideNav、Tabs、Sparkline、IconLoader 和 PlateGrid、TubeStrip。
- 提供组件展示页，供视觉评审和回归对照。

## 流程

1. 启动时调用 `theme.stylesheet.apply_theme(app)`：加载内置字体，设置应用字体，按 token 生成并套用 QSS。
2. 组件通过 `ui/theme/tokens.py` 取色，不在组件里写十六进制色值（测试会检查）。文字样式用 `Label(role=...)`，按钮、徽标等通过动态属性由 QSS 控制。
3. 深色主题：补一份 `Tokens`，`register_theme` 后 `set_theme`，再调用 `apply_theme`。目前只有浅色。
4. 字体缺失时回退到系统字体（PingFang SC、Microsoft YaHei、Menlo、Consolas），`FontReport` 记录是否内置。内置字体只含 GB2312 汉字，生僻字由系统回退链补全。

PlateGrid 要点：

- 竖向编号，全部通过 `domain.plate` 换算。第 1 列（Row01）是 1–8。
- 六种状态靠形状区分：阳性实心；阴性 3px 空心环；待复核下半填充；读数中实环淡底；孵育中虚线环；空孔细线。
- 显示模式点击发 `tubeClicked(int)`；选择模式切换并发 `selectionChanged(list)`，可设上限（`selectionLimitReached`）。
- 键盘：Tab 进入后方向键移动，空格或回车触发，Home/End 跳到首尾，键盘聚焦才画 3px 焦点环。读屏名称为「孔板 8 乘 10」，描述随焦点孔更新（如「7 号管，待复核」）。逐孔的 QAccessible 子对象尚未实现。
- QPainter 自绘并开启抗锯齿，随 Qt 高 DPI 缩放；孔径随控件宽度在 16px 至 1.5 倍基准之间变化。

展示页：

```bash
uv run python -m gelinspect.ui.gallery
QT_QPA_PLATFORM=offscreen uv run python -m gelinspect.ui.gallery \
    --screenshot out.png --size 1280x800 [--page gallery|monitor] [--full] [--scale 2]
```

## 权限

无。本阶段只提供组件，不涉及角色与权限。

## 数据

不读写数据库，也不导入 `simulation`、`infrastructure`（测试强制）。资源如下：

| 资源 | 位置 | 许可 |
|---|---|---|
| Noto Sans SC（400/500/700 静态实例，裁剪为 GB2312 汉字与常用符号） | `ui/assets/fonts/` | OFL 1.1 |
| IBM Plex Mono（Regular、Medium） | `ui/assets/fonts/` | OFL 1.1 |
| Lucide 图标（取用到的 SVG） | `ui/assets/icons/` | ISC |

字体与图标的许可文本在同目录（`OFL-*.txt`、`LICENSE.lucide.txt`）。`pyproject.toml` 的 `[tool.hatch.build.targets.wheel] artifacts` 把 ttf、svg、txt 打进包，约 7.5 MB。其中 `triangle-alert.svg` 按 Lucide 路径手写；`history` 是 `clock` 的别名。

Noto Sans SC 的裁剪方法：从 google/fonts 仓库的可变字体 `NotoSansSC[wght].ttf` 用 fontTools 固定字重为 400、500、700 并裁剪，重写名称表使三个字重可同时注册。

## 测试

自动化（`tests/ui/`，pytest-qt 离屏）：

- `test_plate_grid.py`：编号与 domain 一致、点击与选择信号、选择上限、键盘、六种状态与两种模式绘制、非法管号。
- `test_widgets.py`：DataTable 空状态、Toast 显示与消失、按钮高度、SideNav、步骤条等绘制、全部图标可加载。
- `test_theme.py`：QSS 无未解析占位符、组件无硬编码色值、字体文件齐全且已加载、UI 不导入 simulation 与数据库。

人工：对照 `docs/design` 看展示页和监控小样截图；Windows 实机检查字体与高 DPI。
