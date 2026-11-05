# LGPL 组件说明（Qt、PySide6、Shiboken6、FFmpeg）

本软件使用以下以 GNU LGPL 提供的库：

| 组件 | 许可证 | 在发布包里的位置 |
|---|---|---|
| Qt 6（PySide6-Essentials 附带的 Qt 库） | LGPL-3.0 | macOS：`Contents/MacOS/PySide6/Qt/lib/*.framework`；Windows：`PySide6/*.dll` |
| PySide6、Shiboken6（Python 绑定） | LGPL-3.0 | `PySide6/*.so`/`*.pyd`、`shiboken6/`、`libpyside6*`、`libshiboken6*` |
| FFmpeg（opencv-python-headless 附带） | LGPL-2.1 或更高 | `cv2/` 下及程序目录里的 `libav*`、`libsw*` 动态库 |

## 你的权利

- 这些库在发布包里以**独立的动态库**存在（Nuitka standalone 目录模式，不是单文件），
  你可以用自己修改或重新编译的、接口兼容的版本替换它们。替换后软件的完整性校验会报告
  「文件与发布清单不一致」并写入审计，但不会阻止运行。
- 许可证全文见同目录的 `LGPL-3.0.txt`、`GPL-3.0.txt`；OpenCV 和 FFmpeg 的许可文本见
  `THIRD_PARTY_NOTICES.txt` 中 opencv-python-headless 一节。

## 源代码

所用版本见 `THIRD_PARTY_NOTICES.txt`。对应源代码：

- Qt：https://download.qt.io/official_releases/qt/
- PySide6 / Shiboken6：https://download.qt.io/official_releases/QtForPython/
- FFmpeg 与 OpenCV：https://github.com/opencv/opencv-python （按版本标签）

> **待法务确认**：Qt 官方 FAQ 认为分发 LGPL 版 Qt 时，源代码需要由分发方自己提供或给出书面承诺，
> 只给上游链接不够。正式交付前请由权利人（`[版权方名称]`）补充书面承诺：在至少三年内应请求
> 提供上述组件对应版本的完整源代码，并写明联系方式。
