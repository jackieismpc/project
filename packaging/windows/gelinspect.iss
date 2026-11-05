; GelInspect 的 Windows 安装程序（Inno Setup 6，可选）。
;
; 先构建正式包：  uv run python scripts/build.py
; 再编译安装程序："C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\windows\gelinspect.iss
; 输出：dist\GelInspect-<版本>-windows-setup.exe
;
; 按当前用户安装（不需要管理员权限），程序放在 %LOCALAPPDATA%\Programs\GelInspect；
; 数据目录（数据库、密钥、日志）在 %LOCALAPPDATA%\GelInspect，卸载时**不删除**数据。

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6C1F2B7E-5C1E-4C5B-9F0A-7A2E8D3B4C11}
AppName=GelInspect
AppVersion={#AppVersion}
AppPublisher=[版权方名称]
DefaultDirName={autopf}\GelInspect
DefaultGroupName=GelInspect
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=GelInspect-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\GelInspect.exe

[Languages]
; 安装向导自身的文字用 Inno Setup 自带的英文；需要中文向导时换成社区维护的 ChineseSimplified.isl。
Name: "default"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\GelInspect\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GelInspect"; Filename: "{app}\GelInspect.exe"
Name: "{group}\第三方许可声明"; Filename: "{app}\licenses\THIRD_PARTY_NOTICES.txt"
Name: "{autodesktop}\GelInspect"; Filename: "{app}\GelInspect.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Run]
Filename: "{app}\GelInspect.exe"; Description: "启动 GelInspect"; Flags: nowait postinstall skipifsilent
