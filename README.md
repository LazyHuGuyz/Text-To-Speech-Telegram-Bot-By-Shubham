# VoiceBot AI

A Telegram bot that turns any text message into a natural-sounding voice note.

- Python 3.12+, python-telegram-bot v22+, fully async
- Google Cloud Text-to-Speech (Neural2 voices) as the TTS provider
- English, Hindi, and Hinglish (Indian English voices)
- Male and female voices, adjustable speed, natural pauses between sentences
- Per-user settings stored in SQLite
- Automatic splitting of long messages
- Retries for transient API failures, graceful error messages
- Runs on Linux and Android (Termux)

## Project structure

```
VoiceBotAI/
├── bot.py           # Telegram handlers and application entry point
├── config.py        # Environment config and voice/language catalogue
├── tts.py           # Google TTS client, SSML, chunking, OGG encoding
├── database.py      # SQLite schema and queries (users, settings, logs)
├── utils.py         # Text sanitation, speed parsing, logging, typing indicator
├── requirements.txt
├── .env.example
└── README.md
```

## Commands

| Command | Action |
|---|---|
| `/start` | Welcome message |
| `/help` | Usage help |
| `/settings` | Show language, voice and speed |
| `/voices` | Pick a voice for the current language (buttons) |
| `/language` | Switch between English, Hindi and Hinglish |
| `/speed` | Pick a speed with buttons, or `/speed 1.25` directly |
| `/about` | About the bot |
| *any text* | Replies with a voice note |

---

## 1. Get a Google Cloud Text-to-Speech API key

1. Go to <https://console.cloud.google.com> and sign in.
2. Create a project (top bar, **New Project**), or select an existing one.
3. Enable billing for the project. Google requires a billing account to use the API. Neural2 voices have a free monthly quota, and you pay only beyond it. Check current pricing on Google's site.
4. Enable the API: go to **APIs & Services → Library**, search for **Cloud Text-to-Speech API**, and click **Enable**.
5. Create a key: go to **APIs & Services → Credentials → Create credentials → API key**. Copy the key.
6. Restrict the key (strongly recommended): click the key, set **API restrictions** to *Restrict key* and select *Cloud Text-to-Speech API*. Optionally restrict by IP address if your server has a fixed IP.

## 2. Create a Telegram bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot`, follow the prompts, and copy the token it gives you.

---

## 3. Install on Linux

Requires Python 3.12+ and ffmpeg.

```bash
# Debian/Ubuntu
sudo apt update
sudo apt install -y python3.12 python3.12-venv ffmpeg git

# Get the code
git clone <your-repo-url> VoiceBotAI
cd VoiceBotAI

# Virtual environment and dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Configure secrets
cp .env.example .env
nano .env        # paste TELEGRAM_BOT_TOKEN and GOOGLE_TTS_API_KEY

# Run
python bot.py
```

To run it as a service that starts on boot, create `/etc/systemd/system/voicebot.service`:

```ini
[Unit]
Description=VoiceBot AI Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/VoiceBotAI
ExecStart=/home/youruser/VoiceBotAI/.venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now voicebot
sudo journalctl -u voicebot -f
```

## 4. Install on Android (Termux)

Install Termux from F-Droid (the Play Store version is outdated).

```bash
# Update packages and install Python, ffmpeg and git
pkg update && pkg upgrade -y
pkg install -y python ffmpeg git

# Confirm Python is 3.12 or newer
python --version

# Get the code
git clone <your-repo-url> VoiceBotAI
cd VoiceBotAI

# Virtual environment and dependencies
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Configure secrets
cp .env.example .env
nano .env

# Keep the CPU awake and run the bot
termux-wake-lock
python bot.py
```

Notes for Termux:
- Run `termux-wake-lock` before starting so Android does not suspend the process. Remove the lock later with `termux-wake-unlock`.
- Disable battery optimization for Termux in Android settings so the bot keeps running in the background.
- To keep it running after you close the session, use `nohup python bot.py > bot.out 2>&1 &`, or run it inside `tmux`.
- If `pip install` fails building a package, run `pkg install -y clang rust` and retry.

---

## 5. Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | - | Token from BotFather |
| `GOOGLE_TTS_API_KEY` | yes | - | Google Cloud API key with Text-to-Speech enabled |
| `DB_PATH` | no | `voicebot.db` | SQLite database file |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FILE` | no | `logs/voicebot.log` | Rotating log file |
| `MAX_TEXT_LENGTH` | no | `3000` | Maximum characters per message |
| `DEFAULT_LANGUAGE` | no | `en` | `en`, `hi` or `hinglish` |
| `DEFAULT_VOICE` | no | first voice of language | Provider voice name |
| `DEFAULT_SPEED` | no | `1.0` | Between 0.5 and 2.0 |
| `TTS_MAX_RETRIES` | no | `3` | Attempts for transient provider errors |
| `TTS_TIMEOUT` | no | `30` | Seconds per provider request |

## 6. How it works

1. Text is sanitized (control characters removed, whitespace normalized). Empty messages are ignored.
2. Long text is split at sentence boundaries into chunks under Google's request limit.
3. Each chunk becomes SSML with escaped text and 350 ms breaks between sentences.
4. Chunks are synthesized in parallel as LINEAR16 WAV. Transient errors (network, 429, 5xx) retry with exponential backoff.
5. The WAV parts are joined with a short silence between chunks and encoded to OGG/Opus by ffmpeg.
6. The bot sends the result as a voice note. A "recording voice" indicator shows while processing.
7. Audio is held in memory and never written to disk, so there are no temporary files to delete.

## 7. Security notes

- Secrets are read only from `.env` and environment variables. `.env` is listed in `.gitignore`-style practice: do not commit it.
- The Google API key is sent in an HTTP header, not in the URL. Log output is passed through a redactor that masks the bot token and API key.
- User input is sanitized and length-limited. Settings updates go through an allowlist, so user input never builds SQL.
- The message content is not stored in the database. The `logs` table records only event names and metadata such as character counts.
- Provider errors are shown to users in generic form. Details go only to the log file.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `ffmpeg was not found on PATH` | Install ffmpeg (`apt install ffmpeg` or `pkg install ffmpeg`) |
| `Missing required environment variables` | Check that `.env` exists in the project folder and has both values |
| Bot replies "speech provider rejected this request" | Confirm the Cloud Text-to-Speech API is enabled, billing is on, and the key's API restriction includes it |
| Voice names fail with a 400 error | Check the names in `config.py` against Google's voice list; some voices are not available in every region |
| No reply at all | Check `logs/voicebot.log`. Verify the bot token and that only one instance is running |
| Hinglish sounds unnatural | Hinglish uses Indian English voices. Try different voices with `/voices` |

## 9. Expressive style

Google's Neural2 voices do not expose a style control, so the bot does not offer one. Pitch and rate are adjusted with `/speed`, and pauses are controlled through SSML. To add expressive styles, switch to a provider that supports them (for example, OpenAI's `gpt-4o-mini-tts` with an `instructions` field) and replace `_request_wav` in `tts.py`. The rest of the pipeline stays the same.

## 10. Extending

- Add voices or languages by editing `LANGUAGES` in `config.py`.
- Add a new TTS provider by implementing a class with the same `synthesize(text, voice_name, speed) -> bytes` method and changing the construction in `build_application`.
- Add an admin command by checking `update.effective_user.id` against a list in `.env`.
