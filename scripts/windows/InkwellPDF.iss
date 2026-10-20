; Inno Setup script: turns dist\InkwellPDF into a normal Windows installer.
; Built by scripts\build_windows.ps1, which passes /DAppVersion=x.y.z and /DAppUrl=...

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppUrl
  #define AppUrl ""
#endif

[Setup]
AppId={{3E7A9C41-5B2D-4F86-A1C3-9D04E6B27F58}
AppName=Inkwell PDF
AppVersion={#AppVersion}
AppPublisher=Inkwell PDF contributors
AppPublisherURL={#AppUrl}
DefaultDirName={autopf}\InkwellPDF
DefaultGroupName=Inkwell PDF
DisableProgramGroupPage=yes
; installs for the current user, so no administrator rights are needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=InkwellPDF-{#AppVersion}-Windows-Setup
SetupIconFile=..\..\dist\InkwellPDF.ico
UninstallDisplayIcon={app}\InkwellPDF.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\InkwellPDF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Inkwell PDF"; Filename: "{app}\InkwellPDF.exe"
Name: "{group}\Uninstall Inkwell PDF"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Inkwell PDF"; Filename: "{app}\InkwellPDF.exe"; Tasks: desktopicon

[Registry]
; adds Inkwell PDF to "Open with" for PDFs without taking over the default PDF app
Root: HKA; Subkey: "Software\Classes\InkwellPDF.Document"; ValueType: string; ValueName: ""; ValueData: "PDF document"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\InkwellPDF.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\InkwellPDF.exe,0"
Root: HKA; Subkey: "Software\Classes\InkwellPDF.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\InkwellPDF.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "InkwellPDF.Document"; ValueData: ""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\InkwellPDF.exe"; Description: "Open Inkwell PDF now"; Flags: nowait postinstall skipifsilent
