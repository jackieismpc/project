# Qt 设计系统与组件展示页

对应阶段：P2。把 `docs/design/*.dc.html` 的视觉规格落到 PySide6，供后续 12 个页面复用。

## 目的

- 提供 token、全局 QSS、内置字体与图标，保证离线、Windows 与 macOS 显示一致。
- 提供 Card、StatCard、ReadoutCard、Button、StatusBadge、DataTable、Toast、ConfirmDialog、StepIndicator、SideNav、Tabs、Sparkline、IconLoader 和 PlateGrid、TubeStrip。
- 应用外壳（P2 评审后追加）：QuickToolbar（顶部快捷栏）与 AppShell（快捷栏、可自动隐藏的侧栏和内容区），详见 [app-shell.md](app-shell.md)。
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

应用外壳要点（详见 [app-shell.md](app-shell.md)）：

- 页面不再自己摆侧栏，统一用 `AppShell(content)`：顶部 56px 的 `QuickToolbar`，下方左侧 `SideNav`，右侧内容区。
- 侧栏默认自动隐藏（覆盖层滑出，不挤压内容），可在品牌行用图钉按钮固定（208px，占布局）。模式由 `ShellSettings` 持久化。
- 快捷操作在 `quick_toolbar.DEFAULT_ACTIONS` 中声明式定义，带权限与快捷键；宽度不足时收进「更多」。
- 新增图标（Lucide 手写路径，ISC）：`menu`、`pin`、`pin-off`、`folder-open`、`plug`、`unplug`、`languages`、`ellipsis`。
- 新增 QSS 角色：`QFrame[role="toolbar"]`、`QFrame[role="vsep"]`、`QToolButton[role="quick"]`、`QLabel[role="status"]`，以及 `QMenu` 样式，均来自 token。

P8、P9 追加的组件（代码都在 `ui/widgets/`）：

- `PlateGrid.set_row_labels_visible(bool)`：是否显示列头的 `Row01` 标签。新试验向导的管位选择与设计稿一致，不显示列头；监控页显示。与 `set_countdowns_visible` 互相独立。
- `edit_table.py` 的 `EditTable` 和 `EditColumn`：可编辑表格，自绘单元格，样式与 `DataTable` 一致（无网格、行分隔线、等宽数字）。列有只读、文字、数字（可留空）和选项四种；内容保存在单元格里，用 `cell_text` / `cell_choice` 读取，`commit_editor` 提交正在编辑的单元格。向导的样品表和模板编辑器使用。
- `reagent_result.py` 的 `ReagentResultView`：试剂校验结果展示块（批号、结论、剩余天数、标示灵敏度），结论用文字加颜色表达。向导的试剂卡片、试剂溯源页和锁定说明共用。
- `marks.WellState.ERROR`（P9）：读数或分析失败、没有结果的孔，危险色细环加淡底。图例不列出，只在故障时出现；文字「异常」。
- `video_view.VideoView`：暗底视频区，图像按比例居中，没有画面时显示说明。实时预览和回放共用。
- `tube_image_view.TubeImageView`：单管裁剪图加掩码叠加（凝胶 `gel_overlay` 色 38% 填充加阳性色描边，试管主色虚线 6/5）。新增颜色 token `gel_overlay`。
- `replay_player.ReplayPlayer`：小型播放器（播放/暂停、进度条、时间），用 OpenCV 解码，不用 QtMultimedia。
- `alert_stack.AlertStack`：告警横幅栈，按键去重，最多 4 条，可关闭；复用 `QFrame[role="banner"]` 样式。
- `mini_plate.MiniPlate`：80 个 16px 小孔的孔板缩略图（工作台）。`progress_bar.ProgressBar`：8px 进度条。
- `ui/background.py` 的 `BackgroundRunner`：用 Qt 线程池把读盘和解码放到后台，回调经排队信号回到主线程。
- QSS 新增：`QCheckBox`（44px 触控高度）。

展示页：

```bash
uv run python -m gelinspect.ui.gallery
QT_QPA_PLATFORM=offscreen uv run python -m gelinspect.ui.gallery \
    --screenshot out.png --size 1280x800 [--page gallery|monitor] [--full] [--scale 2] \
    [--nav-mode auto|pinned] [--nav-open]
```

`--nav-mode` 指定侧栏模式，`--nav-open` 在自动隐藏模式下展开侧栏，两者用于截图。截图时使用内存设置并关闭动画，不读写本机偏好。

## 权限

组件本身不判断角色。快捷栏按调用方传入的权限集合（`set_user_permissions`）启用或禁用操作，权限矩阵见 `domain/permissions.py` 与 [permissions.md](permissions.md)。

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
- `test_quick_toolbar.py`、`test_app_shell.py`：快捷栏与应用外壳，场景见 [app-shell.md](app-shell.md)。
- `test_theme.py`：QSS 无未解析占位符、组件无硬编码色值、字体文件齐全且已加载、UI 不导入 simulation 与数据库。

人工：对照 `docs/design` 看展示页和监控小样截图；Windows 实机检查字体与高 DPI；触摸屏与真实窗口的悬停行为见 app-shell.md。
