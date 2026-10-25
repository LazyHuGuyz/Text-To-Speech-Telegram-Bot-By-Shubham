"""
Text-to-speech service using edge-tts (Microsoft Edge online voices).

No API key or billing is required.

Pipeline:
  1. Split long text into sentence-aligned chunks.
  2. Synthesize each chunk to MP3 via edge-tts (retrying transient failures).
  3. Join the MP3 parts.
  4. Encode to OGG/Opus with ffmpeg through pipes (no temporary files).

Pauses come from sentence punctuation, which edge-tts respects naturally.
Speed is applied through edge-tts's rate parameter.
"""

from __future__ import annotations

import asyncio
import logging
import re

import edge_tts

from config import MAX_SPEED, MIN_SPEED, find_voice

log = logging.getLogger("voicebot.tts")

MAX_CHUNK_CHARS = 1500          # Keeps each request small and reliable
MAX_CONCURRENT_REQUESTS = 3     # Avoids hammering the free service
SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+")  # includes Devanagari danda


class TTSError(Exception):
    """Failure with a message that is safe to show to end users."""


# --- Text preparation ---------------------------------------------------------
def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most max_chars, preferring sentence boundaries."""
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]

    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        # Hard-wrap sentences that are longer than a whole chunk, on word boundaries.
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            piece = sentence[:cut].strip()
            sentence = sentence[cut:].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)

        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()

    if current:
        chunks.append(current)
    return chunks


def rate_string(speed: float) -> str:
    """Convert a multiplier (0.5-2.0) into edge-tts rate format, e.g. 1.25 -> '+25%'."""
    percent = round((speed - 1.0) * 100)
    return f"{percent:+d}%"


# --- Service ------------------------------------------------------------------
class TTSService:
    """Async client for edge-tts with ffmpeg OGG/Opus encoding."""

    def __init__(self, *, timeout: float = 30.0, max_retries: int = 3) -> None:
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def close(self) -> None:
        """Nothing to release for edge-tts; kept so the bot lifecycle stays the same."""
        return None

    async def synthesize(self, text: str, voice_name: str, speed: float) -> bytes:
        """Convert text to OGG/Opus bytes suitable for Telegram voice notes."""
        voice = find_voice(voice_name)
        if voice is None:
            raise TTSError("That voice is not available. Please pick another with /voices.")

        speed = min(max(speed, MIN_SPEED), MAX_SPEED)
        chunks = split_text(text)
        if not chunks:
            raise TTSError("There was nothing to speak in that message.")

        log.debug("Synthesizing %d chunk(s) with voice %s at %.2fx", len(chunks), voice_name, speed)
        mp3_parts = await asyncio.gather(
            *(self._synth_chunk(chunk, voice["name"], speed) for chunk in chunks)
        )
        return await self._encode_ogg(b"".join(mp3_parts))

    async def _synth_chunk(self, text: str, voice: str, speed: float) -> bytes:
        """Synthesize one chunk to MP3, retrying transient failures with backoff."""
        rate = rate_string(speed)

        async with self._semaphore:
            for attempt in range(1, self._max_retries + 1):
                try:
                    communicate = edge_tts.Communicate(text, voice, rate=rate)
                    audio = bytearray()
                    async with asyncio.timeout(self._timeout):
                        async for event in communicate.stream():
                            if event["type"] == "audio":
                                audio.extend(event["data"])

                    if audio:
                        return bytes(audio)
                    log.warning("edge-tts returned no audio (attempt %d/%d)", attempt, self._max_retries)

                except Exception as exc:
                    # Log only the exception type, never request details or secrets.
                    log.warning(
                        "edge-tts error (attempt %d/%d): %s",
                        attempt, self._max_retries, type(exc).__name__,
                    )

                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))

        raise TTSError("The speech service is temporarily unavailable. Please try again shortly.")

    async def _encode_ogg(self, mp3_bytes: bytes) -> bytes:
        """Encode MP3 to OGG/Opus using ffmpeg through pipes (no temporary files)."""
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "mp3", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "32k", "-application", "voip",
            "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(mp3_bytes)
        if process.returncode != 0 or not stdout:
            log.error("ffmpeg encoding failed: %s", stderr.decode("utf-8", "replace")[:300])
            raise TTSError("Audio encoding failed. Please try again.")
        return stdout
