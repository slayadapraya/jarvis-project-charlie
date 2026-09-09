#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/jarvis/app"
CONFIG_DIR="$HOME/.config/jarvis"
STATE_DIR="$HOME/.local/state/jarvis"
BACKUP_DIR="$HOME/.local/share/jarvis/backups"
VENV_DIR="$HOME/.local/share/jarvis/venv"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$BACKUP_DIR/jarvis-before-v2-$timestamp.tar.gz.enc"
manifest="$BACKUP_DIR/jarvis-before-v2-$timestamp.sha256"

for command in python3 tar openssl sha256sum; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing required command: $command" >&2; exit 1; }
done
mkdir -p "$BACKUP_DIR" "$CONFIG_DIR" "$STATE_DIR/logs"

declare -a candidates=(
  "$HOME/Desktop/JARVIS"
  "$APP_DIR"
  "$CONFIG_DIR/models.json"
  "$CONFIG_DIR/repositories.json"
  "$CONFIG_DIR/jarvis.env"
  "$CONFIG_DIR/secrets.env"
)
declare -a legacy_candidates=(
  "$HOME/Desktop/start_agent.sh"
  "$HOME/Desktop/start_jarvis.sh"
  "$HOME/Desktop/jarvis_server.py"
  "$HOME/Desktop/index.html"
  "$HOME/Desktop/manifest.json"
)
declare -a legacy_to_remove=()
legacy_detected=0
for path in "${legacy_candidates[@]}"; do
  # These names are generic enough that a friend may already use one. Only
  # adopt files whose contents identify them as part of JARVIS.
  if [[ -f "$path" ]] && grep -Eiq 'jarvis|j[.]a[.]r[.]v[.]i[.]s|jarvis_server|chat_history' "$path"; then
    candidates+=("$path")
    legacy_to_remove+=("$path")
    legacy_detected=1
  fi
done
if [[ "$legacy_detected" == "1" && -f "$HOME/Desktop/chat_history.json" ]]; then
  candidates+=("$HOME/Desktop/chat_history.json")
  legacy_to_remove+=("$HOME/Desktop/chat_history.json")
fi
declare -a existing=()
for path in "${candidates[@]}"; do [[ -e "$path" ]] && existing+=("$path"); done
backup_created=0

if ((${#existing[@]})); then
  echo "Creating an encrypted, byte-for-byte backup before changing anything."
  read -rsp "Choose backup passphrase: " passphrase_one; echo
  read -rsp "Repeat backup passphrase: " passphrase_two; echo
  [[ -n "$passphrase_one" && "$passphrase_one" == "$passphrase_two" ]] || { echo "Passphrases did not match." >&2; exit 1; }
  : > "$manifest"
  for path in "${existing[@]}"; do
    if [[ -f "$path" ]]; then sha256sum "$path" >> "$manifest"; fi
  done
  declare -a relative=()
  for path in "${existing[@]}"; do relative+=("${path#"$HOME/"}"); done
  printf '%s\0' "${relative[@]}" | tar -C "$HOME" --null --files-from=- --transform='s,^,home/,' -czf - | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -out "$archive" -pass fd:3 3<<<"$passphrase_one"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$archive" -pass fd:3 3<<<"$passphrase_one" | tar -tzf - >/dev/null
  unset passphrase_one passphrase_two
  chmod 600 "$archive" "$manifest"
  backup_created=1
  echo "Verified encrypted backup: $archive"
else
  echo "No previous JARVIS files were found; no backup was needed."
fi

python3 "$SOURCE_DIR/scripts/migrate_secrets.py"

mkdir -p "$APP_DIR" "$CONFIG_DIR" "$STATE_DIR"
cp -a "$SOURCE_DIR/app" "$SOURCE_DIR/config" "$SOURCE_DIR/scripts" "$SOURCE_DIR/tests" "$SOURCE_DIR/requirements.txt" "$APP_DIR/"
if [[ ! -f "$CONFIG_DIR/models.json" ]]; then cp "$SOURCE_DIR/config/models.json" "$CONFIG_DIR/models.json"; fi
if [[ ! -f "$CONFIG_DIR/repositories.json" ]]; then cp "$SOURCE_DIR/config/repositories.json" "$CONFIG_DIR/repositories.json"; fi
if [[ ! -f "$CONFIG_DIR/jarvis.env" ]]; then
  printf '%s\n' \
    'export JARVIS_HOST=127.0.0.1' \
    'export JARVIS_PORT=8000' \
    'export LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1' \
    'export JARVIS_PIPER_MODEL=$HOME/Desktop/piper_voices/jarvis-high.onnx' \
    'export JARVIS_UPLOAD_DIR=$HOME/Desktop/JARVIS_UPLOADS' \
    'export JARVIS_WHISPER_MODEL=base.en' \
    'export JARVIS_TAILSCALE_SERVE=1' \
    > "$CONFIG_DIR/jarvis.env"
fi
chmod 600 "$CONFIG_DIR/secrets.env" "$CONFIG_DIR/jarvis.env"
chmod +x "$APP_DIR/scripts/"*.sh "$APP_DIR/scripts/desktop-launcher"

PYTHONPATH="$APP_DIR" JARVIS_DB_PATH="$STATE_DIR/jarvis.db" python3 - <<'PY'
from pathlib import Path
from app.database import Database

database = Database(Path.home() / ".local" / "state" / "jarvis" / "jarvis.db")
count = database.import_legacy(Path.home() / "Desktop" / "chat_history.json")
print(f"Imported {count} legacy chat messages into SQLite.")
PY

if [[ "${JARVIS_SKIP_DEPENDENCIES:-0}" != "1" ]]; then
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
else
  echo "Skipping dependency installation because JARVIS_SKIP_DEPENDENCIES=1."
fi

mkdir -p "$HOME/Desktop"
install -m 700 "$APP_DIR/scripts/desktop-launcher" "$HOME/Desktop/JARVIS"

# These inactive legacy copies are now represented by the verified encrypted
# backup. Removing them prevents the unchanged credential from remaining in
# shareable Desktop source files. Project folders, models, voices, and the old
# virtual environment are deliberately untouched.
if ((${#legacy_to_remove[@]})); then
  rm -f -- "${legacy_to_remove[@]}"
fi

echo
if [[ "$backup_created" == "1" ]]; then
  echo "JARVIS 2.4 installed. Replaced files are preserved in the verified encrypted backup."
else
  echo "JARVIS 2.4 installed. This was a fresh install, so no backup archive was required."
fi
echo "Launch it with: $HOME/Desktop/JARVIS"
echo "Access token remains stored locally in: $CONFIG_DIR/secrets.env"
echo "To use another tailnet device, run: $APP_DIR/scripts/enable_private_tailscale.sh"
echo "To configure email or change the login token, run: $APP_DIR/scripts/configure.sh"
