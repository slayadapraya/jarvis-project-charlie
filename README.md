# JARVIS 2.4 local assistant

This package replaces the fragile single-file JARVIS runtime with an authenticated, repository-aware local agent. It keeps the existing Qwen, Dolphin, and Qwen-VL entries and adds optional recommendations without downloading or deleting any model.

## What changes

- Chat history and the active repository are stored server-side in SQLite.
- Every thread remembers its selected repository after restarts.
- A Projects panel creates workspace folders, initializes Git, and writes a starter `JARVIS.md`.
- A Memory panel manages durable cross-chat facts and displays recent conversation topics.
- Threads can be renamed from either the header or the pencil button beside each chat.
- The paperclip uploads authenticated phone or desktop files into `~/Desktop/JARVIS_UPLOADS`; attached images are sent to the selected vision model and text files are included as bounded excerpts.
- The microphone button records in current Firefox/Chromium browsers and transcribes locally with Faster Whisper.
- Explicit phrases such as “my name is…”, “I prefer…”, and “remember that…” are learned automatically.
- The model receives Git status, repository instructions, a bounded file tree, recent conversation, and a rolling durable summary.
- Read and search tools are constrained to configured project roots.
- Patches, commands, and emails require an Approve click.
- Patch approval accepts fenced unified diffs and the `*** Begin Patch` instruction format, eliminating the common Qwen “No valid patches in input” failure while still requiring exact, unambiguous context.
- Models that print a JSON tool request instead of emitting LM Studio's structured tool-call field are recovered safely; mutating requests still require the same Approve click.
- Model switches are serialized, unload the previous model, load the requested model, and verify LM Studio state with `lms ps --json`.
- LM Studio error bodies reach the interface instead of becoming a generic failure.
- The browser no longer contains an execution credential.
- Public ngrok and Tailscale Funnel startup remains disabled. Startup automatically configures authenticated Tailscale Serve for private phone access when Tailscale is connected.
- Piper TTS loads only when voice output is requested, so a missing voice cannot prevent server startup.
- The UI has no external CDN dependency.
- The fixed-height HUD keeps the composer visible while only the conversation scrolls.
- Live monitoring shows CPU, RAM, disk, temperatures, VRAM, uptime, and top processes.
- The voice visualizer reacts to generated audio and the working state.
- The launcher prefers Ptyxis, then GNOME Console, GNOME Terminal, or the system terminal fallback.
- The browser opens automatically after the health check succeeds.

## Exact backup and credential preservation

`install.sh` runs before any active JARVIS file is replaced. It finds the legacy Desktop launcher, server, HTML, manifest, history, current v2 installation, and configuration. It hashes regular files and streams the original bytes and metadata directly into an AES-256-CBC encrypted archive using PBKDF2 with 200,000 iterations.

The encryption passphrase is chosen locally during installation and is never placed in this package. The encrypted archives and hash manifests are saved under:

```text
~/.local/share/jarvis/backups/
```

Existing email and JARVIS token values are parsed locally without sourcing the legacy files. Their values are preserved unchanged in:

```text
~/.config/jarvis/secrets.env
```

That file is mode `600`. It is not copied into the downloadable source or the encrypted archive's unencrypted sidecar. The previous browser execution token becomes the new login token when present, so it is not silently changed.

After the encrypted backup is verified and history is imported into SQLite, the installer removes the inactive legacy Desktop server, HTML, launcher script, manifest, and JSON history so the unchanged credential does not remain embedded in shareable source. The exact originals remain recoverable from the encrypted archive. It never deletes or moves `JARVIS_WORKSPACE`, model files, Piper voices, or the old virtual environment.

## Install

Extract the package on the JARVIS PC, open a terminal in the extracted directory, and run:

```bash
chmod +x install.sh
./install.sh
```

If an extracted copy has lost its executable bit, `bash install.sh` works as well.

Choose and retain the backup passphrase. Once installation finishes, launch:

```bash
~/Desktop/JARVIS
```

Open `http://127.0.0.1:8000` and use the access token from `~/.config/jarvis/secrets.env`.
The launcher terminal intentionally stays open while the server is running. Messages such as `Application startup complete` and `Uvicorn running` mean JARVIS is ready, not stuck; closing that terminal stops it.

## Microphone and phone uploads

The microphone records up to 90 seconds in a current Firefox or Chromium browser and sends that audio only to the authenticated local JARVIS server. Faster Whisper transcribes it on the CPU using the `base.en` model with int8 computation. The first use downloads that model and can therefore take longer; later uses reuse its local cache. Set `JARVIS_WHISPER_MODEL` in `~/.config/jarvis/jarvis.env` if another Whisper model is preferred.

Microphone permission requires a secure context: local `http://127.0.0.1` works on the PC, while a phone should use the private HTTPS Tailscale Serve address.

The paperclip accepts up to six attachments per message, 20 MB each and 35 MB total when sent to a model. Uploading immediately saves the original file into:

```text
~/Desktop/JARVIS_UPLOADS/
```

Duplicate filenames receive a timestamp instead of being overwritten. Uploads are mode `600`. Images automatically select the configured vision profile when available. Other binary formats are transferred to the PC but are not claimed to be visually analyzed; common text/code formats are included as bounded text context.

To configure a fresh friend's installation or change email settings without editing files:

```bash
~/.local/share/jarvis/app/scripts/configure.sh
```

The script hides credential input and writes the local secrets file with mode `600`.

## Install from GitHub over SSH

This repository contains no copied credentials, user databases, uploaded files, or encrypted personal backups. Each installation creates its secrets and state locally.

```bash
git clone git@github.com:slayadapraya/jarvis-project-charlie.git
cd jarvis-project-charlie
./install.sh
~/Desktop/JARVIS
```

The repository is public, so no GitHub account is required for HTTPS cloning. SSH cloning requires an SSH key configured with GitHub. HTTPS cloning also works:

```bash
git clone https://github.com/slayadapraya/jarvis-project-charlie.git
```

## Folder layout

```text
~/.local/share/jarvis/app/       application source
~/.local/share/jarvis/venv/      Python environment
~/.local/share/jarvis/backups/   encrypted original backups
~/.config/jarvis/                models, repository roots, non-public secrets
~/.local/state/jarvis/           SQLite database and logs
~/Desktop/JARVIS_WORKSPACE/      projects; never moved or deleted
```

## Repository setup

Repositories are discovered immediately below each root in `~/.config/jarvis/repositories.json`. Copy `templates/JARVIS.md` into a repository and customize it to give JARVIS stable project-specific build commands and constraints.

The Active Repository selector is a safety and context boundary: it determines the one project JARVIS is allowed to inspect, search, patch, and test for that chat. Creating a project from the UI places it under the first configured workspace root.

## Memory behavior

Each chat retains its own rolling summary and active repository. Global memories are shared between threads, while recent user questions provide short-term continuity across chats. Use the Memory panel to add, correct, or delete durable facts. Only explicit patterns are learned automatically; ordinary conversation is not silently promoted into permanent memory.

Only configured command prefixes can be queued. Expand `allowed_commands` deliberately when a project needs another test or lint command.

## Models

The original IDs remain configured:

- `qwen2.5-coder-14b-instruct`
- `dolphin3.0-llama3.1-8b`
- `qwen2.5-vl-7b-instruct`

Dolphin uses a creative/story profile with permissive tone and remains selectable. It does not receive mutation tools because its intended role is casual and creative conversation.

Optional Qwen 3 Coder and Gemma 4 entries are included. LM Studio identifiers differ between downloads, so edit `~/.config/jarvis/models.json` to match the exact identifier printed by `lms ls` before selecting an optional model.

Use the LM Studio estimator before installing/loading a large quantization:

```bash
lms load --estimate-only MODEL_ID --context-length 32768 --gpu max
```

## Private Tailscale access

JARVIS now checks Tailscale every time it starts. When Tailscale is connected, it persistently publishes the local server through private HTTPS Serve and prints a line such as:

```text
Phone URL: https://your-computer.your-tailnet.ts.net
```

The interface also shows a green `PHONE` link after login. Open the printed address on a phone signed into the same tailnet and enter the normal JARVIS access token. The first setup may ask for the Ubuntu administrator password because changing Tailscale Serve configuration can require permission.

To configure or repair it manually, run:

```bash
~/.local/share/jarvis/app/scripts/enable_private_tailscale.sh 8000
```

This uses Tailscale Serve, not Funnel, so it is limited to devices authorized on the tailnet. To disable automatic setup without removing an existing Serve configuration, set `JARVIS_TAILSCALE_SERVE=0` in `~/.config/jarvis/jarvis.env`. Do not restore ngrok or Funnel unless a properly hardened external identity proxy is added.

## Restore

To restore the newest encrypted backup:

```bash
~/.local/share/jarvis/app/scripts/restore_backup.sh
```

Or pass a specific `.tar.gz.enc` path. Restoration requires the original passphrase and a typed `RESTORE` confirmation. It overwrites only paths present in the chosen backup and does not delete newer unrelated files.

## Tests

From the installed application directory:

```bash
~/.local/share/jarvis/venv/bin/python -m unittest discover -s tests -v
```

The tests cover SQLite persistence, thread naming, cross-chat memory, legacy history import, project creation, repository confinement, unified and instruction-format patches, printed tool-call recovery, prompt context, and session-bound approvals.

## Important limitation

This package was built from the files supplied in ChatGPT. ChatGPT cannot remotely inspect or execute it on the PC. The installer performs the PC-local backup, credential migration, dependency installation, and launcher replacement when you run it there.

## Email behavior

The model is told only that email is configured and which sender address is active. The Gmail app password remains in the server process and is never inserted into model context. `send_email` creates an approval card; SMTP runs only after Approve is clicked. JARVIS records success only after Gmail accepts the message. If an older Qwen chat template prints the call as a JSON code block, the server converts that text into the same approval card instead of pretending it was sent.
