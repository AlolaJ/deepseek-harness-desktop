<#
.SYNOPSIS
  Launch the DeepSeek Harness desktop shell (WebView2 window embedding `dsh web`).

.DESCRIPTION
  Creates python/desktop/.venv (if missing), installs requirements.txt, then
  runs `python -m dsh_shell`. Forward any extra arguments straight through,
  e.g.:  .\scripts\run_dev.ps1 --data-dir C:\tmp\dshdata

  Requires: Node.js on PATH (or set DSH_DESKTOP_NODE) and the workspace build
  (`pnpm build` once, so apps/cli/lib/bin.js + apps/web/dist exist).
#>
$DesktopRoot = Split-Path -Parent $PSScriptRoot            # python/desktop
$Venv        = Join-Path $DesktopRoot '.venv'
$Py          = Join-Path $Venv 'Scripts\python.exe'

if (-not (Test-Path $Py)) {
  Write-Host '==> creating venv'
  $null = python -m venv $Venv
  if (-not (Test-Path $Py)) { throw 'venv creation failed' }
}

& $Py -m pip install --quiet --disable-pip-version-check -r (Join-Path $DesktopRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

Push-Location $DesktopRoot
try {
  & $Py -m dsh_shell @args
  exit $LASTEXITCODE
}
finally { Pop-Location }