#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#ifndef CompressionMode
  #define CompressionMode "lzma2/normal"
#endif

#define MyAppName "VigorSync Mirror"
#define MyAppDisplayName "VigorSync 动作遥测"
#define MyAppPublisher "VigorSync"
#define MyAppURL "https://github.com/qyl245/VigorSync"
#define MyAppExeName "VigorSyncDesktop.exe"

[Setup]
AppId={{A83AB8AB-8D63-4F79-9EF0-5C2F6A9D6B73}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\VigorSync Mirror
DefaultGroupName=VigorSync
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\dist\installer
OutputBaseFilename=VigorSync-Mirror-{#MyAppVersion}-win-x64-setup
Compression={#CompressionMode}
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppDisplayName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppDisplayName} installer
VersionInfoProductName={#MyAppDisplayName}
VersionInfoProductVersion={#MyAppVersion}
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no
AppMutex=Local\VigorSyncDesktop
InfoAfterFile=info-after.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce
Name: "startup"; Description: "Start VigorSync when this user signs in"; GroupDescription: "Windows startup:"; Flags: unchecked
Name: "firewall"; Description: "Allow phone access on private networks (TCP 8011, UDP 8765)"; GroupDescription: "Network access:"; Flags: checkedonce

[Files]
Source: "..\..\dist\installer-staging\VigorSyncMirror\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\VigorSync Mirror"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\VigorSync Mirror"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{commonstartup}\VigorSync Mirror"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""VigorSync Core TCP 8011"" dir=in action=allow protocol=TCP localport=8011 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""VigorSync Discovery UDP 8765"" dir=in action=allow protocol=UDP localport=8765 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "{app}\{#MyAppExeName}"; Description: "Launch VigorSync Mirror"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""VigorSync Core TCP 8011"""; Flags: runhidden; RunOnceId: "RemoveCoreFirewallRule"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""VigorSync Discovery UDP 8765"""; Flags: runhidden; RunOnceId: "RemoveDiscoveryFirewallRule"
