# ---------------------------------------------------------------------------
# Bootzählung Starnberger See / Ammersee – Umgebung mit uv einrichten (Windows)
#
#   .\einrichten.ps1                 Umgebung .venv anlegen und Pakete installieren
#   .\einrichten.ps1 -Test           danach den Selbsttest laufen lassen
#   .\einrichten.ps1 -Neu            vorhandene .venv löschen und neu aufbauen
#   .\einrichten.ps1 -Python 3.13    andere Python-Version (Standard: 3.12)
#   .\einrichten.ps1 -Ja             uv ohne Rückfrage installieren, falls es fehlt
#
# Falls Windows das Skript blockiert: einrichten.cmd doppelklicken, oder
#   powershell -ExecutionPolicy Bypass -File .\einrichten.ps1
#
# uv besorgt die passende Python-Version selbst, falls sie nicht installiert ist.
# ---------------------------------------------------------------------------
param(
    [string]$Python = "3.12",
    [switch]$Test,
    [switch]$Neu,
    [switch]$Ja
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$Venv = ".venv"
# Windows: .venv\Scripts\python.exe, mit PowerShell 7 auf macOS/Linux: .venv/bin/python
$VenvPy = if ($IsLinux -or $IsMacOS) { Join-Path $Venv "bin/python" } else { Join-Path $Venv "Scripts\python.exe" }

function Schritt($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
function Fehler($text) { Write-Host "Fehler: $text" -ForegroundColor Red; exit 1 }
function Pruefe-Exitcode($was) { if ($LASTEXITCODE -ne 0) { Fehler "$was ist fehlgeschlagen (Code $LASTEXITCODE)." } }
function Hat-Uv { [bool](Get-Command uv -ErrorAction SilentlyContinue) }

if (-not (Test-Path "requirements.txt")) {
    Fehler "requirements.txt fehlt. Das Skript muss im selben Ordner liegen wie bootzaehlung.py."
}

# --- 1. uv vorhanden? -------------------------------------------------------
Schritt "1/4 uv prüfen"
foreach ($ordner in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")) {
    if (-not (Hat-Uv) -and (Test-Path (Join-Path $ordner "uv.exe"))) {
        $env:Path = "$ordner;$env:Path"
    }
}

if (-not (Hat-Uv)) {
    Write-Host "uv ist nicht installiert."
    if (-not $Ja) {
        $antwort = Read-Host "Jetzt mit dem offiziellen Installer von astral.sh installieren? [j/N]"
        if ($antwort -notmatch '^(j|ja|y|yes)$') {
            Write-Host "Abgebrochen. Selbst installieren mit:"
            Write-Host '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
            Write-Host "oder das Skript mit -Ja starten."
            exit 1
        }
    }
    powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    Pruefe-Exitcode "Die Installation von uv"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
    if (-not (Hat-Uv)) {
        Fehler "uv wurde installiert, ist aber nicht im PATH. PowerShell neu öffnen und das Skript noch einmal starten."
    }
}
uv --version

# --- 2. virtuelle Umgebung ------------------------------------------------------
Schritt "2/4 virtuelle Umgebung $Venv (Python $Python)"
if ((Test-Path $Venv) -and $Neu) {
    Write-Host "Lösche vorhandene $Venv ..."
    Remove-Item -Recurse -Force $Venv
}
$venvOk = $false
if (Test-Path $VenvPy) {
    $ErrorActionPreference = "Continue"
    & $VenvPy -c "import sys" 2>$null
    $venvOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = "Stop"
}
if ($venvOk) {
    Write-Host "$Venv ist schon da ($(& $VenvPy --version)) und wird weiterverwendet."
    $vorhanden = & $VenvPy -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if (-not ($Python -eq $vorhanden -or $Python.StartsWith("$vorhanden."))) {
        Write-Host "Hinweis: gewünscht ist Python $Python. Zum Wechseln: .\einrichten.ps1 -Neu -Python $Python" -ForegroundColor Yellow
    }
    Write-Host "Für einen sauberen Neuaufbau: .\einrichten.ps1 -Neu"
} else {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }   # kaputte Reste entfernen
    uv venv $Venv --python $Python
    Pruefe-Exitcode "Das Anlegen der Umgebung"
}

# --- 3. Pakete ------------------------------------------------------------------
Schritt "3/4 Pakete installieren"
uv pip install --python $VenvPy -r requirements.txt
Pruefe-Exitcode "Die Paketinstallation"

$pruefung = @'
import importlib
for modul in ("numpy", "scipy", "rasterio", "pystac_client", "pyproj", "shapely", "matplotlib"):
    m = importlib.import_module(modul)
    print(f"  {modul:<14} {getattr(m, '__version__', '')}")
'@
$pruefung | & $VenvPy -
if ($LASTEXITCODE -ne 0) { Fehler "Die Pakete sind installiert, lassen sich aber nicht laden (siehe oben)." }

# --- 4. Selbsttest (optional) ---------------------------------------------------
if ($Test) {
    Schritt "4/4 Selbsttest (ohne Internet, dauert etwa eine halbe Minute)"
    if (-not (Test-Path "selbsttest.py")) { Fehler "selbsttest.py fehlt im Ordner." }
    $env:PYTHONIOENCODING = "utf-8"
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $ErrorActionPreference = "Continue"     # Python-Warnungen sollen das Skript nicht abbrechen
    $protokoll = & $VenvPy selbsttest.py 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($code -eq 0) {
        $protokoll | Select-String "Alle Pr.fungen" | ForEach-Object { Write-Host $_.Line -ForegroundColor Green }
    } else {
        $protokoll | Select-String "FEHLER|Error|Traceback" | Select-Object -First 20 | ForEach-Object { Write-Host $_.Line }
        Fehler "Selbsttest fehlgeschlagen. Ausführlich: $VenvPy selbsttest.py"
    }
} else {
    Schritt "4/4 Selbsttest übersprungen (mit -Test starten, um ihn auszuführen)"
}

Write-Host ""
Write-Host "Fertig. So geht es weiter:" -ForegroundColor Green
Write-Host ""
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python bootzaehlung.py --nur-suchen"
Write-Host "  python bootzaehlung.py --see ammersee --von 2026-06-01 --bis 2026-08-31 --vorschau"
Write-Host ""
Write-Host "Ohne Aktivieren geht auch:"
Write-Host "  .\.venv\Scripts\python.exe bootzaehlung.py --nur-suchen"
