from dotenv import load_dotenv
import os
from pathlib import Path

# Загружаем .env только если переменные ещё не определены (для докера)
if not os.getenv("TG_TOKEN"):
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

load_dotenv()

TOKEN_BOT = os.getenv("TOKEN_BOT")
OBZHORA_CHAT_ID = os.getenv("OBZHORA_CHAT_ID") # Используется в личных целях
ZA_IDEU_CHAT_ID = os.getenv("ZA_IDEU_CHAT_ID") # Используется в личных целях
MAIN_ACC = os.getenv("MAIN_ACC") # Используется для проверки запуска бота
MEXC_JSON_FILE = Path('storage') / 'mexc_activities.json'

GIFS_ID = {'Салам дай брад': 'CgACAgIAAxkBAAMLZ14AAaOekJCeA-Nct3-QfFBf2YTsAAKjPgACMqoRShjIMCPEyv2zNgQ',
           'Бойкот работе': 'CgACAgIAAxkBAAMJZ14AAZPMu0dPrqoC2yWaZIb1NiAUAAKyRgACHU9oS5MtGXV2LB3RNgQ',
           'не жили богато': 'CgACAgIAAxkBAAMHZ14AAZGCJ9GnF7dvbGyiAcqAcNL6AALoQQACRF4ISnGgBsDr4eyeNgQ',
          }

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")
CHATGPT_API_KEY = os.getenv("CHATGPT_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WOLFRAMALPHA_TOKEN = os.getenv("WOLFRAMALPHA_TOKEN")
