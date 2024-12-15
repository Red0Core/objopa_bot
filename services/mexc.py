from curl_cffi.requests import AsyncSession
from datetime import datetime
import json
from config import JSON_FILE

# Запрос через curl_cffi на мекс
async def get_mexc_token_airdrop():
    headers = {
        'accept': '*/*',
        'accept-language': 'ru-RU,ru;q=0.5',
        'language': 'ru-RU',
        'pragma': 'akamai-x-cache-on',
        'priority': 'u=1, i',
        'referer': 'https://www.mexc.com/ru-RU/mx-activity/deposit-gain-coins/events?utm_source=mexc&utm_medium=webmenu&utm_campaign=airdroptoken',
        'sec-ch-ua': '"Brave";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'sec-gpc': '1',
        'sentry-trace': '',
        'trochilus-trace-id': '',
        'trochilus-uid': '',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }

    async with AsyncSession() as s:
        response = await s.get('https://www.mexc.com/api/operateactivity/eftd/list', headers=headers, impersonate='chrome')
    
    return response

async def get_new_activities():
    response = await get_mexc_token_airdrop()

    if response.ok:

        # Инициализация JSON-файла, если он не существует
        try: 
            with open(JSON_FILE, 'r') as file: 
                activities_data = json.load(file) 
        except FileNotFoundError:
            activities_data = {'activities': []} 
            with open(JSON_FILE, 'w') as file: 
                json.dump(activities_data, file)

        data = response.json()['data']
        new_activities = []
        for activity in data:
            activity_id = activity.get('id')

            if activity_id not in [a['activity_id'] for a in activities_data['activities']]:
                activity_name = activity.get('activityName', 'N/A')
                introduction = activity.get('introduction', 'Разделите..')
                start_time = datetime.fromtimestamp(activity.get('startTime', 0) / 1000)
                end_time = datetime.fromtimestamp(activity.get('endTime', 0) / 1000).strftime('%Y-%m-%d %H:%M:%S')
                apply_num = activity.get('applyNum', 'N/A')
                detail_url = f'https://www.mexc.com/ru-RU/mx-activity/deposit-gain-coins/detail/{activity_id}?utm_source=mexc&utm_medium=airdroptokenhomepage&utm_campaign=airdroptoken'
                
                new_activities.append({'activity_id': activity_id})

                message = (f"Новая акция: {activity_name}\n{introduction}\n"
                           f"Начало: {start_time}\n"
                           f"Конец: {end_time}\n"
                           f"Количество участников: {apply_num}\n"
                           f"Ссылка: {detail_url}")
                
                # Обновляем JSON-файл новыми акциями 
                activities_data['activities'].extend(new_activities) 
                with open(JSON_FILE, 'w') as file:
                    json.dump(activities_data, file)

                return message

    else:
        return f"Ошибка при выполнении запроса: {response.status_code}"
