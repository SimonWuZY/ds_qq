import base64
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ds_qq.models import Attachment, GroupMemory, MemberMemory, MemoryBatch, StoredMessage


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

        CREATE TABLE IF NOT EXISTS group_memory (
            group_id INTEGER PRIMARY KEY,
            summary TEXT NOT NULL DEFAULT '',
            relationship_notes TEXT NOT NULL DEFAULT '',
            last_message_id INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS member_memory (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            facts_json TEXT NOT NULL DEFAULT '[]',
            relationship_notes TEXT NOT NULL DEFAULT '',
            interaction_count INTEGER NOT NULL DEFAULT 0,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(group_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS member_memory_group_id ON member_memory(group_id);

        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            group_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            trigger_message_id INTEGER REFERENCES messages(id),
            model TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'chat',
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
            usage_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(api_usage)")
            }
            if "purpose" not in usage_columns:
                self._connection.execute(
                    "ALTER TABLE api_usage ADD COLUMN purpose TEXT NOT NULL DEFAULT 'chat'"
                )

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
            if role == "user":
                now = time.time()
                self._connection.execute(
                    """INSERT INTO member_memory(
                        group_id, user_id, display_name, interaction_count,
                        first_seen_at, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(group_id, user_id) DO UPDATE SET
                        display_name=excluded.display_name,
                        interaction_count=member_memory.interaction_count + 1,
                        last_seen_at=excluded.last_seen_at,
                        updated_at=excluded.updated_at""",
                    (group_id, user_id, display_name, now, now, now),
                )
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

    def get_memory_snapshot(self, group_id: int) -> GroupMemory:
        with self._lock:
            group = self._connection.execute(
                "SELECT * FROM group_memory WHERE group_id=?", (group_id,)
            ).fetchone()
            rows = self._connection.execute(
                "SELECT * FROM member_memory WHERE group_id=? ORDER BY interaction_count DESC",
                (group_id,),
            ).fetchall()
        members = tuple(
            MemberMemory(
                user_id=row["user_id"],
                display_name=row["display_name"],
                facts=tuple(json.loads(row["facts_json"])),
                relationship_notes=row["relationship_notes"],
                interaction_count=row["interaction_count"],
            )
            for row in rows
        )
        return GroupMemory(
            summary=group["summary"] if group else "",
            relationship_notes=group["relationship_notes"] if group else "",
            last_message_id=group["last_message_id"] if group else 0,
            members=members,
        )

    def get_memory_batch(self, group_id: int, limit: int) -> MemoryBatch:
        with self._lock:
            checkpoint = self._connection.execute(
                "SELECT last_message_id FROM group_memory WHERE group_id=?", (group_id,)
            ).fetchone()
            last_message_id = checkpoint["last_message_id"] if checkpoint else 0
            rows = self._connection.execute(
                """SELECT * FROM messages
                WHERE group_id=? AND role='user' AND id>? ORDER BY id LIMIT ?""",
                (group_id, last_message_id, limit),
            ).fetchall()
            messages = tuple(self._load_message(row) for row in rows)
        return MemoryBatch(
            messages=messages,
            last_message_id=messages[-1].id if messages else last_message_id,
        )

    def save_memory_snapshot(
        self,
        *,
        group_id: int,
        summary: str,
        relationship_notes: str,
        last_message_id: int,
        members: list[MemberMemory],
    ) -> None:
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO group_memory(
                    group_id, summary, relationship_notes, last_message_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    summary=excluded.summary,
                    relationship_notes=excluded.relationship_notes,
                    last_message_id=excluded.last_message_id,
                    updated_at=excluded.updated_at""",
                (group_id, summary, relationship_notes, last_message_id, now),
            )
            for member in members:
                self._connection.execute(
                    """INSERT INTO member_memory(
                        group_id, user_id, display_name, facts_json, relationship_notes,
                        first_seen_at, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id, user_id) DO UPDATE SET
                        display_name=excluded.display_name,
                        facts_json=excluded.facts_json,
                        relationship_notes=excluded.relationship_notes,
                        updated_at=excluded.updated_at""",
                    (
                        group_id,
                        member.user_id,
                        member.display_name,
                        json.dumps(member.facts, ensure_ascii=False),
                        member.relationship_notes,
                        now,
                        now,
                        now,
                    ),
                )

    def clear_memory(self, group_id: int) -> None:
        with self._lock, self._connection:
            latest = self._connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS id FROM messages WHERE group_id=?", (group_id,)
            ).fetchone()["id"]
            self._connection.execute("DELETE FROM member_memory WHERE group_id=?", (group_id,))
            self._connection.execute(
                """INSERT INTO group_memory(
                    group_id, summary, relationship_notes, last_message_id, updated_at
                ) VALUES (?, '', '', ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    summary='', relationship_notes='',
                    last_message_id=excluded.last_message_id,
                    updated_at=excluded.updated_at""",
                (group_id, latest, time.time()),
            )

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
        purpose: str = "chat",
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
                    request_id, group_id, session_id, trigger_message_id, model, purpose, status,
                    latency_ms, prompt_tokens, completion_tokens, total_tokens,
                    cache_hit_tokens, cache_miss_tokens, estimated_cost_usd,
                    finish_reason, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    group_id,
                    session_id,
                    trigger_message_id,
                    model,
                    purpose,
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

    def usage_overview(self, days: int) -> dict[str, Any]:
        with self._lock:
            overview = {
                "today": self._usage_summary(
                    "WHERE date(created_at, 'unixepoch', 'localtime') = "
                    "date('now', 'localtime')"
                ),
                "week": self._usage_summary(
                    "WHERE created_at >= strftime("
                    "'%s', date('now', 'localtime', '-6 days', 'start of day'))"
                ),
                "all": self._usage_summary(""),
            }
            period_filter = (
                "WHERE created_at >= strftime('%s', date('now', 'localtime', ?, 'start of day'))"
            )
            period_params = (f"-{days - 1} days",)
            daily = self._connection.execute(
                f"""SELECT date(created_at, 'unixepoch', 'localtime') AS date,
                    COUNT(*) AS requests,
                    COALESCE(SUM(total_tokens), 0) AS tokens,
                    COALESCE(SUM(estimated_cost_usd), 0) AS cost
                FROM api_usage {period_filter}
                GROUP BY date ORDER BY date""",
                period_params,
            ).fetchall()
            groups = self._connection.execute(
                f"""SELECT group_id, COUNT(*) AS requests,
                    COALESCE(SUM(total_tokens), 0) AS tokens,
                    COALESCE(SUM(estimated_cost_usd), 0) AS cost
                FROM api_usage {period_filter}
                GROUP BY group_id ORDER BY tokens DESC LIMIT 10""",
                period_params,
            ).fetchall()
            models = self._connection.execute(
                f"""SELECT model, COUNT(*) AS requests,
                    COALESCE(SUM(total_tokens), 0) AS tokens,
                    COALESCE(SUM(estimated_cost_usd), 0) AS cost
                FROM api_usage {period_filter}
                GROUP BY model ORDER BY tokens DESC""",
                period_params,
            ).fetchall()
            recent = self._connection.execute(
                """SELECT id, request_id, group_id, model, purpose, status, latency_ms,
                    prompt_tokens, completion_tokens, total_tokens, cache_hit_tokens,
                    estimated_cost_usd, finish_reason, error, created_at
                FROM api_usage ORDER BY id DESC LIMIT 20"""
            ).fetchall()
        return {
            **overview,
            "days": days,
            "daily": [dict(row) for row in daily],
            "groups": [dict(row) for row in groups],
            "models": [dict(row) for row in models],
            "recent": [dict(row) for row in recent],
            "generated_at": time.time(),
        }

    def _usage_summary(self, where: str) -> dict[str, float | int]:
        row = self._connection.execute(
            f"""SELECT COUNT(*) AS requests,
                COALESCE(SUM(status = 'success'), 0) AS successes,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,
                COALESCE(SUM(estimated_cost_usd), 0) AS cost,
                COALESCE(AVG(latency_ms), 0) AS average_latency_ms
            FROM api_usage {where}"""
        ).fetchone()
        requests = int(row["requests"])
        prompt_tokens = int(row["prompt_tokens"])
        return {
            "requests": requests,
            "success_rate": (int(row["successes"]) / requests * 100) if requests else 0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "cache_rate": (int(row["cache_hit_tokens"]) / prompt_tokens * 100)
            if prompt_tokens
            else 0,
            "cost": float(row["cost"]),
            "average_latency_ms": float(row["average_latency_ms"]),
        }
