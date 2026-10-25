"""Shared helpers: text sanitation, speed parsing, typing indicators, and logging setup."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import Bot
from telegram.constants import ChatAction
from telegram.error import TelegramError

from config import MAX_SPEED, MIN_SPEED

log = logging.getLogger("voicebot.utils")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    """Normalize user text: strip control characters, collapse excess whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_speed(raw: str) -> float | None:
    """Parse a speed value in the allowed range. Returns None if invalid."""
    try:
        value = float(raw.strip().rstrip("x").replace(",", "."))
    except (ValueError, AttributeError):
        return None
    if not math.isfinite(value) or not MIN_SPEED <= value <= MAX_SPEED:
        return None
    return round(value, 2)


@asynccontextmanager
async def chat_action(bot: Bot, chat_id: int, action: ChatAction) -> AsyncIterator[None]:
    """Keep a chat action (e.g. 'recording voice') visible while the block runs."""

    async def pulse() -> None:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action=action)
            except TelegramError as exc:
                log.debug("send_chat_action failed: %s", type(exc).__name__)
            await asyncio.sleep(4)

    task = asyncio.create_task(pulse())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class SecretRedactor(logging.Filter):
    """Masks known secrets (bot token, API key) in every log record, including tracebacks."""

    MASK = "***REDACTED***"

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, self.MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = self._redact(logging.Formatter().formatException(record.exc_info))
        return True


def setup_logging(level: str, log_file: str, secrets: Iterable[str]) -> None:
    """Configure console and rotating file logging with secret redaction."""
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    redactor = SecretRedactor(secrets)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redactor)

    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    # Keep third-party HTTP clients quiet so they don't dump request details.
    for noisy in ("httpx", "httpcore", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
