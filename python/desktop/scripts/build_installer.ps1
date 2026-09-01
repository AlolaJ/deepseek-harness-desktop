<#
.SYNOPSIS
  Compile the Inno installer for the DeepSeek Harness desktop EXE (bilingual,
  per-user/all-users, full/update auto-detection).

.DESCRIPTION
  Reads the version from apps/cli/package.json (e.g. "0.1.1-rc.2"), splits it
  into a numeric form (0.1.1) for the registry DisplayVersion, and compiles
  python/desktop/inno/setup.iss.

  Requires Inno Setup's ISCC. Locates it via PATH, the standard install dir, or
  the INNO_SETUP_HOME / INNO_HOME env var. Install with:
      winget install JRSoftware.InnoSetup

  Output: python/desktop/dist/DeepSeekHarness-<full>-Setup.exe

  Flags:
    -VersionFull  override (default: read apps/cli/package.json)
.EXAMPLE
  .\scripts\build_installer.ps1
#>
[CmdletBinding()]
param(
  [string]$VersionFull
)

$ErrorActionPreference = 'Stop'

$ScriptDir    = $PSScriptRoot
$DesktopRoot  = Split-Path -Parent $ScriptDir
$RepoRoot     = Split-Path -Parent (Split-Path -Parent $DesktopRoot)

function Get-ISCC {
  foreach ($c in @('ISCC', 'iscc')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  foreach ($env in @('INNO_SETUP_HOME', 'INNO_HOME')) {
    if ($env:INNO_SETUP_HOME) { $f = Join-Path $env:INNO_SETUP_HOME 'ISCC.exe'; if (Test-Path $f) { return $f } }
    if ($env:INNO_HOME)       { $f = Join-Path $env:INNO_HOME 'ISCC.exe';       if (Test-Path $f) { return $f } }
  }
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
  )
  foreach ($f in $candidates) { if (Test-Path $f) { return $f } }
  throw 'ISCC.exe not found. Install Inno Setup: winget install JRSoftware.InnoSetup'
}

# --- version -------------------------------------------------------------
if (-not $VersionFull) {
  $pkg = Get-Content (Join-Path $RepoRoot 'apps\cli\package.json') -Raw | ConvertFrom-Json
  $VersionFull = $pkg.version
}
$Numeric = ($VersionFull -split '[-+]')[0]
if ($Numeric -notmatch '^\d+(\.\d+){0,3}$') { throw "Cannot derive numeric version from: $VersionFull" }

$ISCC = Get-ISCC
Write-Host "ISCC  : $ISCC"
Write-Host "Build : $VersionFull (numeric $Numeric)"

# ISCC cannot READ source files whose absolute path exceeds MAX_PATH (~260): its
# manifest has no longPathAware entry, yet the onedir tree contains files at 276
# chars. The fix: expose the dist tree at a SHORT absolute path (a drive-root
# junction, no admin needed) and pass it via /DSrcDist=.
$DistRoot  = Join-Path $DesktopRoot '.build\dist\dsh-desktop'
$ShortDist = 'C:\dsh-dist'
if (-not (Test-Path $DistRoot)) { throw "dist tree missing: $DistRoot — run build_desktop.ps1 first" }

function Remove-Junction($Path) {
  # Remove the reparse point only; never follow into / delete its target.
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    cmd /c "rmdir `"$Path`" 2>nul"
  }
}

if (Test-Path $ShortDist) {
  $reparse = Get-Item -LiteralPath $ShortDist -Force
  if (-not ($reparse.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "`"$ShortDist`" exists as a real directory — refusing to clobber it. Delete it and re-run."
  }
  # stale junction from an interrupted run: recreate to track the current tree
  Remove-Junction $ShortDist
}
cmd /c "mklink /J `"$ShortDist`" `"$DistRoot`" >nul 2>&1"
if ($LASTEXITCODE -ne 0 -and -not (Test-Path $ShortDist)) {
  throw "failed to create junction $ShortDist -> $DistRoot"
}

try {
  $null = & $ISCC "/DSrcDist=$ShortDist" "/DMyAppVersion=$Numeric" "/DMyAppVersionFull=$VersionFull" (Join-Path $DesktopRoot 'inno\setup.iss')
  if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

  $out = Join-Path $DesktopRoot "dist\DeepSeekHarness-$VersionFull-Setup.exe"
  if (-not (Test-Path $out)) { throw "installer not produced: $out" }
  Write-Host ("DONE: {0} ({1:N1} MB)" -f $out, ((Get-Item $out).Length / 1MB)) -ForegroundColor Green
}
finally {
  Remove-Junction $ShortDist
}