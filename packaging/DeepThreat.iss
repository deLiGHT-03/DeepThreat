#define AppName "DeepThreat"
#define AppVersion "1.0.0"
#define AppPublisher "DeepThreat"
#define AppExeName "DeepThreat.exe"

[Setup]
AppId={{A29E1C61-7114-4FB1-B84F-829686FCD593}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\DeepThreat
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=DeepThreatSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[InstallDelete]
; Remove stale app files during upgrades. User settings live separately under AppData\Roaming.
Type: files; Name: "{app}\DeepThreat.exe"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\DeepThreat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
