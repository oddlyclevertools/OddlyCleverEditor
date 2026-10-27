; OddlyCleverEditor: full offline Windows installer. Built by installer/build.py, which stages _stage/ first.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef MaxDir
  #define MaxDir 110
#endif

[Setup]
AppId={{4D6A1ED5-298E-40E8-AE5A-748B2DA88BC0}
AppName=OddlyCleverEditor
AppVersion={#AppVersion}
AppVerName=OddlyCleverEditor {#AppVersion}
AppPublisher=OddlyCleverEditor contributors
AppComments=Free, offline captions and Shorts on your NVIDIA GPU.
DefaultDirName={localappdata}\Programs\OddlyCleverEditor
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=_stage\LICENSE
SetupIconFile=_stage\oddlyclever.ico
UninstallDisplayIcon={app}\oddlyclever.ico
UninstallDisplayName=OddlyCleverEditor
WizardStyle=modern
OutputDir=dist
OutputBaseFilename=OddlyCleverEditor-setup-{#AppVersion}
; Installer compression only runs on the CPU, so keep it light: the model weights don't shrink
; anyway (they're stored uncompressed below) and the DLLs compress well enough on "fast".
Compression=lzma2/fast
SolidCompression=no
LZMANumBlockThreads=4
#ifdef Split
; For GitHub Releases (each file must be under 2 GiB): setup.exe plus -1.bin, -2.bin... next to it.
DiskSpanning=yes
DiskSliceSize=2000000000
#else
DiskSpanning=no
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The leading "\" anchors the exclude to the stage root: a bare "models\*" also drops every
; package folder named models (tokenizers\models, onnxruntime\...\models) and breaks imports.
Source: "_stage\*"; Excludes: "\models\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "_stage\models\*"; DestDir: "{app}\models"; Flags: recursesubdirs createallsubdirs ignoreversion nocompression

[Icons]
Name: "{autoprograms}\OddlyCleverEditor"; Filename: "{app}\oddlyclever.cmd"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{app}\oddlyclever.ico"; Comment: "Open OddlyCleverEditor in your browser"
Name: "{autodesktop}\OddlyCleverEditor"; Filename: "{app}\oddlyclever.cmd"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{app}\oddlyclever.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\oddlyclever.cmd"; Description: "Start OddlyCleverEditor now"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; Our own folders, including byte-code caches made at run time. Your projects (in your user folder)
; are never touched, and {app} itself is only removed if it's empty.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: dirifempty; Name: "{app}"

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and (Length(WizardDirValue) > {#MaxDir}) then
  begin
    MsgBox('That folder path is too long for Windows (over {#MaxDir} characters).' + #13#10 +
      'Please pick a shorter folder, such as the suggested one.', mbError, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';  // also covers silent installs with /DIR=, which skip the folder page
  if Length(ExpandConstant('{app}')) > {#MaxDir} then
    Result := 'The install folder path is too long for Windows (over {#MaxDir} characters). Pick a shorter folder.';
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not FileExists(ExpandConstant('{sys}\nvcuda.dll')) then
    Result := MsgBox('No NVIDIA GPU driver was found on this PC.' + #13#10 + #13#10 +
      'OddlyCleverEditor needs an NVIDIA graphics card with a current driver to transcribe and render.' + #13#10 +
      'Install anyway?', mbConfirmation, MB_YESNO) = IDYES;
end;
