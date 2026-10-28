import asyncio
import time
from typing import Any

import httpx

from ds_qq.config import Settings
from ds_qq.db import Database
from ds_qq.models import DeepSeekReply


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        client: httpx.AsyncClient | None = None,
    ):
        self._settings = settings
        self._database = database
        self._client = client or httpx.AsyncClient(
            base_url=settings.deepseek_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.deepseek_timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(
        self,
        *,
        group_id: int,
        session_id: int,
        trigger_message_id: int,
        context: list[dict[str, Any]],
        system_prompt: str | None = None,
    ) -> DeepSeekReply:
        prompt = system_prompt or self._settings.bot_system_prompt
        messages = [{"role": "system", "content": prompt}, *context]
        payload = {
            "model": self._settings.deepseek_model,
            "messages": messages,
            "max_tokens": self._settings.deepseek_max_output_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.deepseek_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        last_error = "Unknown DeepSeek error"

        for attempt in range(self._settings.deepseek_max_retries + 1):
            try:
                response = await self._client.post(
                    "/chat/completions", json=payload, headers=headers
                )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                if response.is_error:
                    raise DeepSeekError(self._http_error(response))
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise DeepSeekError("DeepSeek returned an empty response")
                usage = data.get("usage") or {}
                reply = DeepSeekReply(
                    content=content.strip(),
                    finish_reason=data["choices"][0].get("finish_reason"),
                    usage=usage,
                )
                self._record(
                    request_id=response.headers.get("x-request-id"),
                    group_id=group_id,
                    session_id=session_id,
                    trigger_message_id=trigger_message_id,
                    status="success",
                    started=started,
                    usage=usage,
                    finish_reason=reply.finish_reason,
                )
                return reply
            except DeepSeekError as exc:
                last_error = str(exc)
                break
            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                KeyError,
                ValueError,
                TypeError,
            ) as exc:
                last_error = self._safe_error(exc)
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if not retryable or attempt >= self._settings.deepseek_max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 8))

        self._record(
            request_id=None,
            group_id=group_id,
            session_id=session_id,
            trigger_message_id=trigger_message_id,
            status="error",
            started=started,
            error=last_error,
        )
        raise DeepSeekError(last_error)

    def _record(
        self,
        *,
        request_id: str | None,
        group_id: int,
        session_id: int,
        trigger_message_id: int,
        status: str,
        started: float,
        usage: dict[str, Any] | None = None,
        finish_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        self._database.record_usage(
            request_id=request_id,
            group_id=group_id,
            session_id=session_id,
            trigger_message_id=trigger_message_id,
            model=self._settings.deepseek_model,
            status=status,
            latency_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
            prices=(
                self._settings.deepseek_price_cache_hit,
                self._settings.deepseek_price_cache_miss,
                self._settings.deepseek_price_output,
            ),
            finish_reason=finish_reason,
            error=error,
        )

    @staticmethod
    def _http_error(response: httpx.Response) -> str:
        try:
            data = response.json()
            message = data.get("error", {}).get("message") or str(data)[:300]
        except ValueError:
            message = response.text[:300]
        return f"DeepSeek HTTP {response.status_code}: {message}"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"DeepSeek HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.RequestError):
            return f"DeepSeek connection failed: {type(exc).__name__}"
        return f"Invalid DeepSeek response: {type(exc).__name__}"
