#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import secrets
import shlex
from pathlib import Path


HOME = Path.home()
OUTPUT = HOME / ".config" / "jarvis" / "secrets.env"
CANDIDATES = [
    HOME / "Desktop" / "start_agent.sh",
    HOME / "Desktop" / "start_jarvis.sh",
    HOME / "Desktop" / "index.html",
]


def existing_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not OUTPUT.exists():
        return values
    for line in OUTPUT.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^export\s+([A-Z0-9_]+)=(.*)$", line)
        if not match:
            continue
        try:
            parsed = shlex.split(match.group(2))
        except ValueError:
            continue
        if parsed:
            values[match.group(1)] = parsed[0]
    return values


def extract() -> dict[str, str]:
    values = existing_values()
    patterns = {
        "GMAIL_APP_PASSWORD": [r'export\s+GMAIL_APP_PASSWORD=["\']([^"\']*)'],
        "SENDER_EMAIL": [r'export\s+SENDER_EMAIL=["\']([^"\']*)'],
        "JARVIS_ACCESS_TOKEN": [
            r'const\s+JARVIS_EXECUTE_TOKEN\s*=\s*["\']([^"\']+)',
            r'export\s+JARVIS_ACCESS_TOKEN=["\']([^"\']+)',
        ],
    }
    for path in CANDIDATES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for key, expressions in patterns.items():
            if values.get(key):
                continue
            for expression in expressions:
                match = re.search(expression, text)
                if match:
                    values[key] = match.group(1)
                    break
    values.setdefault("JARVIS_ACCESS_TOKEN", secrets.token_urlsafe(32))
    values.setdefault("SENDER_EMAIL", "")
    values.setdefault("GMAIL_APP_PASSWORD", "")
    return values


def main() -> None:
    values = extract()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"export {key}={shlex.quote(value)}" for key, value in sorted(values.items())]
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(OUTPUT)
    OUTPUT.chmod(0o600)
    print(f"Preserved credentials in {OUTPUT} (mode 600).")


if __name__ == "__main__":
    main()
