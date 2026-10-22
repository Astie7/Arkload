; Inno Setup script for ArkLoad - Modern Download Manager.
;
; Build it with:
;     iscc packaging\installer.iss
;
; The installer puts the folder build into "Program Files" (or the folder the user picks),
; creates the shortcuts and an uninstaller.  The user's settings, download list and add-ons stay
; in %LOCALAPPDATA%\ArkLoad - they are never touched by the installation or the uninstallation.

#define AppName "ArkLoad - Modern Download Manager"
#define AppShortName "ArkLoad"
#define AppVersion "1.0"
#define AppPublisher "Astie7"
#define AppURL "https://github.com/Astie7/Arkload"
#define AppExeName "ArkLoad.exe"
#define SourceDir "..\dist\ArkLoad"
#define IconFile "assets\arkload.ico"

[Setup]
AppId={{7B4A0C1E-9C3D-4E6A-9C2E-2A1D8F0B5C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\ArkLoad
DefaultGroupName={#AppShortName}
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=ArkLoad-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
MinVersion=10.0
ShowLanguageDialog=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "jdownloaderengine"; Description: "Install the JDownloader 2 engine (GPLv3+/AGPL)"; GroupDescription: "The download engine for the links of other hosts:"; Flags: unchecked; Check: JDownloaderEngineTask

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\arkload.ico"; DestDir: "{app}"; Flags: ignoreversion
; the engine is a separate program - the user can leave the task unticked and point ArkLoad at
; another build in the preferences
Source: "jdownloader\engine\*"; DestDir: "{localappdata}\ArkLoad\engine"; Flags: ignoreversion recursesubdirs createallsubdirs; Tasks: jdownloaderengine

[Icons]
Name: "{group}\{#AppShortName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppShortName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppShortName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
function JDownloaderEngineTask(): Boolean;
begin
  { the task is only offered when the engine was built next to the installer }
  Result := DirExists(ExpandConstant('{src}\jdownloader\engine'));
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if not FileExists(ExpandConstant('{src}\{#SourceDir}\{#AppExeName}')) then
  begin
    MsgBox('The application was not found in ' + '{#SourceDir}' + #13#10 +
           'Build it first:  pyinstaller packaging\arkload.spec', mbError, MB_OK);
    Result := False;
  end;
end;
