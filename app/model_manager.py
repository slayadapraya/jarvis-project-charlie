from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path


class ModelManager:
    def __init__(self, config: dict):
        self.config = config
        self.models = {item["id"]: item for item in config.get("models", [])}
        self.default_model = config.get("default_model", next(iter(self.models), ""))
        self.context_length = int(config.get("context_length", 32768))
        self.lock = asyncio.Lock()

    def profile(self, model_id: str) -> dict:
        return self.models.get(model_id, {"id": model_id, "profile": "general", "tools": False, "temperature": 0.4})

    async def loaded(self) -> list[str]:
        command = self._lms_command()
        try:
            process = await asyncio.create_subprocess_exec(
                command, "ps", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            data = json.loads(output.decode(errors="replace"))
        except Exception:
            return []
        rows = data if isinstance(data, list) else data.get("models", data.get("loadedModels", []))
        found: list[str] = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, str):
                found.append(row)
            elif isinstance(row, dict):
                value = row.get("identifier") or row.get("model") or row.get("id")
                if value:
                    found.append(str(value))
        return found

    async def ensure_loaded(self, model_id: str) -> None:
        async with self.lock:
            loaded = await self.loaded()
            if any(model_id == item or model_id in item for item in loaded):
                return
            command = self._lms_command()
            unload = await asyncio.create_subprocess_exec(
                command, "unload", "--all",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await asyncio.wait_for(unload.communicate(), timeout=30)
            load = await asyncio.create_subprocess_exec(
                command, "load", model_id,
                "--gpu", "max",
                "--context-length", str(self.context_length),
                "--yes",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(load.communicate(), timeout=180)
            if load.returncode:
                raise RuntimeError(f"Could not load {model_id}: {output.decode(errors='replace')[-1000:]}")
            loaded = await self.loaded()
            if loaded and not any(model_id == item or model_id in item for item in loaded):
                raise RuntimeError(f"LM Studio did not report {model_id} as loaded; loaded={loaded}")

    @staticmethod
    def _lms_command() -> str:
        local = Path(os.path.expanduser("~/.lmstudio/bin/lms"))
        return str(local) if local.exists() else "lms"
