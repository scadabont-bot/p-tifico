#define MyAppName "p-tifico-backup"
#define MyAppVersion "2.5.6"
#define MyAppPublisher "TIFICO"
#define MyAppExeName "p-tifico-backup.exe"

[Setup]
AppId={{8E147B9B-4775-4D21-8C94-1A9BFD4592C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=p-tifico-backup Windows Installer
VersionInfoProductName={#MyAppName}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=p-tifico-backup-Setup
SetupIconFile=..\SCADA_BONT.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Buat ikon di Desktop"; GroupDescription: "Ikon tambahan:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SCADA_BONT.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\RUNMONITORING FINAL.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\G1MONITOR.jpg.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\G2MONITOR.jpg.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SYSTEMCONFIG_DARK.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\AUXILIARY_MATCHED.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MANUAL_MAPPING_G1_G2.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MANUAL_COMMUNICATION_SETTINGS.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MANUAL_PROCESS_TREND.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MANUAL_BACKUP_EXPLORER.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MANUAL_MONITOR_TV_BREAKER.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MONITOR V55.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CommunicationConfig.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MQTTConfig.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\UIRealtimeBindings.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MonitorTVConfig.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CBZ8000AppData.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ListSetting *.csv"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\StatusSetting *.csv"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
Type: files; Name: "{app}\SCADA BONT*.exe"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\__pycache__"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\SCADA_BONT.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\SCADA_BONT.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Jalankan {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
