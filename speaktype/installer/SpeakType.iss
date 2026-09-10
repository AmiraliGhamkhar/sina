; SpeakType Installer Script for Inno Setup
; This creates a professional Windows installer for SpeakType

#define MyAppName "SpeakType"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "SpeakType"
#define MyAppURL "https://github.com/DrNightmare/speaktype"
#define MyAppExeName "SpeakType.App.exe"

[Setup]
AppId={{A7B8C9D0-E1F2-4A5B-8C9D-0E1F2A3B4C5D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=LICENSE.txt
OutputDir=Output
OutputBaseFilename=SpeakTypeSetup
; SetupIconFile=..\src\SpeakType.App\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenu"; Description: "Create Start Menu entry"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; Main application files
Source: "staging\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Dependency download script
Source: "download-dependencies.ps1"; DestDir: "{app}"; Flags: ignoreversion
; Documentation
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\tools\whisper\readme.md"; DestDir: "{app}\tools\whisper"; Flags: ignoreversion
Source: "..\models\readme.md"; DestDir: "{app}\models"; Flags: ignoreversion


[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Download dependencies during installation
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File ""{app}\download-dependencies.ps1"" -InstallDir ""{app}"""; StatusMsg: "Downloading whisper.cpp and AI models..."; Flags: waituntilterminated
; Offer to launch app after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Create tools and models directories if they don't exist
    ForceDirectories(ExpandConstant('{app}\tools\whisper'));
    ForceDirectories(ExpandConstant('{app}\models'));
  end;
end;
