import random
import time
from collections.abc import Callable

from ds_qq.config import Settings
from ds_qq.db import Database


class AmbientReplyPolicy:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.time,
    ):
        self._settings = settings
        self._database = database
        self._rng = rng
        self._clock = clock

    def should_reply(self, group_id: int) -> bool:
        if not self._settings.bot_ambient_enabled:
            return False
        last_message_id, last_sent_at = self._database.ambient_state(group_id)
        new_messages = self._database.messages_after(group_id, last_message_id)
        if new_messages < self._settings.bot_ambient_interval_messages:
            return False
        elapsed = self._clock() - last_sent_at
        if elapsed < self._settings.bot_ambient_cooldown_minutes * 60:
            return False
        today = self._database.count_usage_today(group_id, "ambient")
        if today >= self._settings.bot_ambient_max_daily:
            return False
        return self._rng() < self._settings.bot_ambient_probability

    def mark_sent(self, group_id: int, message_id: int) -> None:
        self._database.mark_ambient_sent(group_id, message_id)
