import os
from dotenv import load_dotenv

load_dotenv()

def get_required_env(name: str) -> str:
    """Get an environment variable or raise an error if it's not set."""
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"{name} is not set in the environment variables.")
    return value

COINMARKETCAP_API_KEY = get_required_env("COINMARKETCAP_API_KEY")
ALPHAVANTAGE_API_KEY = get_required_env("ALPHAVANTAGE_API_KEY")
