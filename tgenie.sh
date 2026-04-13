#!/usr/bin/env bash
# tgenie.sh — GenieCLI Launcher for Linux/macOS
# Usage: ./tgenie.sh [--openai] [--model gpt-4o] [--skills]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

# Forward all args to genie CLI
MAIN_ARGS=("$@")

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${RESET} $*"; }
err()  { echo -e "  ${RED}[ERROR]${RESET} $*"; }
info() { echo -e "  ${CYAN}$*${RESET}"; }
warn() { echo -e "  ${YELLOW}$*${RESET}"; }

echo
echo -e "  ${CYAN}+======================================+${RESET}"
echo -e "  ${CYAN}|     GenieCLI — Trino Query Tuning    |${RESET}"
echo -e "  ${CYAN}+======================================+${RESET}"
echo

# ── 1. Check Python ──────────────────────────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    err "Python not found. Install with: sudo apt install python3"
    exit 1
fi
ok "Python: $($PYTHON --version)"

# ── 2. Check / install pip deps ──────────────────────────────────────────────
REQS="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQS" ]; then
    info "Checking Python dependencies..."
    "$PYTHON" -m pip install -q -r "$REQS" 2>&1 | grep -v "already satisfied" || true
    ok "Dependencies ready"
fi

# ── 3. Auth token (TGenie only — needs Chrome for grab_auth.py) ─────────────
INTERFACE=$(
    "$PYTHON" - <<'EOF'
import json, pathlib
for p in [pathlib.Path.home() / ".genie" / "config.toml", pathlib.Path.home() / "ai-agent-config.json"]:
    if p.exists():
        text = p.read_text()
        if "tgenie" in text and "interface" in text:
            print("tgenie"); break
else:
    print("other")
EOF
)

if [ "$INTERFACE" = "tgenie" ]; then
    CHROME_DEBUG_PORT=9222

    # Start Chrome if not running (needed for grab_auth.py)
    if ! curl -s "http://localhost:${CHROME_DEBUG_PORT}/json" &>/dev/null; then
        CHROME=""
        for candidate in google-chrome google-chrome-stable chromium-browser chromium; do
            if command -v "$candidate" &>/dev/null; then
                CHROME="$candidate"; break
            fi
        done

        if [ -z "$CHROME" ]; then
            warn "Chrome not found — cannot auto-grab TGenie auth token."
            warn "Run 'genie setup' to configure a different backend, or install Chrome."
        else
            info "Starting Chrome for TGenie auth..."
            CHROME_PROFILE="${HOME}/.config/chrome-debug"
            mkdir -p "$CHROME_PROFILE"
            "$CHROME" \
                --remote-debugging-port="${CHROME_DEBUG_PORT}" \
                --user-data-dir="${CHROME_PROFILE}" \
                --remote-allow-origins="http://localhost:${CHROME_DEBUG_PORT}" \
                --no-first-run --no-default-browser-check \
                &>/dev/null &

            for i in {1..15}; do
                sleep 1
                if curl -s "http://localhost:${CHROME_DEBUG_PORT}/json" &>/dev/null; then
                    ok "Chrome ready"; break
                fi
                [ "$i" -eq 15 ] && warn "Chrome did not start in time."
            done
        fi
    fi

    warn "Please make sure you are logged into TGenie in Chrome."
    read -rp "  Press Enter when ready..." _

    info "Grabbing auth token..."
    if "$PYTHON" "$SCRIPT_DIR/grab_auth.py"; then
        ok "Auth token ready"
    else
        warn "Failed to grab auth token — you may need to /renew inside the CLI."
    fi
else
    ok "Using ${INTERFACE} interface"
fi

# ── 4. Launch CLI ────────────────────────────────────────────────────────────
echo
if [ ${#MAIN_ARGS[@]} -eq 0 ]; then
    "$PYTHON" -m genie chat
else
    "$PYTHON" -m genie "${MAIN_ARGS[@]}"
fi

echo
