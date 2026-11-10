; DiffScope Windows 安装包脚本（Inno Setup 6）
; 本地编译：先运行 packaging\build_windows.bat 生成 dist\DiffScope\，
; 然后用 Inno Setup Compiler (ISCC.exe) 编译本脚本：
;   iscc packaging\installer.iss
; 产物：dist\DiffScope-windows-setup.exe

#define AppName "DiffScope"
#define AppVersion "2.2.0"
#define AppPublisher "GluckMI"
#define AppExe "DiffScope.exe"

[Setup]
AppId={{8C7B4D5E-1A2B-4C3D-9E8F-DIFFSCOPE100}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=DiffScope-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "sendto"; Description: "添加到「发送到」菜单（便于右键发送文件到 DiffScope）"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\DiffScope\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{user.sendto}\{#AppName}.lnk"; Filename: "{app}\{#AppExe}"; Tasks: sendto

[Run]
Filename: "{app}\{#AppExe}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理程序自带的便携数据目录（如有）
Type: filesandordirs; Name: "{app}\Data"
