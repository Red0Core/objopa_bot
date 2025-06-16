import os
from datetime import datetime

from curl_cffi.requests import AsyncSession

from core.config import STORAGE_PATH
from core.logger import logger

LAST_ACTIVITY_FILE = STORAGE_PATH / "last_activity_id.txt"


def save_last_activity_id(activity_id):
    with open(LAST_ACTIVITY_FILE, "w") as file:
        file.write(str(activity_id))


def get_last_activity_id():
    if os.path.exists(LAST_ACTIVITY_FILE):
        with open(LAST_ACTIVITY_FILE, "r") as file:
            return int(file.read().strip())
    return 0


# Запрос через curl_cffi на мекс
async def get_mexc_token_airdrop():
    headers = {
        "accept": "*/*",
        "accept-language": "ru-RU,ru;q=0.5",
        "language": "ru-RU",
        "pragma": "akamai-x-cache-on",
        "priority": "u=1, i",
        "referer": "https://www.mexc.com/ru-RU/mx-activity/deposit-gain-coins/events?utm_source=mexc&utm_medium=webmenu&utm_campaign=airdroptoken",
        "sec-ch-ua": '"Brave";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-gpc": "1",
        "sentry-trace": "",
        "trochilus-trace-id": "",
        "trochilus-uid": "",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }

    async with AsyncSession() as s:
        response = await s.get(
            "https://www.mexc.com/api/operateactivity/eftd/list",
            headers=headers,
            impersonate="chrome",
        )  # type: ignore

    return response


async def get_new_activities():
    response = await get_mexc_token_airdrop()

    if response.ok:
        logger.info("Успешный ответ от MEXC API")

        last_activity = get_last_activity_id()
        data = response.json()["data"]
        for activity in data:
            activity_id = activity.get("id")

            if activity_id > last_activity:
                activity_name = activity.get("activityName", "N/A")
                introduction = activity.get("introduction", "Разделите..")
                start_time = datetime.fromtimestamp(activity.get("startTime", 0) / 1000)
                end_time = datetime.fromtimestamp(activity.get("endTime", 0) / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                apply_num = activity.get("applyNum", "N/A")
                detail_url = f"https://www.mexc.com/ru-RU/mx-activity/deposit-gain-coins/detail/{activity_id}?utm_source=mexc&utm_medium=airdroptokenhomepage&utm_campaign=airdroptoken"

                save_last_activity_id(activity_id)

                message = (
                    f"Новая акция: {activity_name}\n{introduction}\n"
                    f"Начало: {start_time}\n"
                    f"Конец: {end_time}\n"
                    f"Количество участников: {apply_num}\n"
                    f"Ссылка: {detail_url}"
                )

                return message

    else:
        logger.exception(f"Ошибка при запросе к MEXC API {response.status_code}")
        return f"Ошибка при выполнении запроса: {response.status_code}"
