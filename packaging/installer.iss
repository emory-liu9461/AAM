; Inno Setup script for the Aedalus Nexus Windows installer.
; (Same AppId as when it was called AM Accounting, so installing upgrades the existing copy in place.)
; Built by .github/workflows/build-windows.yml:
;   iscc /DMyAppVersion=0.1.0 packaging\installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Aedalus Nexus"
#define MyAppExe "Aedalus Nexus.exe"

[Setup]
AppId={{6E0B5D51-6A0F-4C64-9A37-2B2C1E0A8C11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Aedalus
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; the Start menu folder takes the new name (not the one from when it was called AM Accounting)
UsePreviousGroup=no
; Installs for the current user without needing an administrator; an admin can choose "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\installer
OutputBaseFilename=Aedalus-Nexus-Setup-{#MyAppVersion}
SetupIconFile=..\am_accounting\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "fileassoc"; Description: "Open company files (.amdb) with Aedalus Nexus"; GroupDescription: "File types:"

[InstallDelete]
; the program and shortcuts from when it was called AM Accounting
Type: files; Name: "{app}\AM Accounting.exe"
Type: files; Name: "{group}\AM Accounting.lnk"
Type: files; Name: "{group}\Uninstall AM Accounting.lnk"
Type: files; Name: "{autodesktop}\AM Accounting.lnk"
Type: files; Name: "{autoprograms}\AM Accounting.lnk"
Type: filesandordirs; Name: "{autoprograms}\AM Accounting"

[Files]
Source: "..\dist\Aedalus Nexus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.amdb"; ValueType: string; ValueName: ""; ValueData: "AMAccounting.CompanyFile"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\AMAccounting.CompanyFile"; ValueType: string; ValueName: ""; ValueData: "Aedalus Nexus company file"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\AMAccounting.CompanyFile\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExe},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\AMAccounting.CompanyFile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""; Tasks: fileassoc

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
