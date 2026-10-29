import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ds_qq.config import Settings
from ds_qq.db import Database
from ds_qq.deepseek import DeepSeekClient
from ds_qq.memory import MemoryService
from ds_qq.onebot import OneBotConnection
from ds_qq.persona import PersonaManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    time: str
    group_id: int
    user_id: int | None
    message: str | None
    prompt: str | None


class MessageScheduler:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        deepseek: DeepSeekClient,
        persona: PersonaManager,
        memory: MemoryService,
        connection_provider: Callable[[], OneBotConnection | None],
    ):
        self._settings = settings
        self._database = database
        self._deepseek = deepseek
        self._persona = persona
        self._memory = memory
        self._connection_provider = connection_provider
        self._path = settings.bot_scheduler_file
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.run_due(datetime.now(ZoneInfo("Asia/Shanghai")))
            except Exception as exc:
                logger.error("Scheduled message pass failed: %s", exc)
            await asyncio.sleep(15)

    async def run_due(self, now: datetime) -> None:
        jobs = self._load_jobs()
        for job in jobs:
            if job.time != now.strftime("%H:%M"):
                continue
            run_date = now.date().isoformat()
            if self._database.has_scheduled_run(job.id, run_date):
                continue
            connection = self._connection_provider()
            if connection is None:
                continue
            try:
                text = await self._render(job, connection)
                await connection.send_group_message(job.group_id, text, job.user_id)
                self._database.save_message(
                    group_id=job.group_id,
                    user_id=connection.self_id or 0,
                    message_id=None,
                    role="assistant",
                    display_name="大肥鱼",
                    text=text,
                )
                self._database.record_scheduled_run(job.id, run_date, job.group_id)
                logger.info("Sent scheduled job %s to group %s", job.id, job.group_id)
            except Exception as exc:
                logger.error("Scheduled job %s failed: %s", job.id, exc)

    async def _render(self, job: ScheduledJob, connection: OneBotConnection) -> str:
        if job.message is not None:
            return job.message
        if job.prompt is None or connection.self_id is None:
            raise ValueError("AI scheduled jobs require prompt and bot self_id")
        context = self._database.build_context(
            job.group_id, max_messages=10, max_chars=6000, max_images=0
        )
        prompt = self._persona.system_prompt(self._memory.prompt(job.group_id))
        prompt += (
            "\n\n你将要主动在群里发一条消息。直接输出要发送的正文，"
            "不要解释任务，不要引用自己，默认控制在120字以内。"
        )
        context.append({"role": "user", "content": job.prompt})
        reply = await self._deepseek.complete(
            group_id=job.group_id,
            session_id=self._database.get_active_session(job.group_id),
            trigger_message_id=None,
            context=context,
            system_prompt=prompt,
            purpose="scheduled",
        )
        return reply.content

    def _load_jobs(self) -> list[ScheduledJob]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read scheduler config %s: %s", self._path, exc)
            return []
        jobs = []
        for item in data.get("jobs", []):
            try:
                jobs.append(self._parse_job(item))
            except (TypeError, ValueError) as exc:
                logger.warning("Ignored invalid scheduled job: %s", exc)
        return jobs

    def _parse_job(self, item: dict) -> ScheduledJob:
        if not item.get("enabled", True):
            raise ValueError("disabled")
        job_id = str(item["id"]).strip()
        run_time = str(item["time"])
        datetime.strptime(run_time, "%H:%M")
        group_id = int(item.get("group_id") or 0)
        if group_id == 0 and len(self._settings.allowed_group_ids) == 1:
            group_id = next(iter(self._settings.allowed_group_ids))
        if not job_id or group_id not in self._settings.allowed_group_ids:
            raise ValueError("job id or group whitelist")
        message = item.get("message")
        prompt = item.get("prompt")
        if bool(message) == bool(prompt):
            raise ValueError("exactly one of message or prompt is required")
        user_id = item.get("user_id")
        return ScheduledJob(
            id=job_id,
            time=run_time,
            group_id=group_id,
            user_id=int(user_id) if user_id is not None else None,
            message=str(message).strip() if message else None,
            prompt=str(prompt).strip() if prompt else None,
        )
