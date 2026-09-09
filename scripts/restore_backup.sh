#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR="$HOME/.local/share/jarvis/backups"
archive="${1:-}"
if [[ -z "$archive" ]]; then
  mapfile -t archives < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'jarvis-before-v2-*.tar.gz.enc' -printf '%p\n' | sort -r)
  if ((${#archives[@]} == 0)); then
    echo "No encrypted JARVIS backups found." >&2
    exit 1
  fi
  archive="${archives[0]}"
fi
archive="$(realpath "$archive")"
case "$archive" in "$BACKUP_DIR"/*.tar.gz.enc) ;; *) echo "Backup must be inside $BACKUP_DIR" >&2; exit 1;; esac

read -rsp "Backup passphrase: " passphrase; echo
temp_dir="$(mktemp -d)"
trap 'rm -rf -- "$temp_dir"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$archive" -pass fd:3 3<<<"$passphrase" | tar -xzf - -C "$temp_dir"
unset passphrase

echo "This will restore the exact backed-up files over their current versions."
read -rp "Type RESTORE to continue: " answer
[[ "$answer" == "RESTORE" ]] || { echo "Cancelled."; exit 0; }
if [[ -d "$temp_dir/home" ]]; then
  cp -a "$temp_dir/home/." "$HOME/"
fi
echo "Backup restored. Files absent at backup time are not deleted automatically."
