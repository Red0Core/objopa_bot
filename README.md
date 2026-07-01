# Objopa Bot 🤖

![Python Version](https://img.shields.io/badge/python-3.13.5-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Redis](https://img.shields.io/badge/redis-supported-red)

Многофункциональный Telegram-бот с микросервисной архитектурой. Поддерживает скачивание медиа, интеграции с AI, мини-игры и многое другое.

## 🏗️ Архитектура

- **🤖 tg_bot** – Telegram-бот с командами и роутерами
- **🔧 backend** – FastAPI сервер для API и интеграций  
- **⚙️ workers** – Фоновые задачи и обработка
- **🔴 Redis** – Кэширование и обмен данными между сервисами

## 📥 Система скачивания медиа

### Команды:
- `/d <url>` - Универсальная команда скачивания
- `/insta <url>` - Специально для Instagram
- `/d_test <url>` - Диагностика системы

### Управление Instagram:
- `/ua_current` - статус User-Agent
- `/ua_set <UA>` - установить новый User-Agent  
- `/ua_reset` - сбросить на дефолтный
- `/insta_session` - статус системы
- `/insta_reset` - сброс кэша

### Особенности:
✅ **Приоритетная система**: Кастомные скачиватели → yt-dlp → gallery-dl  
✅ **Динамический User-Agent**: Изменение без перезапуска через Redis  
✅ **Строгий режим**: Instagram/Twitter не пробуют fallback методы  
✅ **Нет дублирования**: Каждый URL обрабатывается один раз  
✅ **Автодиагностика**: Детальные отчеты о работе системы  

## 🚀 Быстрый старт

### Требования:
- Python 3.13.5
- [uv](https://github.com/astral-sh/uv)
- [Redis](https://redis.io/)
- [Make](https://www.gnu.org/software/make/)

### Установка:
```bash
git clone https://github.com/Red0Core/objopa_bot.git
cd objopa_bot
make install

# Настройка окружения
cp .env.example .env
# Заполните .env файл необходимыми токенами
```

### Запуск:
```bash
# Запуск бота
make run-bot

# Запуск API (в другом терминале)
make run-api
```

## 📝 Структура проекта

```
objopa_ecosystem/
├── tg_bot/           # Telegram бот
│   ├── routers/      # Команды и обработчики
│   ├── downloaders/  # Система скачивания
│   └── services/     # Сервисы (UA, GPT и др.)
├── backend/          # FastAPI сервер
├── workers/          # Фоновые задачи
├── core/            # Общие компоненты
│   ├── config.py    # Конфигурация
│   ├── logger.py    # Логирование
│   └── redis_client.py # Redis подключение
└── docs/            # Документация
```

## 🔧 Конфигурация

### Обязательные переменные в `.env`:
```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token

# Redis
REDIS_URL=redis://localhost:6379

# Instagram (опционально для продвинутых функций)
INSTAGRAM_USERNAME=your_username

# Twitter (опционально)
TWITTER_AUTH_TOKEN=your_token
TWITTER_CT0=your_csrf_token
```

## 📖 Использование

### Скачивание медиа:
```bash
# Instagram
/d https://instagram.com/p/ABC123/

# YouTube
/d https://youtube.com/watch?v=ABC123

# Twitter/X
/d https://x.com/user/status/123456
```

### Управление Instagram User-Agent:
```bash
# Проверить статус
/ua_current

# Установить новый UA (при блокировках)
/ua_set Mozilla/5.0 (Linux; Android 14...)

# Сбросить на дефолтный
/ua_reset
```

### Диагностика:
```bash
# Тест системы скачивания
/d_test https://instagram.com/p/ABC123/

# Статус Instagram системы  
/insta_session

# Общий статус компонентов
/d_status
```

## 🐳 Docker

```bash
# Сборка и запуск
docker-compose up -d

# Только бот
docker-compose up tg_bot

# Логи
docker-compose logs -f
```

## 🔄 Управление

### Обновление:
```bash
git pull
make restart-bot
make restart-api
```

### Мониторинг:
```bash
# Логи бота
tail -f logs/bot.log

# Статус Redis
redis-cli ping
```

## 📚 Документация

- [📥 Система скачивания](docs/DOWNLOAD_SYSTEM.md) - Подробное описание системы скачивания
- [🔧 Разработка](docs/DEVELOPMENT.md) - Руководство для разработчиков

## 🤝 Участие в разработке

1. Fork репозитория
2. Создайте feature branch (`git checkout -b feature/amazing-feature`)
3. Commit изменения (`git commit -m 'Add amazing feature'`)
4. Push в branch (`git push origin feature/amazing-feature`)
5. Откройте Pull Request

## 📋 TODO

- [ ] Добавить поддержку TikTok
- [ ] Улучшить систему кэширования
- [ ] Добавить метрики производительности
- [ ] Веб-интерфейс для управления

## 📄 Лицензия

Этот проект лицензирован под MIT License - см. [LICENSE](LICENSE) файл.

## 📞 Контакты

- Telegram: [@Red0Core](https://t.me/Red0Core)
- GitHub: [Red0Core](https://github.com/Red0Core)

---

⭐ **Поставьте звезду, если проект был полезен!**

## 🧩 Новый загрузчик Instagram Reels

Мы добавили новый улучшенный модуль для загрузки Instagram Reels (`tg_bot/downloaders/ig_reel_downloader.py`).
Этот модуль работает по гибридной стратегии (сначала пытается получить видео без кукисов, а если это не удается — прибегает к помощи Netscape-кукисов).

### Как его использовать:

Модуль можно использовать отдельно в ваших скриптах:
```python
import asyncio
from tg_bot.downloaders.ig_reel_downloader import download_reel

async def main():
    result = await download_reel("https://www.instagram.com/reel/DaFeGEKM9In/", cookies_path="cookies.txt")
    if result.success:
        print("Success:", result.files[0])
    else:
        print("Error:", result.error)

asyncio.run(main())
```

### Формат cookies.txt

Если инстаграм заблокирует доступ к странице без авторизации, скрипт попытается прочитать Netscape-совместимый файл `cookies.txt` в случае если он был передан через аргумент `cookies_path`.

Пример формата Netscape:
```
# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file! Do not edit.

.instagram.com	TRUE	/	TRUE	1798333134	csrftoken	pwORmmAjf8kD...
.instagram.com	TRUE	/	TRUE	1790557134	ds_user_id	11845255...
.instagram.com	TRUE	/	TRUE	1798333068	sessionid	11845255...
```
