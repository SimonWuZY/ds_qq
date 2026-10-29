import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ds_qq.config import Settings


class VideoError(RuntimeError):
    pass


class VideoStore:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            timeout_seconds=httpx.Timeout(45), follow_redirects=True, max_redirects=3
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def extract_frames(self, url: str) -> list[dict[str, Any]]:
        parsed = httpx.URL(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise VideoError("Unsupported video URL")
        self._settings.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._settings.data_dir) as temporary:
            source = Path(temporary) / "source.video"
            await self._download(url, source)
            duration = await self._duration(source)
            frame_count = min(3, self._settings.bot_video_frames)
            fractions = (0.1, 0.5, 0.9)[:frame_count]
            frames = []
            for index, fraction in enumerate(fractions):
                output = Path(temporary) / f"frame-{index}.jpg"
                await self._run(
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{duration * fraction:.3f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(1280,iw)':-2",
                    "-q:v",
                    "3",
                    str(output),
                    timeout_seconds=30,
                )
                content = output.read_bytes()
                if not content.startswith(b"\xff\xd8\xff"):
                    raise VideoError("FFmpeg returned an invalid frame")
                digest = hashlib.sha256(content).hexdigest()
                self._settings.videos_dir.mkdir(parents=True, exist_ok=True)
                path = self._settings.videos_dir / f"{digest}.jpg"
                if not path.exists():
                    path.write_bytes(content)
                frames.append(
                    {
                        "path": str(path.resolve()),
                        "mime_type": "image/jpeg",
                        "sha256": digest,
                        "size_bytes": len(content),
                    }
                )
            return frames

    async def _download(self, url: str, source: Path) -> None:
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > self._settings.bot_max_video_bytes:
                    raise VideoError("Video exceeds size limit")
                size = 0
                with source.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
                        size += len(chunk)
                        if size > self._settings.bot_max_video_bytes:
                            raise VideoError("Video exceeds size limit")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise VideoError(f"Video download failed: {type(exc).__name__}") from exc

    async def _duration(self, source: Path) -> float:
        stdout = await self._run(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
            timeout_seconds=20,
        )
        try:
            duration = float(stdout.strip())
        except ValueError as exc:
            raise VideoError("Could not read video duration") from exc
        if duration <= 0:
            raise VideoError("Video has invalid duration")
        return duration

    @staticmethod
    async def _run(*args: str, timeout_seconds: int) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            raise VideoError("FFmpeg is not installed") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise VideoError("Video processing timed out") from exc
        if process.returncode:
            message = stderr.decode(errors="replace")[-300:]
            raise VideoError(f"Video processing failed: {message}")
        return stdout.decode(errors="replace")
