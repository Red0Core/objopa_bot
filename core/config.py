import os
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env только если переменные ещё не определены (для докера)
if not os.getenv("TOKEN_BOT"):
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

load_dotenv()


def get_required_env(name: str) -> str:
    """Get an environment variable or raise an error if it's not set."""
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"{name} is not set in the environment variables.")
    return value


BACKEND_ROUTE = get_required_env("BACKEND_ROUTE")

BASE_DIR: Path = Path(__file__).resolve().parent.parent
# objopa_ecosystem/
STORAGE_DIR: Path = BASE_DIR / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = STORAGE_DIR / "image_generation_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_VIDEO_DIR = STORAGE_DIR / "video_generation_uploads"
UPLOAD_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
WORKER_ARCHIVES_DIR = UPLOAD_DIR / "worker_archives"
WORKER_ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_BOT = get_required_env("TOKEN_BOT")
OBZHORA_CHAT_ID = get_required_env("OBZHORA_CHAT_ID")  # Используется в личных целях
ZA_IDEU_CHAT_ID = get_required_env("ZA_IDEU_CHAT_ID")  # Используется в личных целях
MAIN_ACC = get_required_env("MAIN_ACC")  # Используется для проверки запуска бота
MEXC_JSON_FILE = STORAGE_DIR / "mexc_activities.json"

GIFS_ID = {
    "Салам дай брад": "CgACAgIAAxkBAAMLZ14AAaOekJCeA-Nct3-QfFBf2YTsAAKjPgACMqoRShjIMCPEyv2zNgQ",
    "Бойкот работе": "CgACAgIAAxkBAAMJZ14AAZPMu0dPrqoC2yWaZIb1NiAUAAKyRgACHU9oS5MtGXV2LB3RNgQ",
    "не жили богато": "CgACAgIAAxkBAAMHZ14AAZGCJ9GnF7dvbGyiAcqAcNL6AALoQQACRF4ISnGgBsDr4eyeNgQ",
}

OPENROUTER_API_KEY = get_required_env("OPENROUTER_API_KEY")
CHATGPT_API_KEY = get_required_env("CHATGPT_API_KEY")
GEMINI_API_KEY = get_required_env("GEMINI_API_KEY")
WOLFRAMALPHA_TOKEN = get_required_env("WOLFRAMALPHA_TOKEN")
ALPHAVANTAGE_API_KEY = get_required_env("ALPHAVANTAGE_API_KEY")
COINMARKETCAP_API_KEY = get_required_env("COINMARKETCAP_API_KEY")
TWITTER_COOKIES_TOKEN = get_required_env("TWITTER_COOKIES_TOKEN")
INSTAGRAM_USERNAME = get_required_env("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = get_required_env("REDIS_PASSWORD")

PASTEBIN_API_KEY = get_required_env("PASTEBIN_API_KEY")
REDIS_SSL = bool(os.getenv("REDIS_SSL", False))