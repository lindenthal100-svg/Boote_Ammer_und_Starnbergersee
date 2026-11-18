#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Bootzählung Starnberger See / Ammersee – Umgebung mit uv einrichten (macOS / Linux)
#
#   ./einrichten.sh                 Umgebung .venv anlegen und Pakete installieren
#   ./einrichten.sh --test          danach den Selbsttest laufen lassen
#   ./einrichten.sh --neu           vorhandene .venv löschen und neu aufbauen
#   ./einrichten.sh --python 3.13   andere Python-Version (Standard: 3.12)
#   ./einrichten.sh --ja            uv ohne Rückfrage installieren, falls es fehlt
#
# uv besorgt die passende Python-Version selbst, falls sie nicht installiert ist.
# ---------------------------------------------------------------------------
set -euo pipefail

PYTHON_VERSION="3.12"
TEST=0
NEU=0
JA=0

hilfe() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --python)  [ $# -ge 2 ] || { echo "--python braucht eine Version, z. B. 3.12"; exit 1; }
               PYTHON_VERSION="$2"; shift 2 ;;
    --test)    TEST=1; shift ;;
    --neu)     NEU=1; shift ;;
    --ja|-y)   JA=1; shift ;;
    -h|--help) hilfe; exit 0 ;;
    *)         echo "Unbekannte Option: $1"; hilfe; exit 1 ;;
  esac
done

# immer im Ordner des Skripts arbeiten (dort liegen requirements.txt und das Skript)
cd "$(dirname "$0")"
VENV=".venv"
VENV_PY="$VENV/bin/python"

schritt() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fehler()  { printf '\033[31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }

[ -f requirements.txt ] || fehler "requirements.txt fehlt. Das Skript muss im selben Ordner liegen wie bootzaehlung.py."

# --- 1. uv vorhanden? -------------------------------------------------------
schritt "1/4 uv prüfen"
for ordner in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
  if ! command -v uv >/dev/null 2>&1 && [ -x "$ordner/uv" ]; then
    export PATH="$ordner:$PATH"
  fi
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv ist nicht installiert."
  if [ "$JA" -ne 1 ]; then
    if [ -t 0 ]; then
      read -r -p "Jetzt mit dem offiziellen Installer von astral.sh installieren? [j/N] " antwort
    else
      antwort="n"
    fi
    case "$antwort" in
      j|J|ja|Ja|JA|y|Y) ;;
      *) echo "Abgebrochen. Selbst installieren mit:"
         echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
         echo "oder das Skript mit --ja starten."
         exit 1 ;;
    esac
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    fehler "Weder curl noch wget gefunden. Bitte uv selbst installieren: https://docs.astral.sh/uv/"
  fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fehler "uv wurde installiert, ist aber nicht im PATH. Terminal neu öffnen und das Skript noch einmal starten."
fi
echo "$(uv --version)"

# --- 2. virtuelle Umgebung ------------------------------------------------------
schritt "2/4 virtuelle Umgebung $VENV (Python $PYTHON_VERSION)"
if [ -d "$VENV" ] && [ "$NEU" -eq 1 ]; then
  echo "Lösche vorhandene $VENV ..."
  rm -rf "$VENV"
fi
if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import sys" >/dev/null 2>&1; then
  echo "$VENV ist schon da ($("$VENV_PY" --version)) und wird weiterverwendet."
  vorhanden="$("$VENV_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  case "$PYTHON_VERSION" in
    "$vorhanden"|"$vorhanden".*) ;;
    *) echo "Hinweis: gewünscht ist Python $PYTHON_VERSION. Zum Wechseln: ./einrichten.sh --neu --python $PYTHON_VERSION" ;;
  esac
  echo "Für einen sauberen Neuaufbau: ./einrichten.sh --neu"
else
  [ -e "$VENV" ] && rm -rf "$VENV"     # kaputte Reste entfernen
  uv venv "$VENV" --python "$PYTHON_VERSION"
fi

# --- 3. Pakete ------------------------------------------------------------------
schritt "3/4 Pakete installieren"
uv pip install --python "$VENV_PY" -r requirements.txt

"$VENV_PY" - <<'PY' || fehler "Die Pakete sind installiert, lassen sich aber nicht laden (siehe oben)."
import importlib
for modul in ("numpy", "scipy", "rasterio", "pystac_client", "pyproj", "shapely", "matplotlib"):
    m = importlib.import_module(modul)
    print(f"  {modul:<14} {getattr(m, '__version__', '')}")
PY

# --- 4. Selbsttest (optional) ---------------------------------------------------
if [ "$TEST" -eq 1 ]; then
  schritt "4/4 Selbsttest (ohne Internet, dauert etwa eine halbe Minute)"
  [ -f selbsttest.py ] || fehler "selbsttest.py fehlt im Ordner."
  protokoll="$(mktemp)"
  if "$VENV_PY" selbsttest.py >"$protokoll" 2>&1; then
    grep "Alle Prüfungen" "$protokoll" || true
    rm -f "$protokoll"
  else
    grep -E "FEHLER|Error|Traceback" "$protokoll" | head -20 || true
    fehler "Selbsttest fehlgeschlagen. Ganzes Protokoll: $protokoll"
  fi
else
  schritt "4/4 Selbsttest übersprungen (mit --test starten, um ihn auszuführen)"
fi

cat <<EOF

Fertig. So geht es weiter:

  source $VENV/bin/activate
  python bootzaehlung.py --nur-suchen
  python bootzaehlung.py --see ammersee --von 2026-06-01 --bis 2026-08-31 --vorschau

Ohne Aktivieren geht auch:
  $VENV_PY bootzaehlung.py --nur-suchen
EOF
