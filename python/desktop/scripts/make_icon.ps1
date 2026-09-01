<#
.SYNOPSIS
  Regenerate assets/icon.ico from assets/icon.png with a centered zoom crop.

.DESCRIPTION
  The source icon.png (800x800, transparent) carries the glyph in only ~60% of
  the canvas; at small taskbar sizes that renders tiny. This script crops the
  CENTER of the source by 1/Zoom (zoom=1.5 => keep a 533px square around the
  glyph, making it ~1.5x as large in the frame), then embeds that cropped frame
  at every standard ICO size (256/128/64/48/32/16), so titlebar, taskbar, exe
  and shortcut all show the enlarged mark.

  Called by build_desktop.ps1 stage 3 BEFORE the PyInstaller build (the spec
  reads assets/icon.ico for the EXE icon), so editing icon.png + rebuild picks
  up the new look automatically.

.PARAMETER Out
  Destination .ico (default: assets/icon.ico next to the source png).
.PARAMETER Zoom
  Linear zoom factor applied to the glyph (default 1.5).
.PARAMETER Force
  Regenerate even if Out is newer than the source png.
.EXAMPLE
  .\scripts\make_icon.ps1              # re-derive assets/icon.ico (1.5x)
  .\scripts\make_icon.ps1 -Zoom 1.2    # subtler enlargement
#>
[CmdletBinding()]
param(
  [string]$Png,
  [string]$Out,
  [double]$Zoom = 1.5,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ScriptDir   = $PSScriptRoot
$DesktopRoot = Split-Path -Parent $ScriptDir
if (-not $Png) { $Png = Join-Path $DesktopRoot 'assets\icon.png' }
if (-not $Out) { $Out = [IO.Path]::ChangeExtension($Png, '.ico') }
if (-not (Test-Path $Png)) { throw "source png not found: $Png" }

if (-not $Force -and (Test-Path $Out) -and ((Get-Item $Out).LastWriteTime -gt (Get-Item $Png).LastWriteTime)) {
  Write-Host "  icon.ico up to date ($Out)"
  return
}

$Py = Join-Path $DesktopRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { throw "venv python missing: $Py (pip install pillow)" }

$code = @'
import sys
from PIL import Image

args = sys.argv[1:]
def val(name, default=None):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
    return default

src  = val("--png")
out  = val("--out")
zoom = float(val("--zoom", "1.5"))

im = Image.open(src).convert("RGBA")
w, h = im.size
side = int(min(w, h) / zoom)          # centered 1/zoom square around the glyph
ox, oy = (w - side) // 2, (h - side) // 2
crop = im.crop((ox, oy, ox + side, oy + side))

sizes = [256, 128, 64, 48, 32, 16]
frames = [crop.resize((s, s), Image.LANCZOS) for s in sizes]
frames[0].save(out, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[1:])
print(f"wrote {out} from {src} zoom={zoom} crop={side}x{side} sizes={sizes}")
'@

$code | & $Py - --png $Png --out $Out --zoom $Zoom
if ($LASTEXITCODE -ne 0) { throw "icon generation failed (exit $LASTEXITCODE)" }
Write-Host "  DONE: $Out ($((Get-Item $Out).Length) bytes)"