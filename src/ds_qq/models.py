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


@dataclass(frozen=True)
class MemberMemory:
    user_id: int
    display_name: str
    facts: tuple[str, ...]
    relationship_notes: str
    interaction_count: int


@dataclass(frozen=True)
class GroupMemory:
    summary: str
    relationship_notes: str
    last_message_id: int
    members: tuple[MemberMemory, ...]


@dataclass(frozen=True)
class MemoryBatch:
    messages: tuple[StoredMessage, ...]
    last_message_id: int
