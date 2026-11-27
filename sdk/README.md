# 海康 MVS 本地运行库

Windows x64 运行库保存在 `sdk/Win64_x64/`。二进制文件只在本地保存，不提交 Git；
新电脑或构建环境需要先按下面步骤准备。当前副本来自本机 MVS 4.8.1.2。

在项目根目录执行 PowerShell：

```powershell
New-Item -ItemType Directory -Path sdk -Force | Out-Null
Copy-Item -LiteralPath 'C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64' -Destination '.\sdk\Win64_x64' -Recurse
uv run python -c "from gelinspect.infrastructure.video.hikvision_sdk import probe_hikvision_sdk; print(probe_hikvision_sdk())"
```

请复制完整架构目录，包括 `ThirdParty/`、DLL、CTI 和配置文件，不能只复制
`MvCameraControl.dll`。更新已有副本时先将旧目录移出项目，再复制新版，避免混用版本。
探测结果应为 `found: True`、`source: app_bundled`，路径指向项目中的 DLL。

发现优先级为：配置项 `video.options.sdk_path` → 项目/程序目录下的 `sdk/` →
环境变量 `GELINSPECT_HIKVISION_SDK` → 系统 MVS 安装目录。
Nuitka 发布包从程序目录查找 SDK，无需保留源码路径。

`uv run python scripts/build.py` 在 Windows 上将当前进程架构的运行库完整复制到
发布目录的 `sdk/`，并纳入现有文件清单签名和 ZIP/安装包。
缺少本地 DLL 时构建会明确报错，避免生成遗漏 SDK 的 Windows 发布包。
macOS 需要对应的原生 framework，不能使用此 Windows DLL 副本。

SDK DLL 不包含 Windows 相机内核驱动；目标电脑使用 USB/GigE 设备时仍需按厂商要求
安装对应驱动。厂商运行库的分发应遵循 MVS 随附许可。
