import base64
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ds_qq.models import Attachment, StoredMessage


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=10000")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_session_per_group
            ON sessions(group_id) WHERE active=1;

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message_id TEXT,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            display_name TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS unique_qq_message
            ON messages(group_id, message_id) WHERE message_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS messages_session_id_id ON messages(session_id, id);

        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS attachments_message_id ON attachments(message_id);

        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            group_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            trigger_message_id INTEGER REFERENCES messages(id),
            model TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('success', 'error')),
            latency_ms INTEGER NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
            cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            finish_reason TEXT,
            error TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS api_usage_created_at ON api_usage(created_at);
        CREATE INDEX IF NOT EXISTS api_usage_group_id ON api_usage(group_id);
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)

    def get_active_session(self, group_id: int) -> int:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT id FROM sessions WHERE group_id=? AND active=1", (group_id,)
            ).fetchone()
            if row:
                return int(row["id"])
            cursor = self._connection.execute(
                "INSERT INTO sessions(group_id, created_at) VALUES (?, ?)", (group_id, time.time())
            )
            return int(cursor.lastrowid)

    def start_new_session(self, group_id: int) -> int:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET active=0 WHERE group_id=? AND active=1", (group_id,)
            )
            cursor = self._connection.execute(
                "INSERT INTO sessions(group_id, created_at) VALUES (?, ?)", (group_id, time.time())
            )
            return int(cursor.lastrowid)

    def save_message(
        self,
        *,
        group_id: int,
        user_id: int,
        message_id: str | None,
        role: str,
        display_name: str,
        text: str,
        images: list[dict[str, Any]] | None = None,
        session_id: int | None = None,
        created_at: float | None = None,
    ) -> int | None:
        session_id = session_id or self.get_active_session(group_id)
        with self._lock, self._connection:
            if message_id is not None:
                duplicate = self._connection.execute(
                    "SELECT id FROM messages WHERE group_id=? AND message_id=?",
                    (group_id, message_id),
                ).fetchone()
                if duplicate:
                    return None
            cursor = self._connection.execute(
                """INSERT INTO messages(
                    session_id, group_id, user_id, message_id, role, display_name, text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    group_id,
                    user_id,
                    message_id,
                    role,
                    display_name,
                    text,
                    created_at or time.time(),
                ),
            )
            stored_id = int(cursor.lastrowid)
            for image in images or []:
                self._connection.execute(
                    """INSERT INTO attachments(message_id, path, mime_type, sha256, size_bytes)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        stored_id,
                        image["path"],
                        image["mime_type"],
                        image["sha256"],
                        image["size_bytes"],
                    ),
                )
            return stored_id

    def _load_message(self, row: sqlite3.Row) -> StoredMessage:
        attachments = self._connection.execute(
            "SELECT id, path, mime_type FROM attachments WHERE message_id=? ORDER BY id",
            (row["id"],),
        ).fetchall()
        return StoredMessage(
            id=row["id"],
            session_id=row["session_id"],
            group_id=row["group_id"],
            user_id=row["user_id"],
            role=row["role"],
            display_name=row["display_name"],
            text=row["text"],
            created_at=row["created_at"],
            attachments=tuple(
                Attachment(
                    id=attachment["id"],
                    path=attachment["path"],
                    mime_type=attachment["mime_type"],
                )
                for attachment in attachments
            ),
        )

    def build_context(
        self, group_id: int, *, max_messages: int, max_chars: int, max_images: int
    ) -> list[dict[str, Any]]:
        with self._lock:
            session_id = self.get_active_session(group_id)
            rows = self._connection.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, max_messages),
            ).fetchall()
            messages = [self._load_message(row) for row in rows]

        selected: list[dict[str, Any]] = []
        char_count = 0
        image_count = 0
        for message in messages:
            text = message.text
            if message.role == "user":
                text = f"{message.display_name}: {text}" if text else f"{message.display_name}:"
            if selected and char_count + len(text) > max_chars:
                break
            text = text[: max_chars - char_count]
            char_count += len(text)
            content: str | list[dict[str, Any]] = text
            if message.role == "user":
                parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
                for attachment in reversed(message.attachments):
                    if image_count >= max_images:
                        continue
                    path = Path(attachment.path)
                    try:
                        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    except OSError:
                        continue
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{attachment.mime_type};base64,{encoded}"
                            },
                        }
                    )
                    image_count += 1
                parts[1:] = reversed(parts[1:])
                content = parts
            selected.append({"role": message.role, "content": content})

        selected.reverse()
        return selected

    def group_stats(self, group_id: int) -> dict[str, int]:
        with self._lock:
            messages = self._connection.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE group_id=?", (group_id,)
            ).fetchone()["count"]
            sessions = self._connection.execute(
                "SELECT COUNT(*) AS count FROM sessions WHERE group_id=?", (group_id,)
            ).fetchone()["count"]
            return {"messages": int(messages), "sessions": int(sessions)}

    def record_usage(
        self,
        *,
        request_id: str | None,
        group_id: int,
        session_id: int,
        trigger_message_id: int | None,
        model: str,
        status: str,
        latency_ms: int,
        usage: dict[str, Any] | None = None,
        prices: tuple[float, float, float] = (0, 0, 0),
        finish_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        cache_miss = int(usage.get("prompt_cache_miss_tokens") or max(0, prompt - cache_hit))
        hit_price, miss_price, output_price = prices
        cost = (cache_hit * hit_price + cache_miss * miss_price + completion * output_price) / 1e6
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO api_usage(
                    request_id, group_id, session_id, trigger_message_id, model, status,
                    latency_ms, prompt_tokens, completion_tokens, total_tokens,
                    cache_hit_tokens, cache_miss_tokens, estimated_cost_usd,
                    finish_reason, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    group_id,
                    session_id,
                    trigger_message_id,
                    model,
                    status,
                    latency_ms,
                    prompt,
                    completion,
                    total,
                    cache_hit,
                    cache_miss,
                    cost,
                    finish_reason,
                    error,
                    time.time(),
                ),
            )
