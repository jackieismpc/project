# Windows 原生异常 0x80040155

日期：2026-11-17；基线：`bf5c93d`（`feat/client-feedback-20261116`）。状态：已定位并修复 Windows pytest 配置；未更改应用打印逻辑。

## 根因

`0x80040155` 是 Windows HRESULT `REGDB_E_IIDNOTREG`（接口 IID 未注册）。本项目的 UI 测试在 Windows 上默认设置 `QT_QPA_PLATFORM=offscreen`。Qt 6.12.0 `QPrinter` 初始化需要访问 Windows 打印支持，而该组合会触发 faulthandler 的 Windows first-chance exception handler：一条仅有 `QApplication` 和 `QPrinter(QPrinter.PrinterMode.HighResolution)` 的独立子进程，退出码为 0，但 stderr 中有 34 次 `Windows fatal exception: code 0x80040155`。将同一进程切换至 Qt 原生 `windows` QPA 后，退出码仍为 0，异常输出为 0。

同样的对照出现在监控页测试：单个 `test_preview_frames_reach_the_view_only_on_the_main_thread` 在 offscreen 下 34 次异常打印、测试通过；原生 Windows 平台下 0 次打印、测试通过。监控页全文件在原生平台下连续三个独立进程均为退出码 0、9 passed、0 次异常打印。faulthandler 当时显示的 `simulation/image_synth.py:add_noise`、`clock.sleep` 或页面构造栈只是 first-chance 异常发生时其他线程的当前 Python 栈，不是异常抛出位置；图像合成和线程 COM 初始化并非根因。

## 修复与回归

在 `tests/conftest.py` 将 Windows 默认 Qt 平台设为 `windows`，其他平台仍用 `offscreen`。使用 `setdefault` 保留调用方显式选择；UI 和报告测试夹具中已有的 `setdefault("offscreen")` 不会覆盖根夹具的 Windows 默认值。新增 `tests/test_windows_qt_printing.py`，验证清空平台变量后根 pytest 配置选择原生 Windows QPA，并在带 faulthandler 的独立原生 Qt 子进程中创建 QPrinter，断言退出码为 0 且没有 0x80040155 打印。

已验证：新打印回归 2 项通过；监控页文件在默认配置下连续三次独立运行，每次 9 项通过、0 次 fatal 输出；报告中心 5 项通过（31.87 秒）。`QT_ACCESSIBILITY=0` 并不能消除 offscreen 下异常，因此没有把问题归因于 UI Automation，也没有关闭 faulthandler、过滤异常或改动系统 COM 注册表。

## 证据边界

当前定位依据为最小复现和 QPA 平台对照；未通过 WinDbg 获取到原生调用栈，因此不进一步声称是哪一个 Qt/Windows 打印 API 内部调用导致 IID 请求。已证实的修复范围是 Windows UI 测试使用与其打印后端匹配的原生 Qt 平台，并由独立回归锁定该行为。
