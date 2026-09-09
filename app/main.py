from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import secrets
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

import httpx
import psutil
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .actions import ActionStore, execute_action
from .config import APP_ROOT, load_settings
from .database import Database
from .model_manager import ModelManager
from .prompts import system_prompt
from .repositories import RepositoryError, RepositoryManager
from .tool_parser import parse_text_tool_call


settings = load_settings()
db = Database(settings.db_path)
db.import_legacy(Path(os.path.expanduser(os.getenv("JARVIS_LEGACY_HISTORY", "~/Desktop/chat_history.json"))))
repos = RepositoryManager(settings.repos)
models = ModelManager(settings.models)
actions = ActionStore()
sessions: dict[str, float] = {}
inference_lock = asyncio.Lock()
_piper_voice = None
_piper_lock = threading.Lock()
_whisper_model = None
_whisper_lock = threading.Lock()

app = FastAPI(title="JARVIS", version="2.4")
STATIC_DIR = APP_ROOT / "app" / "static"
COOKIE_NAME = "jarvis_session"
SESSION_SECONDS = 60 * 60 * 24 * 7


class LoginRequest(BaseModel):
    token: str


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=200_000)
    model: str | None = None
    attachments: list[str] = Field(default_factory=list, max_length=6)


class ProjectRequest(BaseModel):
    project_id: str | None = None


class SessionRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    initialize_git: bool = True


class MemoryRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=2000)


class ActionRequest(BaseModel):
    session_id: str


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


def _authenticated(request: Request) -> bool:
    session_id = request.cookies.get(COOKIE_NAME, "")
    expires = sessions.get(session_id, 0)
    if expires < time.time():
        sessions.pop(session_id, None)
        return False
    sessions[session_id] = time.time() + SESSION_SECONDS
    return True


@app.middleware("http")
async def authentication(request: Request, call_next):
    public = {"/", "/manifest.json", "/health", "/api/auth/login", "/api/auth/status"}
    if request.url.path not in public and not _authenticated(request):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/health")
async def health():
    lm_ok = False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            lm_ok = (await client.get(f"{settings.lm_base_url}/models")).is_success
    except httpx.HTTPError:
        pass
    return {"status": "online", "lm_studio": lm_ok}


@app.get("/api/auth/status")
async def auth_status(request: Request):
    return {"authenticated": _authenticated(request), "configured": bool(settings.access_token)}


@app.post("/api/auth/login")
async def login(body: LoginRequest, response: Response):
    if not settings.access_token:
        raise HTTPException(503, "JARVIS_ACCESS_TOKEN is not configured")
    if not secrets.compare_digest(body.token, settings.access_token):
        raise HTTPException(401, "Invalid access token")
    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = time.time() + SESSION_SECONDS
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )
    return {"ok": True}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/system-stats")
async def system_stats():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home()))
    cpu_temp = None
    try:
        temperatures = psutil.sensors_temperatures()
        for entries in temperatures.values():
            for entry in entries:
                label = (entry.label or "").lower()
                if "package" in label or "tctl" in label or "cpu" in label:
                    cpu_temp = round(entry.current, 1)
                    break
            if cpu_temp is not None:
                break
    except Exception:
        pass

    gpu_temp = vram_used_gb = vram_total_gb = vram_percent = None
    try:
        for card in sorted(Path("/sys/class/drm").glob("card*/device")):
            total_file, used_file = card / "mem_info_vram_total", card / "mem_info_vram_used"
            if total_file.exists() and used_file.exists():
                total = int(total_file.read_text().strip())
                used = int(used_file.read_text().strip())
                if total > 4 * 1024**3:
                    vram_total_gb = round(total / 1024**3, 1)
                    vram_used_gb = round(used / 1024**3, 1)
                    vram_percent = round(used / total * 100, 1)
                    for temp_file in card.glob("hwmon/hwmon*/temp1_input"):
                        gpu_temp = round(int(temp_file.read_text().strip()) / 1000, 1)
                        break
                    break
    except Exception:
        pass

    processes = []
    for process in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            processes.append({
                "pid": process.info["pid"],
                "name": process.info["name"] or "unknown",
                "cpu": round(process.info["cpu_percent"] or 0, 1),
                "ram": round(process.info["memory_percent"] or 0, 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    processes.sort(key=lambda item: (item["cpu"], item["ram"]), reverse=True)
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_temp": cpu_temp,
        "ram_percent": memory.percent,
        "ram_used_gb": round(memory.used / 1024**3, 1),
        "ram_total_gb": round(memory.total / 1024**3, 1),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "gpu_temp": gpu_temp,
        "vram_percent": vram_percent,
        "vram_used_gb": vram_used_gb,
        "vram_total_gb": vram_total_gb,
        "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
        "processes": processes[:10],
    }


@app.get("/api/network")
async def network_status():
    phone_url_file = settings.state_dir / "phone-url.txt"
    phone_url = ""
    try:
        phone_url = phone_url_file.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return {"local_url": f"http://127.0.0.1:{os.getenv('JARVIS_PORT', '8000')}", "phone_url": phone_url}


@app.get("/api/models")
async def list_models():
    loaded = await models.loaded()
    return {
        "default": models.default_model,
        "loaded": loaded,
        "models": list(models.models.values()),
    }


@app.get("/api/projects")
async def list_projects():
    return {"projects": repos.projects()}


@app.post("/api/projects")
async def create_project(body: ProjectCreateRequest):
    try:
        return repos.create_project(body.name, body.initialize_git)
    except RepositoryError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": db.list_sessions()}


@app.post("/api/sessions")
async def create_session():
    return db.create_session()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return {"session": db.get_session(session_id), "messages": db.messages(session_id, 200)}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    db.delete_session(session_id)
    return {"ok": True}


@app.put("/api/sessions/{session_id}/title")
async def rename_session(session_id: str, body: SessionRenameRequest):
    try:
        return db.rename_session(session_id, body.title)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/api/sessions/{session_id}/project")
async def select_project(session_id: str, body: ProjectRequest):
    if body.project_id:
        repos.resolve_project(body.project_id)
    db.set_project(session_id, body.project_id)
    return db.get_session(session_id)


@app.get("/api/memories")
async def list_memories():
    return {"memories": db.list_memories(), "recent_topics": db.recent_user_topics(16)}


@app.put("/api/memories")
async def save_memory(body: MemoryRequest):
    try:
        return db.upsert_memory(body.key, body.content, "manual")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.delete("/api/memories/{key}")
async def delete_memory(key: str):
    db.delete_memory(key)
    return {"ok": True}


def _resolve_upload(upload_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,180}", upload_id):
        raise ValueError("Invalid upload identifier")
    root = settings.upload_dir.resolve()
    path = (root / upload_id).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError(f"Uploaded file is missing: {upload_id}")
    return path


@app.post("/api/uploads")
async def upload_file(file: UploadFile = File(...)):
    original = Path(file.filename or "upload").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original).stem).strip("._") or "upload"
    suffix = re.sub(r"[^A-Za-z0-9.]", "", Path(original).suffix.lower())[:12]
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    name = (stem[:120] + suffix)[:150]
    destination = settings.upload_dir / name
    if destination.exists():
        destination = settings.upload_dir / f"{stem[:105]}-{int(time.time())}-{secrets.token_hex(3)}{suffix}"
    size = 0
    media_type = file.content_type or mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
    try:
        with destination.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ValueError("Files are limited to 20 MB each")
                output.write(chunk)
        destination.chmod(0o600)
    except ValueError as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(413, str(error)) from error
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {
        "id": destination.name,
        "name": original,
        "stored_name": destination.name,
        "media_type": media_type,
        "size": size,
        "saved_to": str(destination),
        "image": media_type.startswith("image/"),
    }


def _attachment_message(message: str, attachment_ids: list[str]) -> str | list[dict]:
    if not attachment_ids:
        return message
    paths = [_resolve_upload(item) for item in attachment_ids]
    if sum(path.stat().st_size for path in paths) > 35 * 1024 * 1024:
        raise ValueError("Attached files are limited to 35 MB per message")
    notes = [message, "", "Files uploaded to the local Desktop inbox:"]
    images: list[dict] = []
    for path in paths:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        notes.append(f"- {path.name} ({media_type}), saved at {path}")
        if media_type.startswith("image/"):
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            images.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}})
        elif media_type.startswith("text/") or path.suffix.lower() in {".py", ".js", ".ts", ".json", ".md", ".csv", ".yaml", ".yml"}:
            excerpt = path.read_text(encoding="utf-8", errors="replace")[:120_000]
            notes.extend([f"\nContents of {path.name}:", excerpt])
    text = "\n".join(notes)
    return [{"type": "text", "text": text}, *images] if images else text


def _transcribe_audio(path: Path) -> str:
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            _whisper_model = WhisperModel(
                os.getenv("JARVIS_WHISPER_MODEL", "base.en"),
                device="cpu",
                compute_type="int8",
                cpu_threads=threads,
            )
        segments, _ = _whisper_model.transcribe(str(path), vad_filter=True, beam_size=3)
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    suffix = re.sub(r"[^A-Za-z0-9.]", "", Path(file.filename or "audio.webm").suffix)[:12] or ".webm"
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as output:
            path = Path(output.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 15 * 1024 * 1024:
                    raise HTTPException(413, "Voice recordings are limited to 15 MB")
                output.write(chunk)
        text = await asyncio.to_thread(_transcribe_audio, path)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, f"Local speech transcription failed: {error}") from error
    finally:
        await file.close()
        if path is not None:
            path.unlink(missing_ok=True)
    if not text:
        raise HTTPException(422, "No speech was detected")
    return {"text": text}


def _project_context(project_id: str | None) -> str:
    if not project_id:
        return ""
    project = repos.resolve_project(project_id)
    return (
        f"Repository ID: {project_id}\nRepository root: {project}\n"
        f"Git state:\n{repos.git_status(project_id)}\n\n"
        f"Repository instructions:\n{repos.instructions(project_id) or '(none)'}\n\n"
        f"Bounded file tree:\n{repos.tree(project_id)}"
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read numbered lines from a file inside the active repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "default": 1},
                    "end_line": {"type": "integer", "default": 400},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search text within the active repository using ripgrep.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Get branch and working tree status for the active repository.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Queue a unified git patch for explicit user approval.",
            "parameters": {
                "type": "object",
                "properties": {"patch": {"type": "string"}, "description": {"type": "string"}},
                "required": ["patch", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Queue an allowlisted test or lint command for explicit user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                },
                "required": ["argv", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Queue a plain-text email for explicit user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]


def _tool_result(name: str, args: dict, session_id: str, project_id: str | None) -> tuple[str, dict | None]:
    if name == "read_file":
        if not project_id:
            raise RepositoryError("Select a project first")
        return repos.read_file(project_id, args["path"], args.get("start_line", 1), args.get("end_line", 400)), None
    if name == "search_files":
        if not project_id:
            raise RepositoryError("Select a project first")
        return repos.search(project_id, args["query"]), None
    if name == "git_status":
        if not project_id:
            raise RepositoryError("Select a project first")
        return repos.git_status(project_id), None
    if name in {"apply_patch", "run_command"}:
        if not project_id:
            raise RepositoryError("Select a project first")
        args["project_id"] = project_id
        action = actions.queue(session_id, name, args, args.get("description", name))
        return f"Queued action {action.id} for user approval.", {"id": action.id, "description": action.description}
    if name == "send_email":
        action = actions.queue(session_id, name, args, f"Send email to {args['to']}: {args['subject']}")
        return f"Queued email {action.id} for user approval.", {"id": action.id, "description": action.description}
    raise ValueError(f"Unknown tool: {name}")


async def _post_completion(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(f"{settings.lm_base_url}/chat/completions", json=payload)
    if not response.is_success:
        raise RuntimeError(f"LM Studio HTTP {response.status_code}: {response.text[-1500:]}")
    return response.json()


async def _refresh_summary(session_id: str, model_id: str) -> None:
    if db.message_count(session_id) < 16 or db.message_count(session_id) % 16:
        return
    try:
        async with inference_lock:
            loaded = await models.loaded()
            if loaded and not any(model_id == item or model_id in item for item in loaded):
                return
            session = db.get_session(session_id)
            transcript = db.messages(session_id, 24)
            prompt = (
                "Update the durable memory for this conversation. Preserve repository choices, user preferences, "
                "requirements, decisions, work completed, failures, and next steps. Remove chatter and obsolete details. "
                "Return plain text under 450 words.\n\nPrevious memory:\n"
                + session.get("summary", "")
                + "\n\nRecent messages:\n"
                + json.dumps(transcript, ensure_ascii=False)
            )
            result = await _post_completion({
                "model": model_id,
                "messages": [{"role": "system", "content": "You maintain precise assistant memory."}, {"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 650,
            })
            summary = result["choices"][0]["message"].get("content", "").strip()
            if summary:
                db.set_summary(session_id, summary)
    except Exception:
        return


async def _stream_completion(payload: dict):
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", f"{settings.lm_base_url}/chat/completions", json=payload) as response:
            if not response.is_success:
                body = (await response.aread()).decode(errors="replace")
                raise RuntimeError(f"LM Studio HTTP {response.status_code}: {body[-1500:]}")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    delta = json.loads(raw)["choices"][0]["delta"].get("content", "")
                except (KeyError, json.JSONDecodeError, TypeError):
                    continue
                if delta:
                    yield delta


@app.post("/api/chat")
async def chat(body: ChatRequest):
    session = db.get_session(body.session_id)
    model_id = body.model or models.default_model
    profile = models.profile(model_id)
    try:
        user_content = _attachment_message(body.message, body.attachments)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    saved_message = body.message
    if body.attachments:
        saved_message += "\n[Attached files: " + ", ".join(body.attachments) + "]"
    db.add_message(body.session_id, "user", saved_message)
    db.learn_from_message(body.message)

    async def event_stream():
        full_reply = ""
        try:
            async with inference_lock:
                yield _event("status", f"Loading {model_id}...")
                await models.ensure_loaded(model_id)
                history = db.messages(body.session_id, 36)
                if body.attachments and history:
                    history[-1] = {"role": "user", "content": user_content}
                messages = [
                    {
                        "role": "system",
                        "content": (
                            system_prompt(
                                profile.get("profile", "general"),
                                session.get("summary", ""),
                                _project_context(session.get("active_project")),
                            )
                            + (
                                f"\n\nEmail is already configured locally for sender {os.getenv('SENDER_EMAIL')}. "
                                "Never ask the user for email credentials. Call send_email when requested; "
                                "the server will obtain explicit approval and report the real SMTP result."
                                if os.getenv("SENDER_EMAIL")
                                else "\n\nEmail credentials are not configured locally."
                            )
                            + "\n\nCross-chat memories:\n"
                            + ("\n".join(f"- {item['key']}: {item['content']}" for item in db.list_memories()) or "(none saved)")
                            + "\n\nRecent questions and conversation topics across chats:\n"
                            + ("\n".join(f"- {item}" for item in db.recent_user_topics(12)) or "(none)")
                        ),
                    },
                    *history,
                ]
                payload = {
                    "model": model_id,
                    "messages": messages,
                    "temperature": profile.get("temperature", 0.4),
                }

                if profile.get("tools"):
                    for _ in range(4):
                        tool_payload = {**payload, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 1200}
                        result = await _post_completion(tool_payload)
                        message = result["choices"][0]["message"]
                        calls = message.get("tool_calls") or []
                        if not calls:
                            content = message.get("content") or ""
                            fallback_call = parse_text_tool_call(content)
                            if fallback_call:
                                name, arguments = fallback_call
                                try:
                                    output, pending = _tool_result(
                                        name,
                                        arguments,
                                        body.session_id,
                                        session.get("active_project"),
                                    )
                                except Exception as error:
                                    output, pending = f"Tool error: {error}", None
                                if pending:
                                    yield _event("action", pending)
                                    content = f"{pending['description']} is ready. Use the Approve or Reject button."
                                else:
                                    content = output
                            full_reply = content
                            yield _event("chunk", content)
                            break
                        messages.append(message)
                        for call in calls:
                            name = call["function"]["name"]
                            try:
                                arguments = json.loads(call["function"].get("arguments") or "{}")
                                output, pending = _tool_result(name, arguments, body.session_id, session.get("active_project"))
                            except Exception as error:
                                output, pending = f"Tool error: {error}", None
                            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
                            if pending:
                                yield _event("action", pending)
                    else:
                        raise RuntimeError("Tool loop exceeded four rounds")
                else:
                    stream_payload = {**payload, "stream": True}
                    async for chunk in _stream_completion(stream_payload):
                        full_reply += chunk
                        yield _event("chunk", chunk)

                if full_reply:
                    db.add_message(body.session_id, "assistant", full_reply)
                yield _event("done", {"saved": bool(full_reply)})
            asyncio.create_task(_refresh_summary(body.session_id, model_id))
        except Exception as error:
            yield _event("error", str(error))

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _event(kind: str, value: Any) -> str:
    return f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"


@app.post("/api/actions/{action_id}/approve")
async def approve_action(action_id: str, body: ActionRequest):
    try:
        action = actions.pop(action_id, body.session_id)
        result = await asyncio.to_thread(
            execute_action,
            action,
            repos,
            os.getenv("SENDER_EMAIL", ""),
            os.getenv("GMAIL_APP_PASSWORD", ""),
        )
    except (ValueError, RepositoryError) as error:
        raise HTTPException(400, str(error)) from error
    db.add_message(body.session_id, "assistant", f"[APPROVED ACTION: {action.description}]\n{result}")
    return {"ok": True, "result": result}


@app.post("/api/actions/{action_id}/reject")
async def reject_action(action_id: str, body: ActionRequest):
    try:
        action = actions.pop(action_id, body.session_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "result": f"Rejected: {action.description}"}


def _synthesize(text: str) -> bytes:
    global _piper_voice
    if settings.piper_model is None or not settings.piper_model.exists():
        raise ValueError("Piper voice model is not configured or does not exist")
    from piper import PiperVoice

    with _piper_lock:
        if _piper_voice is None:
            _piper_voice = PiperVoice.load(str(settings.piper_model))
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = Path(handle.name)
        try:
            with wave.open(str(path), "wb") as output:
                _piper_voice.synthesize_wav(text, output)
            return path.read_bytes()
        finally:
            path.unlink(missing_ok=True)


@app.post("/api/tts")
async def text_to_speech(body: TTSRequest):
    try:
        audio = await asyncio.to_thread(_synthesize, body.text)
    except ValueError as error:
        raise HTTPException(503, str(error)) from error
    return Response(audio, media_type="audio/wav")
