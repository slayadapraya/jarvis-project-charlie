from __future__ import annotations

import json
from typing import Any


SUPPORTED_TOOLS = {
    "read_file",
    "search_files",
    "git_status",
    "apply_patch",
    "run_command",
    "send_email",
}


def parse_text_tool_call(content: str) -> tuple[str, dict[str, Any]] | None:
    """Recover a tool request rendered as JSON text by weaker chat templates.

    This never executes anything. Mutating calls still pass through the normal
    pending-action approval system.
    """
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        function = value.get("function") if isinstance(value.get("function"), dict) else value
        name = function.get("name")
        arguments = function.get("arguments", {})
        if name not in SUPPORTED_TOOLS:
            continue
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                continue
        if isinstance(arguments, dict):
            return str(name), arguments
    return None
