<#
.SYNOPSIS
  Build the DeepSeek Harness desktop EXE: closure-deploy the node backend,
  bundle node.exe + icon.ico as runtime resources, then PyInstaller onedir.

.DESCRIPTION
  Pipeline (each stage is skippable for iteration):

    stage 0  ensure venv + pyinstaller/pillow
    stage 1  preflight repo build   (apps/cli/lib/bin.js + apps/web/dist) else pnpm run build
    stage 2  closure deploy         pnpm --filter dsh-desktop-runtime deploy  -> .build/resources/cli
                                     (+ de-link the link:vendor/* reparse points into real copies
                                      + materialize link:-overridden vendor/{cosmokit,schemastery}
                                      + smoke-boot: node <closure>/bin.js web prints an HTTP URL)
    stage 3  runtime resources      .build/resources/{node.exe, icon.ico}
    stage 4  PyInstaller onedir     .build/dist/dsh-desktop/DeepSeekHarness.exe  (windowed, console=False)
    stage 5  standalone boot probe  launch EXE from a scratch dir, find window, assert bundled
                                     node backend + webview2 under probe data dir, WM_CLOSE, no leftovers.

  Outputs (all under python/desktop):
    .build/resources/     node.exe + cli closure + icon.ico   (fed into the exe by dsh-desktop.spec)
    .build/dist/dsh-desktop/DeepSeekHarness.exe (+ _internal/)  (the distributable folder)
    .build/probe/         probe scratch dir incl. shell.log

  Flags:
    -SkipDeploy  reuse existing .build/resources/cli (do not re-run pnpm deploy)
    -SkipBuild   reuse existing PyInstaller output (no stage 4)
    -SkipProbe   do not launch/verify the built exe
    -NoInstall   skip pip install of pyinstaller/pillow (assume present)

.EXAMPLE
  .\scripts\build_desktop.ps1               # full build + probe
  .\scripts\build_desktop.ps1 -SkipDeploy    # iterate hiddenimports only
#>
[CmdletBinding()]
param(
  [switch]$SkipDeploy,
  [switch]$SkipBuild,
  [switch]$SkipProbe,
  [switch]$NoInstall
)

$ErrorActionPreference = 'Stop'

$ScriptDir    = $PSScriptRoot
$DesktopRoot  = Split-Path -Parent $ScriptDir
$RepoRoot     = Split-Path -Parent (Split-Path -Parent $DesktopRoot)
$BuildDir     = Join-Path $DesktopRoot '.build'
$ResDir       = Join-Path $BuildDir 'resources'
$Py           = Join-Path $DesktopRoot '.venv\Scripts\python.exe'

function Section([string]$Name) { Write-Host "`n=== [$Name] ===" -ForegroundColor Cyan }

# -- helpers ----------------------------------------------------------------

function Invoke-Native {
  param([Parameter(Mandatory)][scriptblock]$ScriptBlock)
  & $ScriptBlock
  if ($LASTEXITCODE -ne 0) { throw "command failed (exit $LASTEXITCODE): $ScriptBlock" }
}

function Get-NativeCmd { param([string]$Path) (Get-Command $Path -ErrorAction Stop).Source }

<#
  pnpm deploy preserves `link:` overrides (cosmokit/schemastery in this repo)
  as real reparse-point symlinks that point OUTSIDE the deployed tree — into the
  repo's vendor/. A shipped closure must not reference the repo, so resolve each
  link's target and replace it with a real recursive copy.
#>
function Fix-ReparsePoints([string]$Root, [string]$Label) {
  Section "de-link $Label reparse points"
  $links = Get-ChildItem -LiteralPath $Root -Directory -Recurse -Attributes ReparsePoint -ErrorAction SilentlyContinue
  if (-not $links) { Write-Host '  (none)'; return }
  foreach ($l in $links) {
    $targets = @($l.Target)
    if ($targets.Count -eq 0) { throw "reparse point without a target: $($l.FullName)" }
    $target = $targets[0]
    $abs = [IO.Path]::GetFullPath((Join-Path $l.Parent.FullName $target))
    if (-not (Test-Path -LiteralPath $abs)) { throw "reparse target missing: $abs" }
    Write-Host "  $($l.FullName)  ->  $abs"
    cmd.exe /c rmdir "`"$($l.FullName)`""
    if ($LASTEXITCODE -ne 0) { throw "rmdir of link failed (exit $LASTEXITCODE)" }
    Copy-Item -LiteralPath $abs -Destination $l.FullName -Recurse -Force -ErrorAction Stop
  }
  $left = Get-ChildItem -LiteralPath $Root -Recurse -Attributes ReparsePoint -ErrorAction SilentlyContinue
  if ($left) { throw "$($left.Count) reparse point(s) remain in closure" }
  Write-Host '  all reparse points resolved'
}

<#
  Root pnpm-workspace.yaml `overrides:` maps @deepseek-ai/{cosmokit,schemastery}
  to `link:vendor/*`. Depending on deploy-root's declarations, the deploy may
  emit them as `link:` reparse points to vendor/ (Fix-ReparsePoints then
  de-links those into REAL copies — handled), or omit them entirely (an older
  deploy-root that didn't declare them). This fallback guarantees the real
  packages exist in the closure either way: no-op when already materialized,
  else copy from vendor/. Strips nested node_modules (broken `link:` workspace
  symlinks) so a shipped closure has no repository references at all.
#>
function Materialize-LinkOverrides([string]$Closure) {
  $scope = Join-Path $Closure 'node_modules\@deepseek-ai'
  New-Item -ItemType Directory -Force $scope | Out-Null
  foreach ($name in @('cosmokit', 'schemastery')) {
    $src = Join-Path $RepoRoot ("vendor\{0}" -f $name)
    $dst = Join-Path $scope $name
    if (Test-Path $dst) { Write-Host "  $name already materialized in closure (de-linked)"; continue }
    if (-not (Test-Path (Join-Path $src 'package.json'))) { throw "vendor package missing: $src" }
    Write-Host "  materialize vendor\$name -> closure node_modules\@deepseek-ai"
    Copy-Item -LiteralPath (Join-Path $src '*') -Destination $dst -Recurse -Force -ErrorAction Stop
    $nested = Join-Path $dst 'node_modules'
    if (Test-Path $nested) { Remove-Item -LiteralPath $nested -Recurse -Force }
  }
}

<#
  Drop non-runtime artifacts from the closure: source maps and TypeScript
  declarations are never loaded by Node, they dominate the file count AND they
  are exactly the files that push installed paths closest to MAX_PATH (the
  deepest path in the tree is `.../machine-id/getMachineId-unsupported.js.map`).
  The closure smoke boot immediately after this step is the safety net: if a
  prune ever removed something the CLI actually needs, that gate fails in
  seconds. Run BEFORE the smoke, so the gate tests the pruned closure.
#>
function Prune-DevArtifacts([string]$Closure) {
  Section 'prune closure dev artifacts (maps + d.ts + tsbuildinfo)'
  $before = (Get-ChildItem -LiteralPath $Closure -Recurse -File | Measure-Object).Count
  $patterns = @('*.js.map','*.mjs.map','*.cjs.map','*.d.ts','*.d.mts','*.d.cts','*.tsbuildinfo')
  $removed = 0
  foreach ($pat in $patterns) {
    $removed += (Get-ChildItem -LiteralPath $Closure -Recurse -File -Filter $pat -ErrorAction SilentlyContinue |
      ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue; 1 } | Measure-Object).Count
  }
  $after = (Get-ChildItem -LiteralPath $Closure -Recurse -File | Measure-Object).Count
  Write-Host "  files: $before -> $after (removed $removed)"
}

<#
  PyInstaller places every listed data file under `_internal/`. For the node
  closure that would mean installed paths like
  `{app}\_internal\resources\cli\node_modules\...` — too deep for Inno/ISCC's
  MAX_PATH cap at real install locations. Relayout AFTER PyInstaller moves the
  node closure, node.exe and icon.ico up to the bundle root (siblings of the
  EXE), which config.py's frozen branch resolves via `Path(sys.executable).parent`.
#>
function Relayout-Resources([string]$BundleRoot) {
  Section 'relayout: node.exe + cli + icon.ico to bundle root'
  $fromRes = Join-Path $BundleRoot '_internal\resources'
  if (-not (Test-Path $fromRes)) { throw "expected PyInstaller resources dir missing: $fromRes" }
  foreach ($name in @('cli', 'node.exe', 'icon.ico')) {
    $src = Join-Path $fromRes $name
    if (-not (Test-Path $src)) { throw "relayout source missing: $src" }
    Move-Item -LiteralPath $src -Destination (Join-Path $BundleRoot $name) -Force -ErrorAction Stop
    Write-Host "  moved $name -> bundle root"
  }
  $left = Get-ChildItem -LiteralPath $fromRes -ErrorAction SilentlyContinue
  if ($left) { throw "unexpected leftovers in _internal\resources: $($left.Name -join ',')" }
  Remove-Item -LiteralPath $fromRes -Force
}

<#
  Pre-EXE boot gate: launch the closure's `dsh web` with the exact same args the
  shell uses (bin.js web --port 0 --no-open) and assert it prints an HTTP URL.
  Catches a broken/ incomplete closure in seconds — long before PyInstaller
  spends ~5 minutes rebuilding an EXE that would fail its boot probe anyway.
#>
function Invoke-ClosureSmoke([string]$NodeExe, [string]$Closure, [string]$Scratch) {
  Section "smoke: dsh web from closure (pre-EXE boot gate)"
  New-Item -ItemType Directory -Force $Scratch | Out-Null
  $bin = Join-Path $Closure 'node_modules\@deepseek-ai\dsh\lib\bin.js'
  if (-not (Test-Path $bin)) { throw "harness bin.js missing: $bin" }
  $out = Join-Path $Scratch 'stdout.log'
  $err = Join-Path $Scratch 'stderr.log'
  $p = Start-Process -FilePath $NodeExe -WorkingDirectory $Scratch -PassThru `
        -ArgumentList @($bin, 'web', '--port', '0', '--no-open') `
        -RedirectStandardOutput $out -RedirectStandardError $err
  Write-Host "  launched node pid=$($p.Id)"
  $deadline = (Get-Date).AddSeconds(90)
  $url = $null
  while ((Get-Date) -lt $deadline) {
    if ($p.HasExited) { break }
    $text = (Get-Content $out -Raw -ErrorAction SilentlyContinue) + (Get-Content $err -Raw -ErrorAction SilentlyContinue)
    if ($text -match 'http://127\.0\.0\.1:\d+') { $url = $matches[0]; break }
    Start-Sleep -Milliseconds 300
  }
  if (-not $url) {
    if (-not $p.HasExited) { $p.Kill() }
    Write-Host "  STDOUT:`n$(Get-Content $out -Raw -ErrorAction SilentlyContinue)"
    Write-Host "  STDERR:`n$(Get-Content $err -Raw -ErrorAction SilentlyContinue)"
    throw 'closure dsh web did not print an HTTP URL'
  }
  Write-Host "  URL: $url"
  if (-not $p.HasExited) { $p.Kill(); if (-not $p.WaitForExit(10000)) { $p.ProcessId } }
  Write-Host '  smoke OK: node + closure boots the backend'
}

function Find-WindowForPid {
  param([int]$TargetPid, [string]$Title, [int]$TimeoutSec = 90)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $found = [IntPtr]::Zero
    $cb = [DeskWin32Probe+EnumWindowsProc]{
      param($hwnd, $lparam)
      if ([DeskWin32Probe]::IsWindowVisible($hwnd)) {
        $sb = New-Object System.Text.StringBuilder 512
        # GetWindowTextW returns the CHAR COUNT (int) — compare the buffer, not
        # the return value, or the title never matches.
        [void][DeskWin32Probe]::GetWindowTextW($hwnd, $sb, 512)
        if ($sb.ToString() -eq $Title) {
          $winPid = 0
          [void][DeskWin32Probe]::GetWindowThreadProcessId($hwnd, [ref]$winPid)
          if ($winPid -eq $TargetPid) { $script:FoundHwnd = $hwnd; return $false }
        }
      }
      return $true
    }
    $script:FoundHwnd = [IntPtr]::Zero
    [void][DeskWin32Probe]::EnumWindows($cb, [IntPtr]::Zero)
    if ($script:FoundHwnd -ne [IntPtr]::Zero) { return $script:FoundHwnd }
    Start-Sleep -Milliseconds 500
  }
  return [IntPtr]::Zero
}

function Get-ProbeProcesses([string]$ProbeDataDir) {
  Get-CimInstance Win32_Process -Filter "Name='node.exe' or Name='msedgewebview2.exe'" -ErrorAction Stop |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($ProbeDataDir) }
}

<#
  Launch the built EXE from a scratch dir (fresh, repo-independent env) and
  verify the frozen bundle actually boots standalone: window appears, the
  bundled bundle-root node.exe spawns `dsh web`, a webview2 renderer uses the
  probe data dir, WM_CLOSE exits gracefully, and nothing leaks.
#>
function Invoke-BootProbe([string]$ExePath, [string]$ProbeRoot, [string]$Label) {
  Section "probe: $Label"
  $data = Join-Path $ProbeRoot 'data'
  New-Item -ItemType Directory -Force $data | Out-Null
  $null = Remove-Item -LiteralPath $data -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item Env:DSH_DESKTOP_NODE -ErrorAction SilentlyContinue
  Remove-Item Env:DSH_DESKTOP_CLI_DIR -ErrorAction SilentlyContinue
  # CRITICAL: point the shell at the probe data dir, not the real
  # %LOCALAPPDATA%\DeepSeekHarness — otherwise webview2 profile, backend cwd and
  # shell.log all land in the user's real data dir and the assertions below
  # target a dir that is never written.
  $env:DSH_DESKTOP_DATA_DIR = $data
  # Close-to-tray escape hatch: the shell's X button normally hides to the tray,
  # but the probe drives it with WM_CLOSE and must get a real exit to assert
  # zero leftover processes.
  $env:DSH_DESKTOP_E2E_CLOSE_EXIT = '1'

  $out = Join-Path $ProbeRoot 'stdout.log'
  $err = Join-Path $ProbeRoot 'stderr.log'
  $t0 = Get-Date
  $p = Start-Process -FilePath $ExePath -WorkingDirectory $ProbeRoot -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
  Write-Host "  launched pid=$($p.Id) at $(Get-Date -Format HH:mm:ss)"

  $hwnd = Find-WindowForPid -TargetPid $p.Id -Title 'DeepSeek Harness' -TimeoutSec 120
  if ($hwnd -eq [IntPtr]::Zero) {
    Write-Host "  STDOUT:`n$(Get-Content $out -Raw -ErrorAction SilentlyContinue)"
    Write-Host "  STDERR:`n$(Get-Content $err -Raw -ErrorAction SilentlyContinue)"
    throw 'window "DeepSeek Harness" did not appear'
  }
  Write-Host "  window up at $(Get-Date -Format HH:mm:ss) ($([int]((Get-Date)-$t0).TotalSeconds)s)"

  $bundledNode = [regex]::Escape((Join-Path (Split-Path $ExePath) 'node.exe'))
  $node = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction Stop |
    Where-Object { $_.CommandLine -and $_.CommandLine -match $bundledNode -and $_.CommandLine -match 'bin\.js web' }
  if (-not $node) { throw 'bundled node backend (bundle-root node.exe ... bin.js web) not spawned' }
  Write-Host "  backend node pid=$($node.ProcessId)"

  $wv = Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" -ErrorAction Stop |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($data) }
  if (-not $wv) { throw "no webview2 renderer under probe data dir (bad WEBVIEW2_USER_DATA_FOLDER?)" }
  Write-Host "  webview2 renderers: $($wv.Count)"

  # The splash window appears BEFORE the backend finishes booting, so wait for
  # the boot URL in shell.log before closing — the point of this probe (the
  # backend boots standalone) and the shell.log assertion below both need it.
  # Stopping the app early would kill the backend mid-boot and no URL line
  # would ever be written.
  $log = Join-Path $data 'shell.log'
  $booted = $false
  $urlDeadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $urlDeadline) {
    $logText = Get-Content $log -Raw -ErrorAction SilentlyContinue
    if ($logText -match 'backend URL: http://') { $booted = $true; break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $booted) {
    Write-Host "  SHELL.LOG:`n$(Get-Content $log -Raw -ErrorAction SilentlyContinue)"
    throw 'backend did not report a URL before the probe timeout'
  }
  Write-Host '  backend reported its URL'

  [void][DeskWin32Probe]::PostMessageW($hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)  # WM_CLOSE
  $exited = $p.WaitForExit(25000)
  if (-not $exited) { $p.Kill(); throw 'EXE did not exit after WM_CLOSE' }
  Write-Host "  exited pid=$($p.Id) code=$($p.ExitCode)"

  # give child node/webview2 a beat to wind down, then assert nothing leaks
  $left = $null
  for ($i = 0; $i -lt 12; $i++) {
    $left = Get-ProbeProcesses $data
    if (-not $left) { break }
    Start-Sleep -Seconds 1
  }
  if ($left) { throw "leftover processes: $($left.ProcessId -join ',')" }
  Write-Host '  no leftover node/webview2'

  $log = Join-Path $data 'shell.log'
  $text = Get-Content $log -Raw -ErrorAction SilentlyContinue
  if ($text -notmatch 'backend URL: http://') { throw "shell.log missing boot URL (see $log)" }
  Write-Host "  probe OK: backend boot logged in $log"
}

# -- win32 interop for the probe --------------------------------------------

if (-not ('DeskWin32Probe' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class DeskWin32Probe {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
}
'@
}

# -- stage 0: venv + tooling -------------------------------------------------

if (-not (Test-Path $Py)) { throw "venv python missing: $Py (run scripts/run_dev.ps1 once)" }
if (-not $NoInstall) {
  Section 'pip: pyinstaller + pillow'
  & $Py -m pip install --quiet --disable-pip-version-check pyinstaller pillow
  if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
}
$PyVer = & $Py --version
Write-Host "venv: $PyVer"

# -- stage 1: preflight repo build --------------------------------------------

$cliBin = Join-Path $RepoRoot 'apps\cli\lib\bin.js'
$webDist = Join-Path $RepoRoot 'apps\web\dist\index.html'
if ((Test-Path $cliBin) -and (Test-Path $webDist)) {
  Section 'preflight: workspace build present'
} else {
  Section 'preflight: workspace build missing -> pnpm run build'
  Push-Location $RepoRoot
  try { Invoke-Native { & pnpm run build } } finally { Pop-Location }
  if (-not (Test-Path $cliBin) -or -not (Test-Path $webDist)) { throw 'pnpm run build did not produce apps/cli/lib/bin.js + apps/web/dist' }
}

# -- stage 2: pnpm install (register deploy-root) + closure deploy ------------

Push-Location $RepoRoot
try {
  if (-not $SkipDeploy) {
    Section 'pnpm install (workspace + deploy-root member)'
    Invoke-Native { & pnpm install }

    Section 'closure deploy -> .build/resources/cli'
    $closure = Join-Path $ResDir 'cli'
    New-Item -ItemType Directory -Force $ResDir | Out-Null
    if (Test-Path $closure) { Remove-Item -LiteralPath $closure -Recurse -Force }
    # auto-install-peers=true: pnpm installs non-workspace peers too. The
    # workspace: peers that still fail to materialize are declared as regular
    # dependencies of deploy-root/package.json (pnpm won't auto-install a
    # `workspace:`-protocol peer inside a deploy), and the link:-overridden
    # packages get materialized by Materialize-LinkOverrides below.
    Invoke-Native {
      & pnpm --filter dsh-desktop-runtime deploy --legacy --prod `
           --config.node-linker=hoisted --config.auto-install-peers=true `
           --config.link-workspace-packages=true $closure
    }
    if (-not (Test-Path (Join-Path $closure 'node_modules\@deepseek-ai\dsh\lib\bin.js'))) {
      throw 'deploy did not produce node_modules/@deepseek-ai/dsh/lib/bin.js'
    }
    Fix-ReparsePoints $closure 'closure'
    Materialize-LinkOverrides $closure
    Prune-DevArtifacts $closure
    if ((Get-Command node -ErrorAction SilentlyContinue)) {
      Invoke-ClosureSmoke -NodeExe (Get-Command node -ErrorAction Stop).Source `
        -Closure $closure -Scratch (Join-Path $BuildDir 'closure-smoke')
    }
  }
} finally { Pop-Location }

# -- stage 3: runtime resources ------------------------------------------------

if ((Get-Command node -ErrorAction SilentlyContinue)) {
  Section 'runtime resources: node.exe + icon.ico'
  $nodeExe = (Get-Command node -ErrorAction Stop).Source
  Copy-Item $nodeExe (Join-Path $ResDir 'node.exe') -Force
  # Derive icon.ico from assets/icon.png with a centered 1.5x zoom crop FIRST so
  # the enlarged glyph lands in the EXE's PE icon (spec reads assets/icon.ico),
  # the staged resources copy, and the titlebar/taskbar at runtime.
  # -Force: the ico is derived from icon.png on every build. The timestamp
  # skip let a hand-regenerated (crop-less) ico survive builds and shrink the
  # taskbar glyph back to the uncropped size.
  & (Join-Path $ScriptDir 'make_icon.ps1') -Force -Out (Join-Path $DesktopRoot 'assets\icon.ico' -ErrorAction Stop)
  Copy-Item (Join-Path $DesktopRoot 'assets\icon.ico') (Join-Path $ResDir 'icon.ico') -Force -ErrorAction Continue
  if (-not (Test-Path (Join-Path $ResDir 'node.exe'))) { throw 'node.exe could not be staged' }
  Write-Host "  node.exe $((Get-Item (Join-Path $ResDir 'node.exe')).Length) bytes"
  $ico = Get-Item (Join-Path $ResDir 'icon.ico') -ErrorAction SilentlyContinue
  Write-Host "  icon.ico $($ico.Length) bytes"
}

# -- stage 4: PyInstaller onedir -----------------------------------------------

<#
  A leftover frozen EXE / its backend node / webview2 renderers keep the OLD
  .build\dist\dsh-desktop bundle's DLLs (e.g. sharp's libvips-42.dll) open, so
  PyInstaller's COLLECT -> rmtree fails with WinError 5 (access denied). Kill
  anything still referencing the build dir before PyInstaller touches dist.
#>
$bundleLocked = Get-CimInstance Win32_Process -Filter "Name='node.exe' or Name='DeepSeekHarness.exe' or Name='msedgewebview2.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($BuildDir) }
if ($bundleLocked) {
  Section 'clear stale bundle processes'
  foreach ($p in $bundleLocked) {
    Write-Host "  killing pid $($p.ProcessId) $($p.Name)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 1000
}

$DistDir = Join-Path $BuildDir 'dist'
if (-not $SkipBuild) {
  Section 'PyInstaller onedir (windowed)'
  Push-Location $DesktopRoot
  try {
    Invoke-Native {
      & $Py -m PyInstaller --noconfirm --clean `
           --distpath $DistDir --workpath (Join-Path $BuildDir 'pyinstaller') `
           (Join-Path $DesktopRoot 'dsh-desktop.spec')
    }
  } finally { Pop-Location }
}
Relayout-Resources (Join-Path $DistDir 'dsh-desktop')
$ExePath = Join-Path $DistDir 'dsh-desktop\DeepSeekHarness.exe'
if (-not (Test-Path $ExePath)) { throw "built exe missing: $ExePath" }
Write-Host "exe: $ExePath"
$bytes = (Get-ChildItem -Recurse -File (Join-Path $DistDir 'dsh-desktop') | Measure-Object Length -Sum).Sum
Write-Host ("bundle size: {0:N1} MB" -f ($bytes / 1MB))

# -- stage 5: standalone boot probe ---------------------------------------------

if (-not $SkipProbe) {
  Invoke-BootProbe -ExePath $ExePath -ProbeRoot (Join-Path $BuildDir 'probe') -Label 'frozen boot (node.exe + cli at bundle root)'
}

Write-Host "`nDONE. EXE at $ExePath (next: inno/setup.iss)" -ForegroundColor Green