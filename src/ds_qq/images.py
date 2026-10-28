import hashlib
from typing import Any

import httpx

from ds_qq.config import Settings


class ImageError(RuntimeError):
    pass


class ImageStore:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._directory = settings.images_dir
        self._max_bytes = settings.bot_max_image_bytes
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20), follow_redirects=True, max_redirects=3
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def download(self, url: str) -> dict[str, Any]:
        parsed = httpx.URL(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ImageError("Unsupported image URL")
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > self._max_bytes:
                    raise ImageError("Image exceeds size limit")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_bytes:
                        raise ImageError("Image exceeds size limit")
        except (httpx.HTTPError, ValueError) as exc:
            raise ImageError(f"Image download failed: {type(exc).__name__}") from exc

        mime_type = self._detect_mime(content)
        if not mime_type:
            raise ImageError("Unsupported image format")
        digest = hashlib.sha256(content).hexdigest()
        extension = mime_type.split("/")[1]
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{digest}.{extension}"
        if not path.exists():
            path.write_bytes(content)
        return {
            "path": str(path.resolve()),
            "mime_type": mime_type,
            "sha256": digest,
            "size_bytes": len(content),
        }

    @staticmethod
    def _detect_mime(content: bytearray) -> str | None:
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        return None
