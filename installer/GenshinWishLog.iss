; Inno Setup 脚本：把 dist\GenshinWishLog 文件夹打成安装包。
; 用法：ISCC.exe /DMyAppVersion=0.2.0 installer\GenshinWishLog.iss
; 本文件以 UTF-8 (带 BOM) 保存，Inno Setup 才能正确显示中文。

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "原神抽卡记录"
#define MyAppExe "GenshinWishLog.exe"

[Setup]
AppId={{E55F1A55-5CBC-4311-8E3B-EF57182406FA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\GenshinWishLog
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=GenshinWishLog-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 只给当前用户安装：不需要管理员权限，也不会弹出 UAC 提示
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\GenshinWishLog\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
