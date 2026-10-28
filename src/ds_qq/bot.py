import asyncio
import json
import logging
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ds_qq.config import Settings, get_settings
from ds_qq.db import Database
from ds_qq.deepseek import DeepSeekClient, DeepSeekError
from ds_qq.images import ImageError, ImageStore
from ds_qq.onebot import OneBotConnection, parse_message

logger = logging.getLogger(__name__)


class GroupBot:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        deepseek: DeepSeekClient,
        images: ImageStore,
    ):
        self._settings = settings
        self._database = database
        self._deepseek = deepseek
        self._images = images
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_trigger: dict[int, float] = {}

    async def handle_group_message(
        self, event: dict, connection: OneBotConnection, self_id: int
    ) -> None:
        group_id = int(event["group_id"])
        user_id = int(event["user_id"])
        if group_id not in self._settings.allowed_group_ids or user_id == self_id:
            return
        async with self._locks[group_id]:
            await self._handle_locked(event, connection, self_id, group_id, user_id)

    async def _handle_locked(
        self,
        event: dict,
        connection: OneBotConnection,
        self_id: int,
        group_id: int,
        user_id: int,
    ) -> None:
        parsed = parse_message(event.get("message"), self_id)
        text = parsed.text
        if parsed.reply_to_id:
            text = f"[回复消息 {parsed.reply_to_id}] {text}".strip()
        images = []
        for url in parsed.image_urls[:6]:
            try:
                images.append(await self._images.download(url))
            except ImageError as exc:
                logger.warning("Could not store image from group %s: %s", group_id, exc)
                text = f"{text} [图片下载失败]".strip()
        sender = event.get("sender") or {}
        display_name = str(sender.get("card") or sender.get("nickname") or user_id)
        message_id = str(event["message_id"])
        stored_id = self._database.save_message(
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
            role="user",
            display_name=display_name,
            text=text,
            images=images,
        )
        if stored_id is None:
            return

        command = text.split(maxsplit=1)[0].lower() if text else ""
        triggered = parsed.at_bot or command in {"/help", "/new", "/stats"}
        if not triggered:
            return
        if command == "/help":
            await connection.send_group_reply(group_id, message_id, self._help_text())
            return
        if command == "/new":
            self._database.start_new_session(group_id)
            await connection.send_group_reply(
                group_id, message_id, "已开启新会话，历史消息仍已保留。"
            )
            return
        if command == "/stats":
            stats = self._database.group_stats(group_id)
            await connection.send_group_reply(
                group_id,
                message_id,
                f"本群已保存 {stats['messages']} 条消息，共 {stats['sessions']} 个会话。",
            )
            return
        if not text and not images:
            await connection.send_group_reply(group_id, message_id, "我在，有什么可以帮你？")
            return
        elapsed = time.monotonic() - self._last_trigger.get(group_id, 0)
        if elapsed < self._settings.bot_group_cooldown_seconds:
            logger.info("Ignored cooldown trigger in group %s", group_id)
            return
        self._last_trigger[group_id] = time.monotonic()

        session_id = self._database.get_active_session(group_id)
        context = self._database.build_context(
            group_id,
            max_messages=self._settings.bot_context_messages,
            max_chars=self._settings.bot_context_chars,
            max_images=self._settings.bot_context_images,
        )
        try:
            reply = await self._deepseek.complete(
                group_id=group_id,
                session_id=session_id,
                trigger_message_id=stored_id,
                context=context,
            )
        except DeepSeekError as exc:
            logger.error("DeepSeek request failed for group %s: %s", group_id, exc)
            await connection.send_group_reply(
                group_id, message_id, "我暂时无法连接 DeepSeek，请稍后再试。"
            )
            return
        await connection.send_group_reply(group_id, message_id, reply.content)
        self._database.save_message(
            group_id=group_id,
            user_id=self_id,
            message_id=None,
            role="assistant",
            display_name="DeepSeek",
            text=reply.content,
            session_id=session_id,
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "使用方式：@我 后输入问题，也可以直接发送图片或表情并 @我。\n"
            "/new：开启新会话（保留历史）\n"
            "/stats：查看本群存储统计\n"
            "/help：显示帮助"
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.validate_bot()
        settings.ensure_data_dirs()
        database = Database(settings.database_path)
        deepseek = DeepSeekClient(settings, database)
        images = ImageStore(settings)
        app.state.bot = GroupBot(settings, database, deepseek, images)
        app.state.tasks = set()
        try:
            yield
        finally:
            for task in app.state.tasks:
                task.cancel()
            await deepseek.close()
            await images.close()
            database.close()

    app = FastAPI(title="ds_qq bot", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/onebot/v11/ws")
    async def onebot_ws(websocket: WebSocket) -> None:
        expected = settings.onebot_access_token.get_secret_value()
        authorization = websocket.headers.get("authorization", "")
        supplied = authorization.removeprefix("Bearer ").strip()
        supplied = websocket.query_params.get("access_token", supplied)
        if not secrets.compare_digest(supplied, expected):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        connection = OneBotConnection(websocket)
        logger.info("NapCat connected")
        try:
            while True:
                event = await websocket.receive_json()
                if connection.resolve_response(event):
                    continue
                if event.get("post_type") != "message" or event.get("message_type") != "group":
                    continue
                self_id = int(event.get("self_id") or 0)
                tasks = websocket.app.state.tasks
                if len(tasks) >= 100:
                    logger.warning("Dropped group event because the task queue is full")
                    continue
                task = asyncio.create_task(
                    websocket.app.state.bot.handle_group_message(event, connection, self_id)
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            connection.close()
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()
            logger.info("NapCat disconnected")

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    try:
        settings.validate_bot()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    uvicorn.run(
        create_app(settings),
        host=settings.bot_host,
        port=settings.bot_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
