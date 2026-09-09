from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    active_project TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS memories (
                    key TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
        if not self.list_sessions():
            self.create_session("default")

    def create_session(self, session_id: str | None = None) -> dict:
        session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (session_id, "New thread", now, now),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return self.create_session(session_id)
        return dict(row)

    def list_sessions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        if not self.list_sessions():
            self.create_session("default")

    def rename_session(self, session_id: str, title: str) -> dict:
        clean = " ".join(title.strip().split())[:80]
        if not clean:
            raise ValueError("Thread name is required")
        self.get_session(session_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET title=?,updated_at=? WHERE id=?",
                (clean, time.time(), session_id),
            )
        return self.get_session(session_id)

    def set_project(self, session_id: str, project: str | None) -> None:
        self.create_session(session_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET active_project=?,updated_at=? WHERE id=?",
                (project, time.time(), session_id),
            )

    def set_summary(self, session_id: str, summary: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET summary=?,updated_at=? WHERE id=?",
                (summary[:12000], time.time(), session_id),
            )

    def add_message(self, session_id: str, role: str, content: str) -> None:
        session = self.get_session(session_id)
        now = time.time()
        title = session["title"]
        if role == "user" and title == "New thread":
            title = content.strip().replace("\n", " ")[:60] or title
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET title=?,updated_at=? WHERE id=?",
                (title, now, session_id),
            )

    def messages(self, session_id: str, limit: int = 40) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role,content FROM (SELECT id,role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (session_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def message_count(self, session_id: str) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0])

    def recent_user_topics(self, limit: int = 12) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT content FROM messages WHERE role='user' ORDER BY id DESC LIMIT ?",
                (limit * 2,),
            ).fetchall()
        result: list[str] = []
        seen: set[str] = set()
        for row in rows:
            content = " ".join(row["content"].strip().split())[:300]
            normalized = content.lower()
            if content and normalized not in seen:
                seen.add(normalized)
                result.append(content)
            if len(result) >= limit:
                break
        return result

    def list_memories(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM memories ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def upsert_memory(self, key: str, content: str, source: str = "manual") -> dict:
        clean_key = re.sub(r"[^a-z0-9_-]+", "_", key.strip().lower()).strip("_")[:80]
        clean_content = " ".join(content.strip().split())[:2000]
        if not clean_key or not clean_content:
            raise ValueError("Memory key and content are required")
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO memories(key,content,source,created_at,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET content=excluded.content,source=excluded.source,updated_at=excluded.updated_at""",
                (clean_key, clean_content, source[:40], now, now),
            )
            row = conn.execute("SELECT * FROM memories WHERE key=?", (clean_key,)).fetchone()
        return dict(row)

    def delete_memory(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM memories WHERE key=?", (key,))

    def learn_from_message(self, content: str) -> list[dict]:
        learned: list[dict] = []
        text = " ".join(content.strip().split())
        patterns = [
            ("user_name", r"\b(?:my name is|call me)\s+([A-Za-z][A-Za-z' -]{0,40})[.!?]?$"),
            ("user_preference", r"\bi (?:prefer|like)\s+(.{2,300})[.!?]?$"),
            ("remembered_fact", r"\bremember(?: that)?\s+(.{2,500})[.!?]?$"),
        ]
        for key, expression in patterns:
            match = re.search(expression, text, re.IGNORECASE)
            if match:
                learned.append(self.upsert_memory(key, match.group(1).strip(), "conversation"))
        return learned

    def import_legacy(self, path: Path) -> int:
        if not path.exists() or self.message_count("default"):
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        imported = 0
        for session_id, messages in data.items():
            if not isinstance(messages, list):
                continue
            self.create_session(str(session_id))
            for message in messages:
                role = message.get("role")
                content = message.get("content")
                if role in {"user", "assistant"} and isinstance(content, str):
                    self.add_message(str(session_id), role, content)
                    imported += 1
        return imported
