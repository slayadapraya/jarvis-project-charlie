#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${JARVIS_APP_DIR:-$HOME/.local/share/jarvis/app}"
CONFIG_DIR="${JARVIS_CONFIG_DIR:-$HOME/.config/jarvis}"
STATE_DIR="${JARVIS_STATE_DIR:-$HOME/.local/state/jarvis}"
VENV_DIR="$HOME/.local/share/jarvis/venv"

mkdir -p "$STATE_DIR/logs"
if [[ -f "$CONFIG_DIR/jarvis.env" ]]; then
  # shellcheck disable=SC1091
  source "$CONFIG_DIR/jarvis.env"
fi
if [[ -f "$CONFIG_DIR/secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$CONFIG_DIR/secrets.env"
fi

export JARVIS_CONFIG_DIR="$CONFIG_DIR"
export JARVIS_STATE_DIR="$STATE_DIR"

if [[ -z "${JARVIS_ACCESS_TOKEN:-}" ]]; then
  echo "JARVIS_ACCESS_TOKEN is missing from $CONFIG_DIR/secrets.env" >&2
  exit 1
fi
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "JARVIS virtual environment is missing. Re-run install.sh." >&2
  exit 1
fi
if ! command -v lms >/dev/null 2>&1 && [[ ! -x "$HOME/.lmstudio/bin/lms" ]]; then
  echo "LM Studio CLI was not found. Enable it in LM Studio first." >&2
  exit 1
fi

LMS="$(command -v lms 2>/dev/null || true)"
[[ -n "$LMS" ]] || LMS="$HOME/.lmstudio/bin/lms"
"$LMS" server start >/dev/null 2>&1 || true

HOST="${JARVIS_HOST:-127.0.0.1}"
PORT="${JARVIS_PORT:-8000}"
if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$PORT" | tail -n +2 | grep -q .; then
  echo "Port $PORT is already occupied; refusing to kill an unrelated process." >&2
  exit 1
fi

echo "JARVIS 2.4 starting at http://$HOST:$PORT"
echo "Log: $STATE_DIR/logs/jarvis.log"
if [[ "${JARVIS_TAILSCALE_SERVE:-1}" == "1" ]]; then
  if ! "$APP_DIR/scripts/enable_private_tailscale.sh" "$PORT"; then
    echo "JARVIS will remain available locally; phone access was not enabled." >&2
  fi
fi
cd "$APP_DIR"
if [[ "${JARVIS_OPEN_BROWSER:-1}" == "1" ]] && command -v xdg-open >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
  (
    for _ in {1..40}; do
      if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true
        exit 0
      fi
      sleep 0.25
    done
  ) &
fi
exec "$VENV_DIR/bin/python" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info 2>&1 | tee -a "$STATE_DIR/logs/jarvis.log"
