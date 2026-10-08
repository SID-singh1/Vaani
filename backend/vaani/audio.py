"""Async ffmpeg/ffprobe helpers. Subprocesses are awaited, so audio conversion never blocks
the event loop (one slow upload no longer stalls every other request)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from .errors import InvalidAudio

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".mp3",
    ".webm",
    ".m4a",
    ".aac",
    ".mp4",
    ".flac",
    ".mov",
    ".amr",
    ".3gp",
}
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


async def _run(*args: str, timeout: float = 300) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stdout, stderr


async def probe_duration(path: Path) -> float | None:
    """Duration in seconds, or None if it can't be determined."""
    try:
        code, out, _ = await _run(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            timeout=60,
        )
        if code == 0 and out.strip() and out.strip() != b"N/A":
            return float(out.strip())
        # Some containers (e.g. browser-recorded webm) have no duration header; ffmpeg can still tell.
        _, _, err = await _run("ffmpeg", "-hide_banner", "-i", str(path), "-f", "null", "-", timeout=120)
        matches = _DURATION.findall(err.decode(errors="ignore"))
        times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", err.decode(errors="ignore"))
        if times:
            h, m, sec = times[-1]
            return int(h) * 3600 + int(m) * 60 + float(sec)
        if matches:
            h, m, sec = matches[-1]
            return int(h) * 3600 + int(m) * 60 + float(sec)
    except (OSError, ValueError, TimeoutError) as exc:
        log.warning("duration probe failed: %s", exc)
    return None


async def to_wav_16k(src: Path, dst: Path) -> Path:
    """Decode anything ffmpeg understands to 16 kHz mono PCM, the format Whisper expects."""
    code, _, err = await _run(
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-i",
        str(src),
        "-vn",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dst),
    )
    if code != 0 or not dst.exists() or dst.stat().st_size < 1024:
        raise InvalidAudio(detail=f"ffmpeg decode failed: {err.decode(errors='ignore')[-300:]}")
    return dst


async def to_mp3_64k(src: Path, dst: Path) -> Path:
    code, _, err = await _run(
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(dst),
    )
    if code != 0 or not dst.exists():
        raise InvalidAudio(detail=f"ffmpeg mp3 encode failed: {err.decode(errors='ignore')[-300:]}")
    return dst
