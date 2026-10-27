from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Attachment:
    id: int
    path: str
    mime_type: str


@dataclass(frozen=True)
class StoredMessage:
    id: int
    session_id: int
    group_id: int
    user_id: int
    role: str
    display_name: str
    text: str
    created_at: float
    attachments: tuple[Attachment, ...]


@dataclass(frozen=True)
class DeepSeekReply:
    content: str
    finish_reason: str | None
    usage: dict[str, Any]
