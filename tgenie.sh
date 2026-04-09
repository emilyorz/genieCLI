#!/usr/bin/env bash
# tgenie.sh — TGenie CLI Launcher for Linux (Ubuntu)
# Usage: ./tgenie.sh [--openai] [--model gpt-4o] [--skills]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
CHROME_DEBUG_PORT=9222
CHROME_PROFILE="${HOME}/.config/chrome-debug"

# Parse pass-through args (forward everything to main.py)
MAIN_ARGS=()
for arg in "$@"; do
    MAIN_ARGS+=("$arg")
done

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; GRAY='\033[0;90m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${RESET} $*"; }
err()  { echo -e "  ${RED}[ERROR]${RESET} $*"; }
info() { echo -e "  ${CYAN}$*${RESET}"; }
warn() { echo -e "  ${YELLOW}$*${RESET}"; }

echo
echo -e "  ${CYAN}+======================================+${RESET}"
echo -e "  ${CYAN}|        TGenie CLI  Launcher          |${RESET}"
echo -e "  ${CYAN}+======================================+${RESET}"
echo

# ── 1. Check Python ───────────────────────────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    err "Python not found. Install with: sudo apt install python3"
    exit 1
fi
ok "Python: $($PYTHON --version)"

# ── 2. Check / install pip deps ───────────────────────────────────────────────
REQS="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQS" ]; then
    info "Checking Python dependencies..."
    "$PYTHON" -m pip install -q -r "$REQS" 2>&1 | grep -v "already satisfied" || true
    ok "Dependencies ready"
fi

# ── 3. Detect Chrome / Chromium ───────────────────────────────────────────────
CHROME=""
for candidate in google-chrome google-chrome-stable chromium-browser chromium; do
    if command -v "$candidate" &>/dev/null; then
        CHROME="$candidate"
        break
    fi
done

if [ -z "$CHROME" ]; then
    err "Chrome / Chromium not found."
    echo
    echo "  Install with one of:"
    echo "    sudo apt install chromium-browser"
    echo "    sudo snap install chromium"
    echo "  Or download Google Chrome from: https://www.google.com/chrome/"
    exit 1
fi
ok "Browser: $CHROME"

# ── 4. Start Chrome in debug mode if not already running ──────────────────────
if curl -s "http://localhost:${CHROME_DEBUG_PORT}/json" &>/dev/null; then
    ok "Chrome already running on port ${CHROME_DEBUG_PORT}"
else
    info "Starting Chrome in remote-debug mode..."
    mkdir -p "$CHROME_PROFILE"
    "$CHROME" \
        --remote-debugging-port="${CHROME_DEBUG_PORT}" \
        --user-data-dir="${CHROME_PROFILE}" \
        --remote-allow-origins="http://localhost:${CHROME_DEBUG_PORT}" \
        --no-first-run \
        --no-default-browser-check \
        &>/dev/null &

    echo -n "  Waiting for Chrome"
    for i in {1..20}; do
        sleep 1
        echo -n "."
        if curl -s "http://localhost:${CHROME_DEBUG_PORT}/json" &>/dev/null; then
            echo
            ok "Chrome is ready"
            break
        fi
        if [ "$i" -eq 20 ]; then
            echo
            err "Chrome did not start in time. Check if another process is using port ${CHROME_DEBUG_PORT}."
            exit 1
        fi
    done
fi

# ── 5. Auth token (skip for OpenAI-only mode) ─────────────────────────────────
# Check if running in OpenAI interface mode via config
INTERFACE=$(
    "$PYTHON" - <<'EOF'
import json, pathlib
cfg_path = pathlib.Path.home() / "ai-agent-config.json"
if cfg_path.exists():
    data = json.loads(cfg_path.read_text())
    print(data.get("interface", "tgenie"))
else:
    print("tgenie")
EOF
)

if [ "$INTERFACE" = "tgenie" ]; then
    echo
    warn "Please make sure you are logged into TGenie in the Chrome window."
    read -rp "  Press Enter when ready..." _

    echo
    info "Grabbing auth token..."
    if ! "$PYTHON" "$SCRIPT_DIR/grab_auth.py"; then
        echo
        err "Failed to grab auth token. See error above."
        exit 1
    fi
    ok "Auth token ready"
else
    ok "Using OpenAI interface — skipping TGenie auth"
fi

# ── 6. Launch CLI ─────────────────────────────────────────────────────────────
echo
# Skills are always enabled by default (use --no-skills to disable)
if [ ${#MAIN_ARGS[@]} -eq 0 ]; then
    "$PYTHON" -m genie chat
else
    "$PYTHON" -m genie "${MAIN_ARGS[@]}"
fi

echo
