; Inno Setup script: turns dist\LocalPDF into a normal Windows installer.
; Built by scripts\build_windows.ps1, which passes /DAppVersion=x.y.z

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8C1B6F2E-4A7D-4E0B-9C51-7A3F2D9E6B14}
AppName=LocalPDF
AppVersion={#AppVersion}
AppPublisher=LocalPDF contributors
AppPublisherURL=https://github.com/
DefaultDirName={autopf}\LocalPDF
DefaultGroupName=LocalPDF
DisableProgramGroupPage=yes
; installs for the current user, so no administrator rights are needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=LocalPDF-{#AppVersion}-Windows-Setup
SetupIconFile=..\..\dist\LocalPDF.ico
UninstallDisplayIcon={app}\LocalPDF.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\LocalPDF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LocalPDF"; Filename: "{app}\LocalPDF.exe"
Name: "{group}\Uninstall LocalPDF"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LocalPDF"; Filename: "{app}\LocalPDF.exe"; Tasks: desktopicon

[Registry]
; adds LocalPDF to "Open with" for PDFs without taking over the default PDF app
Root: HKA; Subkey: "Software\Classes\LocalPDF.Document"; ValueType: string; ValueName: ""; ValueData: "PDF document"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\LocalPDF.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\LocalPDF.exe,0"
Root: HKA; Subkey: "Software\Classes\LocalPDF.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\LocalPDF.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "LocalPDF.Document"; ValueData: ""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\LocalPDF.exe"; Description: "Open LocalPDF now"; Flags: nowait postinstall skipifsilent
