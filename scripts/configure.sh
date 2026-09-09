#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_DIR="${JARVIS_CONFIG_DIR:-$HOME/.config/jarvis}"
SECRETS_FILE="$CONFIG_DIR/secrets.env"
mkdir -p "$CONFIG_DIR"
touch "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
# shellcheck disable=SC1090
source "$SECRETS_FILE"

read -rp "Sender Gmail address [${SENDER_EMAIL:-not configured}]: " new_email
read -rsp "Gmail app password [press Enter to keep current]: " new_password; echo
read -rsp "JARVIS access token [press Enter to keep current]: " new_token; echo

sender="${new_email:-${SENDER_EMAIL:-}}"
password="${new_password:-${GMAIL_APP_PASSWORD:-}}"
token="${new_token:-${JARVIS_ACCESS_TOKEN:-}}"
if [[ -z "$token" ]]; then token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"; fi

umask 077
temp_file="$SECRETS_FILE.tmp"
printf 'export GMAIL_APP_PASSWORD=%q\n' "$password" > "$temp_file"
printf 'export JARVIS_ACCESS_TOKEN=%q\n' "$token" >> "$temp_file"
printf 'export SENDER_EMAIL=%q\n' "$sender" >> "$temp_file"
mv "$temp_file" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
echo "Configuration saved. Restart JARVIS to apply it."
