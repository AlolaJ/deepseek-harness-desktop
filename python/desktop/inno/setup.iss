; DeepSeek Harness installer
; -------------------------------------------
; Bilingual (zh-CN / en) following the system language, per-user/all-users
; choice at install time, and full-install vs update auto-detection:
;   * first install       -> license + directory + tasks pages
;   * update install      -> skips license/dir/tasks, keeps install dir and
;                            user data, welcome text says "updating in place"
;   * downgrade guard     -> refuses (with opt-out) if a NEWER version exists
; User data (%LOCALAPPDATA%\DeepSeekHarness) is NEVER touched by install or
; by uninstall — conversations and settings live there, outside {app}.
;
; Version defines are injected by scripts/build_installer.ps1 from
; apps/cli/package.json (e.g. 0.1.1-rc.2 -> MyAppVersion=0.1.1 and
; MyAppVersionFull=0.1.1-rc.2). The numeric form must be 1-4 dot-separated
; integers (0.1.1) for [Setup] AppVersion / registry DisplayVersion.

#ifndef MyAppVersion
  #define MyAppVersion "0.1.1"
#endif
#ifndef MyAppVersionFull
  #define MyAppVersionFull "0.1.1"
#endif

#define MyAppName "DeepSeek Harness"
#define MyAppDisplayName "DeepSeek Harness"
#define MyAppGUID "{8F3C0B2A-1E2D-4B49-8A6F-2E5C3A9B0F11}"
#define MyAppMutex "Global\DeepSeek Harness.dsh-desktop-8f3c0b2a-1e2d-4b49-8a6f-2e5c3a9b0f11"
; This suffix must match the runtime AppId so install + update + version
; detection all share one identity (this is {MyAppGUID} as Inno stores it):
#define UninstallKeyName "{#MyAppGUID}_is1"

; [Files] source root, relative to SourceDir. build_installer.ps1 overrides it
; with a short-path junction (/DSrcDist=C:\dsh-dist) because ISCC cannot READ
; source files whose absolute path exceeds MAX_PATH (~260, no longPathAware in
; its manifest) — the onedir tree legitimately contains 276-char paths.
#ifndef SrcDist
  #define SrcDist ".build\dist\dsh-desktop"
#endif

[Setup]
AppId={{#MyAppGUID}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersionFull}
AppPublisher=DeepSeek
AppComments=DeepSeek Harness desktop client (web UI in a WebView2 window)
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
SourceDir=..
OutputDir=dist
OutputBaseFilename=DeepSeekHarness-{#MyAppVersionFull}-Setup
SetupIconFile=assets\icon.ico
; LicenseFile resolves relative to SourceDir (= python/desktop); the repo-root
; LICENSE is TWO levels up (python/desktop -> python -> repo root).
LicenseFile=..\..\LICENSE
UninstallDisplayIcon={app}\DeepSeekHarness.exe
UninstallDisplayName={#MyAppDisplayName} {#MyAppVersionFull}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
AppMutex={#MyAppMutex}
LanguageDetectionMethod=locale
ShowLanguageDialog=no
CloseApplications=no
RestartIfNeededByRun=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Vendored copy of the official Inno translation (winget's per-user install
; doesn't ship Chinese). {#SourcePath} = directory of this .iss file.
Name: "chinesesimp"; MessagesFile: "{#SourcePath}ChineseSimplified.isl"

[Tasks]
; visible only on a FULL install (page is skipped on updates)
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SrcDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Fixed shortcut names -> re-running an update OVERWRITES them instead of
; duplicating; on update we skip creating them entirely (their targets never
; move because {app} is reused).
Name: "{group}\{#MyAppName}"; Filename: "{app}\DeepSeekHarness.exe"; WorkingDir: "{app}"; Check: not IsUpdateInstall
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\DeepSeekHarness.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: not IsUpdateInstall

[Run]
Filename: "{app}\DeepSeekHarness.exe"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
english.CreateDesktopIcon=Create a desktop shortcut
english.AdditionalIcons=Additional shortcuts:
english.LaunchProgram=Launch {#MyAppName} now
english.UpdateWelcome1=Update installation
english.UpdateWelcome2=A previous installation (version %1) was found. It will be updated to %2 in place. Your data and conversation history are preserved.
english.DowngradeWarningMsg=Version %1 is already installed. Continuing would DOWNGRADE it to %2. Continue anyway?
chinesesimp.CreateDesktopIcon=创建桌面快捷方式
chinesesimp.AdditionalIcons=附加快捷方式：
chinesesimp.LaunchProgram=立即启动 {#MyAppName}
chinesesimp.UpdateWelcome1=更新安装
chinesesimp.UpdateWelcome2=检测到已安装版本 %1，将原地更新到 %2。您的数据与对话记录会完整保留。
chinesesimp.DowngradeWarningMsg=已安装版本 %1，继续安装将降级到 %2。仍要继续吗？

[Code]
const
  UninstallRegBase   = 'Software\Microsoft\Windows\CurrentVersion\Uninstall';
  UninstallRegKey    = '{#UninstallKeyName}';

var
  IsUpdateFlag: Boolean;

{ --- installed-version helpers -------------------------------------------- }

function GetInstalledVersion(): String;
var
  KeyName, V: String;
begin
  Result := '';
  KeyName := UninstallRegBase + '\' + UninstallRegKey;
  if RegQueryStringValue(HKCU, KeyName, 'DisplayVersion', V) or
     RegQueryStringValue(HKLM, KeyName, 'DisplayVersion', V) then
    Result := V;
end;

{ Pack "1.2.3" / "10" / "0.1.1-rc.2" into one comparable integer (major.mior.rev.build,
  4 digits per part). Non-numeric suffixes (prerelease) are ignored for the
  downgrade guard, which is deliberate: 0.1.1-rc.2 must NOT beat 0.1.1. }
function NumericKey(const S: String): Int64;
var
  Parts: TStringList;
  i, n, maxParts: Integer;
begin
  Parts := TStringList.Create;
  try
    Parts.Delimiter := '.';
    Parts.DelimitedText := S;
    maxParts := Parts.Count - 1;
    if maxParts > 3 then maxParts := 3;
    Result := 0;
    for i := 0 to maxParts do begin
      n := StrToIntDef(Parts.Strings[i], 0);
      if n < 0 then n := 0;
      if n > 9999 then n := 9999;
      Result := Result * 10000 + n;
    end;
  finally
    Parts.Free;
  end;
end;

{ --- install-time --------------------------------------------------------- }

function InitializeSetup(): Boolean;
var
  Installed: String;
  Msg: String;
begin
  Result := True;
  Installed := GetInstalledVersion();
  IsUpdateFlag := Installed <> '';

  if IsUpdateFlag then begin
    if NumericKey(Installed) > NumericKey('{#MyAppVersion}') then begin
      Msg := FmtMessage(CustomMessage('DowngradeWarningMsg'), [Installed, '{#MyAppVersionFull}']);
      if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
    end;
  end;
end;

function IsUpdateInstall(): Boolean;
begin
  Result := IsUpdateFlag;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if IsUpdateInstall then
    if (PageID = wpLicense) or (PageID = wpSelectDir) or (PageID = wpSelectTasks) then
      Result := True;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Installed: String;
begin
  if (CurPageID = wpWelcome) and IsUpdateFlag then begin
    Installed := GetInstalledVersion();
    WizardForm.WelcomeLabel1.Caption := CustomMessage('UpdateWelcome1');
    WizardForm.WelcomeLabel2.Caption := FmtMessage(CustomMessage('UpdateWelcome2'), [Installed, '{#MyAppVersionFull}']);
  end;
end;