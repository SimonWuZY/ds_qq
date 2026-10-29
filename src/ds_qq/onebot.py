import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


class OneBotError(RuntimeError):
    pass


@dataclass
class ParsedMessage:
    text_parts: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    at_bot: bool = False
    reply_to_id: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()


def parse_message(segments: Any, self_id: int) -> ParsedMessage:
    parsed = ParsedMessage()
    if isinstance(segments, str):
        parsed.text_parts.append(segments)
        return parsed
    if not isinstance(segments, list):
        return parsed
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        kind = segment.get("type")
        data = segment.get("data") or {}
        if kind == "text":
            parsed.text_parts.append(str(data.get("text") or ""))
        elif kind == "at":
            if str(data.get("qq")) == str(self_id):
                parsed.at_bot = True
            else:
                name = data.get("name") or data.get("qq")
                parsed.text_parts.append(f"@{name} ")
        elif kind in {"image", "mface"}:
            url = data.get("url")
            if url:
                parsed.image_urls.append(str(url))
            else:
                parsed.text_parts.append("[贴纸]" if kind == "mface" else "[图片]")
        elif kind == "face":
            parsed.text_parts.append(f"[QQ表情:{data.get('id', '')}]")
        elif kind == "reply":
            parsed.reply_to_id = str(data.get("id"))
        elif kind == "forward":
            parsed.text_parts.append("[合并转发消息]")
        elif kind == "json":
            parsed.text_parts.append("[JSON 卡片消息]")
    return parsed


class OneBotConnection:
    def __init__(self, websocket: WebSocket):
        self._websocket = websocket
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.self_id: int | None = None

    def resolve_response(self, event: dict[str, Any]) -> bool:
        echo = event.get("echo")
        if echo is None or str(echo) not in self._pending:
            return False
        future = self._pending.pop(str(echo))
        if not future.done():
            future.set_result(event)
        return True

    def close(self, error: Exception | None = None) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error or OneBotError("NapCat disconnected"))
        self._pending.clear()

    async def call(
        self, action: str, params: dict[str, Any], timeout_seconds: float = 15
    ) -> dict[str, Any]:
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        try:
            await self._websocket.send_json({"action": action, "params": params, "echo": echo})
            response = await asyncio.wait_for(future, timeout_seconds)
        finally:
            self._pending.pop(echo, None)
        if response.get("status") != "ok" and response.get("retcode") != 0:
            wording = response.get("wording", "unknown")
            raise OneBotError(f"OneBot action {action} failed: {wording}")
        return response.get("data") or {}

    async def send_group_reply(self, group_id: int, message_id: str, text: str) -> None:
        chunks = [text[index : index + 1800] for index in range(0, len(text), 1800)] or ["…"]
        for index, chunk in enumerate(chunks):
            message: list[dict[str, Any]] = []
            if index == 0 and message_id:
                message.append({"type": "reply", "data": {"id": message_id}})
            message.append({"type": "text", "data": {"text": chunk}})
            await self.call("send_group_msg", {"group_id": group_id, "message": message})

    async def send_group_message(
        self, group_id: int, text: str, user_id: int | None = None
    ) -> None:
        chunks = [text[index : index + 1800] for index in range(0, len(text), 1800)] or ["…"]
        for index, chunk in enumerate(chunks):
            message: list[dict[str, Any]] = []
            if index == 0 and user_id is not None:
                message.append({"type": "at", "data": {"qq": str(user_id)}})
            message.append({"type": "text", "data": {"text": chunk}})
            await self.call("send_group_msg", {"group_id": group_id, "message": message})
