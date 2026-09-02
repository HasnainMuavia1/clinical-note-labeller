#!/bin/bash
# =============================================================================
#  Clinical Note Labeller - one-file installer and launcher for macOS and Linux.
#
#  Double-click it. It installs Docker if missing, starts the app, and opens
#  the browser. Run it again any time to relaunch.
# =============================================================================
set -uo pipefail

COMPOSE_URL="https://raw.githubusercontent.com/HasnainMuavia1/clinical-note-labeller/main/docker-compose.prod.yml"
IMAGE_TAG="@@IMAGE_TAG@@"
OPENAI_API_KEY="@@OPENAI_API_KEY@@"
OPENAI_MINI_MODEL_ID="@@OPENAI_MINI_MODEL_ID@@"
LLAMA_CLOUD_API_KEY="@@LLAMA_CLOUD_API_KEY@@"
APP_API_KEY="@@APP_API_KEY@@"

INSTALL_DIR="$HOME/Library/Application Support/ClinicalNoteLabeller"
[ "$(uname -s)" = "Linux" ] && INSTALL_DIR="$HOME/.local/share/ClinicalNoteLabeller"
# Results live next to the .command the client double-clicked.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/ClinicalNoteLabeller"

bold=$(printf '\033[1m'); dim=$(printf '\033[2m'); red=$(printf '\033[31m'); off=$(printf '\033[0m')
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s%s%s\n' "$bold" "$*" "$off"; }
die()  { printf '\n%s[X] %s%s\n\n' "$red" "$*" "$off"; say "Nothing has been damaged. You can run this file again at any time."; read -r -p "Press return to close." _; exit 1; }

cat <<'BANNER'

  ==========================================================
     CLINICAL NOTE LABELLER
     Automatic setup - this window tells you if it needs
     anything. Otherwise just leave it running.
  ==========================================================
BANNER

# ---- 1. Docker present? -----------------------------------------------------
step "[1/6] Checking for Docker..."
if ! command -v docker >/dev/null 2>&1; then
    [ -x /Applications/Docker.app/Contents/Resources/bin/docker ] && \
        export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
fi

if ! command -v docker >/dev/null 2>&1; then
    if [ "$(uname -s)" = "Linux" ]; then
        die "Docker is not installed. Install Docker Engine for your distribution, then run this file again."
    fi

    say "      Docker is not installed. Downloading it (about 600 MB)."
    say "      This is a one-time step and can take several minutes."
    case "$(uname -m)" in
        arm64) DMG_URL="https://desktop.docker.com/mac/main/arm64/Docker.dmg" ;;
        *)     DMG_URL="https://desktop.docker.com/mac/main/amd64/Docker.dmg" ;;
    esac

    curl -fL --progress-bar -o /tmp/Docker.dmg "$DMG_URL" || die "Could not download Docker Desktop. Check the internet connection."

    say "      Installing. You will be asked for your Mac password."
    hdiutil attach -nobrowse -quiet /tmp/Docker.dmg || die "Could not open the Docker disk image."
    sudo /Volumes/Docker/Docker.app/Contents/MacOS/install --accept-license >/dev/null 2>&1
    installed=$?
    hdiutil detach -quiet /Volumes/Docker >/dev/null 2>&1
    rm -f /tmp/Docker.dmg
    [ $installed -eq 0 ] || die "The Docker installation did not complete."
    export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
fi
command -v docker >/dev/null 2>&1 || die "Docker still is not on the PATH. Open Docker Desktop once, then run this file again."
say "      Docker is installed."

# ---- 2. Docker engine running? ----------------------------------------------
step "[2/6] Starting the Docker engine..."
if ! docker info >/dev/null 2>&1; then
    [ "$(uname -s)" = "Darwin" ] && open -a Docker 2>/dev/null
    printf '      Waiting for Docker to start (this can take a minute or two)'
    for _ in $(seq 1 90); do
        docker info >/dev/null 2>&1 && break
        printf '.'; sleep 4
    done
    printf '\n'
fi
docker info >/dev/null 2>&1 || die "Docker did not finish starting. Open Docker Desktop manually, wait for it to say Running, then run this file again."
say "      Docker engine is ready."

# ---- 3. Folders --------------------------------------------------------------
step "[3/6] Preparing folders..."
mkdir -p "$INSTALL_DIR" "$DATA_DIR/workspace" || die "Could not create the application folders."
say "      Labelled output will appear in: $DATA_DIR/workspace"

# ---- 4. Compose file and configuration ---------------------------------------
step "[4/6] Downloading the application definition..."
curl -fsSL -o "$INSTALL_DIR/docker-compose.yml" "$COMPOSE_URL" || die "Could not download the application definition."

pick_port() {                     # pick_port <preferred>  -> echoes a free port
    local p=$1
    while lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; do p=$((p + 1)); done
    printf '%s' "$p"
}
API_PORT=$(pick_port 8000)
UI_PORT=$(pick_port 5173)

cat > "$INSTALL_DIR/.env" <<ENVEOF
IMAGE_TAG=$IMAGE_TAG
OPENAI_API_KEY=$OPENAI_API_KEY
OPENAI_MINI_MODEL_ID=$OPENAI_MINI_MODEL_ID
LLAMA_CLOUD_API_KEY=$LLAMA_CLOUD_API_KEY
APP_API_KEY=$APP_API_KEY
API_PORT=$API_PORT
UI_PORT=$UI_PORT
WORKSPACE_DIR=$DATA_DIR/workspace
ENVEOF
chmod 600 "$INSTALL_DIR/.env"
say "      Configured. Web address will be http://localhost:$UI_PORT"

# ---- 5. Pull and start --------------------------------------------------------
step "[5/6] Downloading and starting the application..."
say "      The first run downloads about 320 MB. Later runs are almost instant."
say ""
cd "$INSTALL_DIR" || die "Could not enter $INSTALL_DIR"
docker compose pull || die "Could not download the application images. If this said 'denied' or 'unauthorized', the published packages are private - see PUBLISHING.md."
docker compose up -d || die "The application failed to start. Run: docker compose -f '$INSTALL_DIR/docker-compose.yml' logs"

# ---- 6. Wait for health, open the browser -------------------------------------
step "[6/6] Waiting for the application to be ready..."
printf '      '
ready=0
for _ in $(seq 1 60); do
    if curl -fsS "http://localhost:$API_PORT/api/v1/health" >/dev/null 2>&1; then ready=1; break; fi
    printf '.'; sleep 3
done
printf '\n'
[ "$ready" -eq 1 ] || die "The application did not become ready in time. Run: docker compose -f '$INSTALL_DIR/docker-compose.yml' logs"

cat <<READY

  ==========================================================
     READY

     Opening http://localhost:$UI_PORT in your browser.

     Drag clinical notes onto the page - PDF, DOCX, text or
     ZIP - and they are sorted into folders by whether they
     contain medical codes and by specialty.

     Results also appear on disk in:
       $DATA_DIR/workspace
  ==========================================================

READY
if [ "$(uname -s)" = "Darwin" ]; then
    open "http://localhost:$UI_PORT/"
else
    xdg-open "http://localhost:$UI_PORT/" >/dev/null 2>&1 || say "  Open http://localhost:$UI_PORT/ in your browser."
fi
say "${dim}  You can close this window.${off}"
read -r -p "Press return to close." _
