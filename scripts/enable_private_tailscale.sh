#!/usr/bin/env bash
set -Eeuo pipefail

PORT="${1:-${JARVIS_PORT:-8000}}"
STATE_DIR="${JARVIS_STATE_DIR:-$HOME/.local/state/jarvis}"
PHONE_URL_FILE="$STATE_DIR/phone-url.txt"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Phone access unavailable: Tailscale is not installed." >&2
  exit 1
fi

TAILSCALE="$(command -v tailscale)"
status_json="$("$TAILSCALE" status --json 2>/dev/null || true)"
backend_state="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState", ""))' <<<"$status_json" 2>/dev/null || true)"
if [[ "$backend_state" != "Running" ]]; then
  echo "Phone access unavailable: Tailscale is not connected. Run: sudo tailscale up" >&2
  exit 1
fi

current_status="$("$TAILSCALE" serve status 2>/dev/null || true)"
if ! grep -Eq "(127[.]0[.]0[.]1|localhost):$PORT" <<<"$current_status"; then
  echo "Enabling private Tailscale access for JARVIS..."
  if ! serve_output="$($TAILSCALE serve --bg --yes "http://127.0.0.1:$PORT" 2>&1)"; then
    if [[ -t 0 && "${JARVIS_TAILSCALE_ALLOW_SUDO:-1}" == "1" ]] && command -v sudo >/dev/null 2>&1; then
      echo "Tailscale needs administrator permission once; Ubuntu may ask for your password."
      sudo "$TAILSCALE" serve --bg --yes "http://127.0.0.1:$PORT"
    else
      echo "$serve_output" >&2
      echo "Run once: sudo tailscale serve --bg --yes http://127.0.0.1:$PORT" >&2
      exit 1
    fi
  fi
  current_status="$("$TAILSCALE" serve status 2>/dev/null || true)"
fi

dns_name="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self", {}).get("DNSName", "").rstrip("."))' <<<"$status_json" 2>/dev/null || true)"
phone_url=""
if [[ -n "$dns_name" ]]; then
  phone_url="https://$dns_name"
else
  phone_url="$(grep -Eo 'https://[^[:space:]]+' <<<"$current_status" | head -n 1 || true)"
fi
if [[ -z "$phone_url" ]]; then
  echo "Tailscale Serve is active, but its HTTPS URL could not be determined." >&2
  exit 1
fi

mkdir -p "$STATE_DIR"
umask 077
temp_url="$PHONE_URL_FILE.tmp"
printf '%s\n' "$phone_url" > "$temp_url"
mv "$temp_url" "$PHONE_URL_FILE"
chmod 600 "$PHONE_URL_FILE"
echo "Phone URL: $phone_url"
echo "Open that address on a phone signed into the same tailnet, then enter the normal JARVIS access token."
