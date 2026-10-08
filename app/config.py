"""Конфигурация из переменных окружения (.env)."""
import os
from pathlib import Path

# Простая загрузка .env без зависимостей
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")   # https://ваш-домен (для кнопки Mini App)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "5"))
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # чат премодерации (опционально)

# --- Агент-контентщик канала ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")        # @joydao — куда публиковать
POST_HOUR = int(os.environ.get("POST_HOUR", "8"))    # час поста по времени сервера (UTC)
MAILING_HOUR = int(os.environ.get("MAILING_HOUR", "6"))  # час рассылки «энергия дня» (UTC, 6 = 9 МСК)
BOT_USERNAME = os.environ.get("BOT_USERNAME", "JOYDAO_bot")
COMMUNITY_CHAT_URL = os.environ.get("COMMUNITY_CHAT_URL", "https://t.me/joydao_club")  # общий чат сообщества

if not BOT_TOKEN:
    raise RuntimeError("Укажите BOT_TOKEN в файле .env (см. .env.example)")
