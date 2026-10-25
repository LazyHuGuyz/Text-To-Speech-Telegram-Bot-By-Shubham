"""
Central configuration.

- Secrets and deployment settings come from environment variables (.env).
- The voice/language catalogue lives here so it can be edited without touching other modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# --- Speed --------------------------------------------------------------------
MIN_SPEED = 0.5
MAX_SPEED = 2.0
SPEED_PRESETS: tuple[float, ...] = (0.75, 1.0, 1.25, 1.5)

# --- Voice catalogue ----------------------------------------------------------
# Voice names are edge-tts voice short names. To see all available voices run:
#   edge-tts --list-voices
# Hinglish uses Indian English voices, which read romanized Hindi words well.


def _v(name: str, language_code: str, gender: str, label: str) -> dict[str, str]:
    return {"name": name, "language_code": language_code, "gender": gender, "label": label}


LANGUAGES: dict[str, dict[str, Any]] = {
    "en": {
        "label": "English",
        "voices": [
            _v("en-US-JennyNeural", "en-US", "female", "Female - US English (Jenny)"),
            _v("en-US-AriaNeural", "en-US", "female", "Female - US English (Aria)"),
            _v("en-US-GuyNeural", "en-US", "male", "Male - US English (Guy)"),
            _v("en-US-ChristopherNeural", "en-US", "male", "Male - US English (Christopher)"),
        ],
    },
    "hi": {
        "label": "Hindi",
        "voices": [
            _v("hi-IN-SwaraNeural", "hi-IN", "female", "Female - Hindi (Swara)"),
            _v("hi-IN-MadhurNeural", "hi-IN", "male", "Male - Hindi (Madhur)"),
        ],
    },
    "hinglish": {
        "label": "Hinglish",
        "voices": [
            _v("en-IN-NeerjaNeural", "en-IN", "female", "Female - Indian English (Neerja)"),
            _v("en-IN-PrabhatNeural", "en-IN", "male", "Male - Indian English (Prabhat)"),
        ],
    },
}


def voices_for_language(language: str) -> list[dict[str, str]]:
    """Return the voice list for a language key, or an empty list if unknown."""
    entry = LANGUAGES.get(language)
    return entry["voices"] if entry else []


def find_voice(voice_name: str) -> dict[str, str] | None:
    """Look up a voice definition by its voice name."""
    for entry in LANGUAGES.values():
        for voice in entry["voices"]:
            if voice["name"] == voice_name:
                return voice
    return None


# --- Runtime configuration ----------------------------------------------------
@dataclass(frozen=True)
class Config:
    telegram_token: str
    db_path: str
    log_level: str
    log_file: str
    max_text_length: int
    default_language: str
    default_voice: str
    default_speed: float
    tts_max_retries: int
    tts_timeout: float


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be a number.") from exc


def load_config() -> Config:
    """Read and validate configuration. Raises RuntimeError on invalid setup."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Missing required environment variable: TELEGRAM_BOT_TOKEN. "
            "Copy .env.example to .env and fill it in."
        )

    default_language = os.getenv("DEFAULT_LANGUAGE", "en").strip().lower()
    if default_language not in LANGUAGES:
        raise RuntimeError(f"DEFAULT_LANGUAGE must be one of: {', '.join(LANGUAGES)}")

    default_voice = os.getenv("DEFAULT_VOICE", "").strip() or voices_for_language(default_language)[0]["name"]
    if find_voice(default_voice) is None:
        raise RuntimeError(f"DEFAULT_VOICE '{default_voice}' is not in the voice catalogue.")

    default_speed = _float_env("DEFAULT_SPEED", 1.0)
    if not MIN_SPEED <= default_speed <= MAX_SPEED:
        raise RuntimeError(f"DEFAULT_SPEED must be between {MIN_SPEED} and {MAX_SPEED}.")

    return Config(
        telegram_token=token,
        db_path=os.getenv("DB_PATH", "voicebot.db").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_file=os.getenv("LOG_FILE", "logs/voicebot.log").strip(),
        max_text_length=_int_env("MAX_TEXT_LENGTH", 3000),
        default_language=default_language,
        default_voice=default_voice,
        default_speed=default_speed,
        tts_max_retries=_int_env("TTS_MAX_RETRIES", 3),
        tts_timeout=_float_env("TTS_TIMEOUT", 30.0),
    )
