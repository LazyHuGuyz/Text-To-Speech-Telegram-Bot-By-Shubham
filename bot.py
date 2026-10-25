"""
VoiceBot AI - Telegram bot entry point.

Commands: /start /help /settings /voices /language /speed /about
Any non-command text message is converted to a Telegram voice note.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from contextlib import suppress
from io import BytesIO

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    LANGUAGES,
    MAX_SPEED,
    MIN_SPEED,
    SPEED_PRESETS,
    Config,
    find_voice,
    load_config,
    voices_for_language,
)
from database import Database, UserSettings
from tts import TTSError, TTSService
from utils import chat_action, parse_speed, sanitize_text, setup_logging

log = logging.getLogger("voicebot")

HTML = ParseMode.HTML

HELP_TEXT = (
    "<b>How to use VoiceBot AI</b>\n\n"
    "Just send any text and I will reply with a voice note.\n\n"
    "<b>Commands</b>\n"
    "/start - welcome message\n"
    "/help - this help\n"
    "/settings - show your current preferences\n"
    "/voices - choose a voice\n"
    "/language - English, Hindi or Hinglish\n"
    "/speed - change speaking speed (e.g. <code>/speed 1.25</code>)\n"
    "/about - about this bot\n\n"
    "<b>Tips</b>\n"
    "- Use punctuation (. ! ?) for natural pauses.\n"
    "- Long messages are split automatically into one voice note."
)

ABOUT_TEXT = (
    "<b>VoiceBot AI</b>\n"
    "Text-to-speech for Telegram, powered by Microsoft Edge neural voices "
    "(via edge-tts).\n\n"
    "Languages: English, Hindi, Hinglish.\n"
    "Built with python-telegram-bot."
)


# --- Helpers ------------------------------------------------------------------
def _cfg(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["cfg"]


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _tts(context: ContextTypes.DEFAULT_TYPE) -> TTSService:
    return context.application.bot_data["tts"]


async def _ensure_user(update: Update, db: Database) -> None:
    user = update.effective_user
    if user is not None:
        await db.upsert_user(user.id, user.username, user.first_name)


def _voice_label(voice_name: str) -> str:
    voice = find_voice(voice_name)
    return voice["label"] if voice else voice_name


def _settings_text(prefs: UserSettings) -> str:
    return (
        "<b>Your settings</b>\n"
        f"Language: {LANGUAGES[prefs.language]['label']}\n"
        f"Voice: {_voice_label(prefs.voice)}\n"
        f"Speed: {prefs.speed:.2f}x"
    )


def _user_lock(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> asyncio.Lock:
    """One lock per user so a single user cannot flood the TTS service."""
    locks: dict[int, asyncio.Lock] = context.application.bot_data.setdefault("locks", {})
    if user_id not in locks:
        locks[user_id] = asyncio.Lock()
    return locks[user_id]


# --- Command handlers ---------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    await _ensure_user(update, db)
    if update.effective_user:
        await db.add_log(update.effective_user.id, "start")
    await update.effective_message.reply_text(
        "<b>Welcome to VoiceBot AI</b>\n\n"
        "Send me any text and I will turn it into a natural voice note.\n"
        "Use /help to see all commands.",
        parse_mode=HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=HTML)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(ABOUT_TEXT, parse_mode=HTML)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    await _ensure_user(update, db)
    prefs = await db.get_settings(update.effective_user.id)
    await update.effective_message.reply_text(_settings_text(prefs), parse_mode=HTML)


async def cmd_voices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    await _ensure_user(update, db)
    prefs = await db.get_settings(update.effective_user.id)
    voices = voices_for_language(prefs.language)

    keyboard = [
        [InlineKeyboardButton(
            ("✓ " if v["name"] == prefs.voice else "") + v["label"],
            callback_data=f"voice:{v['name']}",
        )]
        for v in voices
    ]
    await update.effective_message.reply_text(
        f"<b>Choose a voice</b> ({LANGUAGES[prefs.language]['label']}):",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    await _ensure_user(update, db)
    prefs = await db.get_settings(update.effective_user.id)

    keyboard = [
        [InlineKeyboardButton(
            ("✓ " if key == prefs.language else "") + entry["label"],
            callback_data=f"lang:{key}",
        )]
        for key, entry in LANGUAGES.items()
    ]
    await update.effective_message.reply_text(
        "<b>Choose a language</b>:",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_speed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    await _ensure_user(update, db)
    user_id = update.effective_user.id
    message = update.effective_message

    # Direct form: /speed 1.25
    if context.args:
        speed = parse_speed(context.args[0])
        if speed is None:
            await message.reply_text(
                f"Please send a number between {MIN_SPEED} and {MAX_SPEED}, e.g. /speed 1.25"
            )
            return
        await db.update_setting(user_id, "speed", speed)
        await db.add_log(user_id, "set_speed", f"{speed}")
        await message.reply_text(f"Speaking speed set to {speed:.2f}x.")
        return

    # Button form: /speed
    prefs = await db.get_settings(user_id)
    keyboard = [
        [InlineKeyboardButton(
            ("✓ " if abs(preset - prefs.speed) < 1e-6 else "") + f"{preset:g}x",
            callback_data=f"speed:{preset}",
        ) for preset in SPEED_PRESETS]
    ]
    await message.reply_text(
        "<b>Choose speaking speed</b> (or send <code>/speed 1.1</code>):",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# --- Callback (inline button) handler -----------------------------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    db = _db(context)
    await _ensure_user(update, db)
    user_id = update.effective_user.id

    action, _, value = (query.data or "").partition(":")
    prefs = await db.get_settings(user_id)

    try:
        if action == "voice":
            voice = find_voice(value)
            if voice is None or voice not in voices_for_language(prefs.language):
                await query.answer("That voice is not available.", show_alert=True)
                return
            await db.update_setting(user_id, "voice", value)
            await db.add_log(user_id, "set_voice", value)
            text = f"Voice set to <b>{voice['label']}</b>."

        elif action == "lang":
            if value not in LANGUAGES:
                await query.answer("Unknown language.", show_alert=True)
                return
            await db.update_setting(user_id, "language", value)
            # Switch to a voice that belongs to the new language if needed.
            current_voice = find_voice(prefs.voice)
            if current_voice is None or current_voice not in voices_for_language(value):
                new_voice = voices_for_language(value)[0]["name"]
                await db.update_setting(user_id, "voice", new_voice)
            await db.add_log(user_id, "set_language", value)
            prefs = await db.get_settings(user_id)
            text = f"Language set to <b>{LANGUAGES[value]['label']}</b>.\n\n" + _settings_text(prefs)

        elif action == "speed":
            speed = parse_speed(value)
            if speed is None:
                await query.answer("Invalid speed.", show_alert=True)
                return
            await db.update_setting(user_id, "speed", speed)
            await db.add_log(user_id, "set_speed", f"{speed}")
            text = f"Speaking speed set to <b>{speed:.2f}x</b>."

        else:
            return

        with suppress(BadRequest):  # "message is not modified" when re-tapping the same option
            await query.edit_message_text(text, parse_mode=HTML)
    except Exception:
        log.exception("Failed to handle callback action=%s", action)
        await query.answer("Something went wrong. Please try again.", show_alert=True)


# --- Text message handler -----------------------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return

    text = sanitize_text(message.text or "")
    if not text:
        log.debug("Ignored empty message")
        return

    cfg = _cfg(context)
    db = _db(context)
    tts = _tts(context)

    if len(text) > cfg.max_text_length:
        await message.reply_text(
            f"That message is {len(text)} characters long. "
            f"The limit is {cfg.max_text_length} characters. Please shorten it."
        )
        return

    await db.upsert_user(user.id, user.username, user.first_name)

    lock = _user_lock(context, user.id)
    if lock.locked():
        await message.reply_text("I'm still working on your previous message. Please wait a moment.")
        return

    async with lock:
        prefs = await db.get_settings(user.id)
        try:
            async with chat_action(context.bot, chat.id, ChatAction.RECORD_VOICE):
                ogg_bytes = await tts.synthesize(text, prefs.voice, prefs.speed)

            await message.reply_voice(
                voice=InputFile(BytesIO(ogg_bytes), filename="speech.ogg"),
            )
            await db.add_log(user.id, "tts_ok", f"chars={len(text)} voice={prefs.voice}")
            log.info("Delivered voice note: chars=%d voice=%s", len(text), prefs.voice)

        except TTSError as exc:
            log.warning("TTS failed for user %s: %s", user.id, exc)
            await db.add_log(user.id, "tts_error", str(exc))
            await message.reply_text(str(exc))

        except Exception:
            log.exception("Unexpected error while handling message for user %s", user.id)
            await db.add_log(user.id, "unexpected_error")
            await message.reply_text("Something went wrong while creating your voice note. Please try again.")


# --- Global error handler -----------------------------------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception in update handler", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        with suppress(Exception):
            await update.effective_message.reply_text(
                "An unexpected error occurred. Please try again later."
            )


# --- Application lifecycle ----------------------------------------------------
def build_application(cfg: Config) -> Application:
    db = Database(
        cfg.db_path,
        UserSettings(voice=cfg.default_voice, language=cfg.default_language, speed=cfg.default_speed),
    )
    # Change 2: edge-tts needs no API key, so the key argument is gone.
    tts = TTSService(timeout=cfg.tts_timeout, max_retries=cfg.tts_max_retries)

    async def post_init(app: Application) -> None:
        await db.connect()
        log.info("VoiceBot AI started")

    async def post_shutdown(app: Application) -> None:
        await tts.close()
        await db.close()
        log.info("VoiceBot AI stopped")

    app = (
        Application.builder()
        .token(cfg.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data.update(cfg=cfg, db=db, tts=tts, locks={})

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("voices", cmd_voices))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("speed", cmd_speed))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    if sys.version_info < (3, 12):
        sys.exit("VoiceBot AI requires Python 3.12 or newer.")

    try:
        cfg = load_config()
    except RuntimeError as exc:
        sys.exit(f"Configuration error: {exc}")

    # Change 1: only the Telegram token is a secret now.
    setup_logging(cfg.log_level, cfg.log_file, secrets=[cfg.telegram_token])

    # ffmpeg check is still needed: it encodes the audio to OGG/Opus.
    if shutil.which("ffmpeg") is None:
        log.critical("ffmpeg was not found on PATH. Install it (see README) and restart.")
        sys.exit(1)

    app = build_application(cfg)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
