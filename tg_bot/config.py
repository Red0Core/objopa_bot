from dotenv import load_dotenv
import os
from pathlib import Path

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

TOKEN_BOT = get_required_env("TOKEN_BOT")
OBZHORA_CHAT_ID = get_required_env("OBZHORA_CHAT_ID") # Используется в личных целях
ZA_IDEU_CHAT_ID = get_required_env("ZA_IDEU_CHAT_ID") # Используется в личных целях
MAIN_ACC = get_required_env("MAIN_ACC") # Используется для проверки запуска бота
MEXC_JSON_FILE = Path('storage') / 'mexc_activities.json'

GIFS_ID = {'Салам дай брад': 'CgACAgIAAxkBAAMLZ14AAaOekJCeA-Nct3-QfFBf2YTsAAKjPgACMqoRShjIMCPEyv2zNgQ',
           'Бойкот работе': 'CgACAgIAAxkBAAMJZ14AAZPMu0dPrqoC2yWaZIb1NiAUAAKyRgACHU9oS5MtGXV2LB3RNgQ',
           'не жили богато': 'CgACAgIAAxkBAAMHZ14AAZGCJ9GnF7dvbGyiAcqAcNL6AALoQQACRF4ISnGgBsDr4eyeNgQ',
          }

OPENROUTER_API_KEY = get_required_env("OPENROUTER_API_KEY")
CHATGPT_API_KEY = get_required_env("CHATGPT_API_KEY")
GEMINI_API_KEY = get_required_env("GEMINI_API_KEY")
WOLFRAMALPHA_TOKEN = get_required_env("WOLFRAMALPHA_TOKEN")
