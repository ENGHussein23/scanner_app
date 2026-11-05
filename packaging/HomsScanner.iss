; Inno Setup script - build with:  iscc packaging\HomsScanner.iss   (output: dist\HomsScanner-Setup.exe)
; Per-user install (no administrator rights needed). Registers:
;   * start with Windows (HKCU Run)
;   * homsscan://start  -> lets the web page start the service when it is not running
#define AppName "HomsScanner"
#define AppVersion "1.0.0"

[Setup]
AppId={{6F1D2B5C-8E4A-4C57-9A0B-3C7D5E1A9F42}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Homs Governorate
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=HomsScanner-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppName}.exe
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Files]
Source: "..\dist\HomsScanner\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\{#AppName} (الإعدادات)"; Filename: "{app}\{#AppName}.exe"; Parameters: "--settings"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "HomsScanner"; ValueData: """{app}\{#AppName}.exe"" --background"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\homsscan"; ValueType: string; ValueData: "URL:HomsScanner"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\homsscan"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\homsscan\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppName}.exe"" --background ""%1"""

[Run]
Filename: "{app}\{#AppName}.exe"; Parameters: "--settings"; Description: "تشغيل الخدمة وفتح الإعدادات"; Flags: nowait postinstall skipifsilent

[Code]
procedure StopRunningService();
var
  code: Integer;
begin
  Exec('taskkill.exe', '/F /IM HomsScanner.exe', '', SW_HIDE, ewWaitUntilTerminated, code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then StopRunningService();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then StopRunningService();
end;
