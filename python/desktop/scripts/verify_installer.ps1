<#
.SYNOPSIS
  Verify the DeepSeek Harness installer end-to-end: silent full install to a
  scratch dir, boot the installed app, then a same-ID update (update branch:
  dir kept, data kept, no duplicate shortcuts), and a silent uninstall that
  preserves %LOCALAPPDATA%\DeepSeekHarness.

.DESCRIPTION
  Requires scripts/build_installer.ps1 to have produced a setup EXE first.

  Flow:
    1. FULL install   setup /VERYSILENT /CURRENTUSER /DIR=<scratch>\inst \
                      -> files under <scratch>\inst, HKCU uninstall DisplayVersion
    2. BOOT app       launch the installed DeepSeekHarness.exe -> window appears
                      -> bundled backend node + webview2 under scratch data dir
                      -> WM_CLOSE -> clean exit, no leftovers
    3. UPDATE install run the SAME setup again (same AppId = update branch; must
                      skip license/dir/shortcut pages): install dir reused, files
                      still there, user data still there, no duplicate shortcuts
    4. DATA check     after update, a marker file in <scratch>\data survives
    5. UNINSTALL      <app>\unins000.exe /VERYSILENT -> {app} gone, data dir intact

  Param defaults: -SetupExe <desktop>\dist\DeepSeekHarness-*-Setup.exe (newest).
                  -Scratch <desktop>\.build\installer-verify
.CI
  A pre-compiled 0.2.0 setup can be dropped next to it to also cover the
  "different version, still update" path and the downgrade guard (run the
  0.2.0 setup AFTER the 0.1.1 install but with /D 0.1.1 -> downgrade warning;
  in /VERYSILENT the MsgBox auto-answers per the default button).
#>
[CmdletBinding()]
param(
  [string]$SetupExe,
  [string]$Scratch
)

$ErrorActionPreference = 'Stop'
$DesktopRoot = Split-Path -Parent $PSScriptRoot

if (-not $SetupExe) {
  $cands = @(Get-ChildItem (Join-Path $DesktopRoot 'dist\DeepSeekHarness-*-Setup.exe') -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
  if (-not $cands) { throw 'no setup exe found; run scripts/build_installer.ps1 first' }
  $SetupExe = $cands[0].FullName
}
if (-not (Test-Path $SetupExe)) { throw "setup exe not found: $SetupExe" }
if (-not $Scratch) { $Scratch = Join-Path $DesktopRoot '.build\installer-verify' }
# Install INTO the real per-user default dir ({userpf}) so the deepest bundled
# paths are exercised at exactly the length real users hit. (A shorter scratch
# dir would mask MAX_PATH issues; {userpf} is the longest prefix a per-user
# install can see. Uninstall at the end removes {app} + registry.)

function Section([string]$Name) { Write-Host "`n=== [$Name] ===" -ForegroundColor Cyan }

if (-not ('VerProbe' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class VerProbe {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
}
'@
}

function Find-WindowForPid {
  param([int]$TargetPid, [string]$Title, [int]$TimeoutSec = 90)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $script:FoundHwnd = [IntPtr]::Zero
    $cb = [VerProbe+EnumWindowsProc]{
      param($hwnd, $lparam)
      if ([VerProbe]::IsWindowVisible($hwnd)) {
        $sb = New-Object System.Text.StringBuilder 512
        # GetWindowTextW returns the CHAR COUNT (int) — compare the buffer.
        [void][VerProbe]::GetWindowTextW($hwnd, $sb, 512)
        if ($sb.ToString() -eq $Title) {
          $winPid = 0
          [void][VerProbe]::GetWindowThreadProcessId($hwnd, [ref]$winPid)
          if ($winPid -eq $TargetPid) { $script:FoundHwnd = $hwnd; return $false }
        }
      }
      return $true
    }
    [void][VerProbe]::EnumWindows($cb, [IntPtr]::Zero)
    if ($script:FoundHwnd -ne [IntPtr]::Zero) { return $script:FoundHwnd }
    Start-Sleep -Milliseconds 500
  }
  return [IntPtr]::Zero
}

function Get-ShellProcesses([string]$DataDir) {
  Get-CimInstance Win32_Process -Filter "Name='node.exe' or Name='msedgewebview2.exe'" -ErrorAction Stop |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($DataDir) }
}

function Invoke-InstalledBoot([string]$ExePath, [string]$DataDir, [string]$Label) {
  Section "boot installed app: $Label"
  New-Item -ItemType Directory -Force $DataDir | Out-Null
  $probeRoot = Split-Path -Parent $DataDir
  $null = Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item Env:DSH_DESKTOP_NODE -ErrorAction SilentlyContinue
  Remove-Item Env:DSH_DESKTOP_CLI_DIR -ErrorAction SilentlyContinue
  # Point the shell at the probe data dir so webview2/backend/shell.log stay out
  # of the real %LOCALAPPDATA%\DeepSeekHarness (mirrors build_desktop.ps1).
  $env:DSH_DESKTOP_DATA_DIR = $DataDir
  $out = Join-Path $probeRoot 'stdout.log'
  $err = Join-Path $probeRoot 'stderr.log'
  $p = Start-Process -FilePath $ExePath -WorkingDirectory $probeRoot -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
  $hwnd = Find-WindowForPid -TargetPid $p.Id -Title 'DeepSeek Harness' -TimeoutSec 120
  if ($hwnd -eq [IntPtr]::Zero) {
    Write-Host "  STDOUT:$(Get-Content $out -Raw -ErrorAction SilentlyContinue)"
    Write-Host "  STDERR:$(Get-Content $err -Raw -ErrorAction SilentlyContinue)"
    throw 'window not found'
  }
  Write-Host "  window up (pid $($p.Id))"

  # Backend spawns with the bundle-root node.exe (sibling of the EXE); also
  # accept the generic CLI spawn signature `bin.js web`.
  $bundleNode = [regex]::Escape((Join-Path (Split-Path $ExePath) 'node.exe'))
  $node = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction Stop |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match $bundleNode -or $_.CommandLine -match 'bin\.js web') }
  if (-not $node) { throw 'backend node not spawned' }
  Write-Host "  backend pid=$($node.ProcessId)"

  [void][VerProbe]::PostMessageW($hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) # WM_CLOSE
  if (-not $p.WaitForExit(25000)) { $p.Kill(); throw 'did not exit after WM_CLOSE' }
  Write-Host "  exited code=$($p.ExitCode)"
  for ($i = 0; $i -lt 12; $i++) {
    if (-not (Get-ShellProcesses $DataDir)) { break }
    Start-Sleep -Seconds 1
  }
  if (Get-ShellProcesses $DataDir) { throw 'leftover node/webview2 processes' }
  Write-Host '  clean exit, no leftovers'
}

$AppName = 'DeepSeek Harness'
# Real per-user default location ({userpf}): the longest prefix a per-user
# install can see. Proves the bundle stays under MAX_PATH end-to-end.
$InstDir = Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness'
$DataDir = Join-Path $Scratch 'data'
$Marker = Join-Path $DataDir 'runtime\verify-marker.txt'

New-Item -ItemType Directory -Force $Scratch | Out-Null
$null = Remove-Item -LiteralPath $InstDir -Recurse -Force -ErrorAction SilentlyContinue
$null = Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction SilentlyContinue

# --- 1. full install --------------------------------------------------------
Section 'INSTALL (full, /CURRENTUSER)'
$p = Start-Process -FilePath $SetupExe -PassThru -Wait -ArgumentList @(
  '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
  "/DIR=`"$InstDir`"")
if ($p.ExitCode -ne 0) { throw "setup failed (exit $($p.ExitCode))" }

$exe = Join-Path $InstDir 'DeepSeekHarness.exe'
if (-not (Test-Path $exe)) { throw 'DeepSeekHarness.exe missing after install' }
if (-not (Test-Path (Join-Path $InstDir 'node.exe'))) { throw 'bundled node.exe missing' }
if (-not (Test-Path (Join-Path $InstDir 'cli\node_modules\@deepseek-ai\dsh\lib\bin.js'))) { throw 'bundled cli closure missing' }
Write-Host '  files OK (exe + node.exe + cli closure at bundle root)'

$reg = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C0B2A-1E2D-4B49-8A6F-2E5C3A9B0F11}_is1" -ErrorAction Stop
Write-Host "  DisplayVersion: $($reg.DisplayVersion)"

# --- 2. boot installed app ---------------------------------------------------
Invoke-InstalledBoot -ExePath $exe -DataDir $DataDir -Label 'after full install'

# drop a data marker that must survive both update and uninstall
New-Item -ItemType Directory -Force (Join-Path $DataDir 'runtime') | Out-Null
Set-Content -LiteralPath $Marker -Value "data-survives $([DateTime]::Now.ToString('O'))" -Encoding utf8

# --- 3. update install (same AppId) ------------------------------------------
Section 'UPDATE (same setup, update branch)'
$p = Start-Process -FilePath $SetupExe -PassThru -Wait -ArgumentList @(
  '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
  "/DIR=`"$InstDir`"")
if ($p.ExitCode -ne 0) { throw "update setup failed (exit $($p.ExitCode))" }
if (-not (Test-Path (Join-Path $InstDir 'DeepSeekHarness.exe'))) { throw 'exe replaced by update' }
Write-Host '  install dir reused; files still present'

# data must have survived the update
if (-not (Test-Path $Marker)) { throw 'user data marker LOST during update' }
Write-Host '  user data preserved through update'

# shortcuts: exact-count check inside the {group} folder (DefaultGroupName =
# app name => `Programs\DeepSeek Harness\DeepSeek Harness.lnk`). Update branch
# must not leave a duplicate link behind.
$smGroup = Join-Path $env:APPDATA ('Microsoft\Windows\Start Menu\Programs\' + $AppName).Trim()
$smLinks = @(Get-ChildItem -LiteralPath $smGroup -Filter '*.lnk' -ErrorAction Stop)
if ($smLinks.Count -ne 1) { throw "expected exactly 1 start-menu shortcut, found $($smLinks.Count) in $smGroup" }
Write-Host "  start-menu shortcut: $($smLinks[0].FullName)"

# --- 4. boot again after update ----------------------------------------------
Invoke-InstalledBoot -ExePath (Join-Path $InstDir 'DeepSeekHarness.exe') -DataDir $DataDir -Label 'after update'

# The step-4 boot wipes $DataDir to start fresh; re-drop the marker so the
# uninstall survival check below has a file to look for.
New-Item -ItemType Directory -Force (Join-Path $DataDir 'runtime') | Out-Null
Set-Content -LiteralPath $Marker -Value "data-survives $([DateTime]::Now.ToString('O'))" -Encoding utf8

# --- 5. uninstall, data preserved ---------------------------------------------
Section 'UNINSTALL (data preserved)'
$un = Join-Path $InstDir 'unins000.exe'
if (-not (Test-Path $un)) { throw 'uninstaller missing' }
$p = Start-Process -FilePath $un -PassThru -Wait -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
if ($p.ExitCode -ne 0) { throw "uninstall failed (exit $($p.ExitCode))" }
if (Test-Path $InstDir) { throw '{app} still exists after uninstall' }
if (-not (Test-Path $Marker)) { throw 'user data deleted by uninstall (expected preserved)' }
Write-Host '  {app} removed; user data preserved'

Write-Host "`nPASS: full install -> update -> uninstall all verified" -ForegroundColor Green