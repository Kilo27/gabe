; Inno Setup script for GabeSetup.exe. Build with packaging/windows/build.ps1.
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\Gabe.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\windows\Gabe"
#endif
#ifndef OutDir
  #define OutDir "..\..\dist\windows"
#endif

[Setup]
AppId={{6F1D5C0E-3B8A-4C55-9A57-2E4B1C9D7A10}
AppName=Gabe
AppVersion={#AppVersion}
AppPublisher=Gabe
DefaultDirName={localappdata}\Programs\Gabe
DefaultGroupName=Gabe
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=GabeSetup
SetupIconFile=Gabe.ico
UninstallDisplayIcon={app}\Gabe.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Gabe"; Filename: "{app}\Gabe.exe"
Name: "{autodesktop}\Gabe"; Filename: "{app}\Gabe.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Gabe.exe"; Description: "{cm:LaunchProgram,Gabe}"; Flags: nowait postinstall skipifsilent
