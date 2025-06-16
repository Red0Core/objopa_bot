
# Objopa Bot

![Python Version](https://img.shields.io/badge/python-3.13.5-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Objopa Bot — Telegram‑бот, работающий вместе с FastAPI‑бэкендом и системой воркеров. Все сервисы развёрнуты как отдельные микросервисы и общаются через Redis. Бот служит главным пользовательским интерфейсом и предоставляет разные команды: от мини‑игр и трекера привычек до интеграций с GPT‑моделями и загрузки медиа из соцсетей.

Основные сервисы:

- **backend** – API на FastAPI, связующее звено между ботом и воркерами;
- **tg_bot** – сам Telegram‑бот;
- **workers** – воркерные процессы, выполняющие тяжёлые задачи.

## Содержание

- [Структура проекта](#структура-проекта)
- [Начало работы](#начало-работы)
  - [Предварительные требования](#предварительные-требования)
  - [Установка](#установка)
- [Использование](#использование)
  - [Запуск бота](#запуск-бота)
  - [Запуск API](#запуск-api)
- [Docker](#docker)
- [Instaloader и prepare_instaloader_session.sh](#instaloader-и-prepare_instaloader_sessionsh)
- [Деплой](#деплой)
- [Лицензия](#лицензия)
- [Контакты](#контакты)

## Структура проекта

```
objopa_bot/
├── backend/
├── tg_bot/
├── docker-compose.yml
├── .env.example
├── Makefile
└── README.md
```

## Возможности

- Интеграция с GPT (Gemini 2.0) и генерация ответов
- Просмотр курсов валют и цен криптовалют
- Скачивание медиа из Instagram, TikTok и Twitter через команду `/d`
- Мини‑игры, гороскоп и счётчики достижений
- Воркеры для тяжёлых задач, управляемые через бэкенд

## Начало работы

### Предварительные требования

- Python 3.13.5
- [uv](https://github.com/astral-sh/uv)
- [tmux](https://github.com/tmux/tmux)
- [Make](https://www.gnu.org/software/make/)
- [Redis](https://github.com/redis/redis)

### Установка

```bash
git clone https://github.com/Red0Core/objopa_bot.git
cd objopa_bot
make install

# скопируйте `.env.example` и заполните необходимые переменные
cp .env.example .env
```

## Использование

### Запуск бота

```bash
make run-bot
```

### Запуск API

```bash
make run-api
```

### Просмотр логов

```bash
make logs-bot
make logs-api
```

### Остановка

```bash
make stop-bot
make stop-api
```

## Docker

Также проект поддерживает запуск через Docker Compose. Это удобно для быстрого деплоя:

```bash
docker-compose up -d --build
```

Сервисы:
- `redis` — брокер сообщений
- `tg-bot` — Telegram-бот
- `backend` — FastAPI REST API

## Instaloader и prepare_instaloader_session.sh

Если вы используете `instaloader`, убедитесь, что логин происходит корректно. Для этого создан вспомогательный скрипт `prepare_instaloader_session.sh`, который:

- Авторизует через `instaloader.load_session_file(INSTAGRAM_USERNAME)`
- Копирует его в нужную директорию

```bash
bash prepare_instaloader_session.sh
```

## Twitter авторизация

Для скачивания приватных материалов бот может войти в Twitter самостоятельно.
Добавьте `TWITTER_USERNAME` и `TWITTER_PASSWORD` в `.env`. При первом запросе
бот выполнит вход и сохранит полученные куки `auth_token` и `ct0`. При наличии
готовых токенов их можно задать через `TWITTER_AUTH_TOKEN` и `TWITTER_CT0`.
Чтобы обновлять куки без перезапуска, бекенд предоставляет эндпоинт
`POST /worker/set-twitter-cookies`. В запросе нужно передать заголовок
`Authorization: Bearer <TWITTER_COOKIES_TOKEN>` и JSON с полями
`auth_token` и `ct0`.

## Команда `/d`

Команда позволяет скачивать медиа по ссылке. Сначала пробуется `yt-dlp`, а если
он не находит файлы, используется `gallery-dl`. Для ссылок на Twitter подключают
ся авторизационные куки. Файлы стараются скачиваться в 1080p, но если ожидаемый
размер превышает ~2 ГБ, берётся версия пониже (до 720p). Папка `downloads`
очищается ежедневно, чтобы не захламлять диск.

## Деплой

```bash
git pull
make restart-bot
make restart-api
```

## Лицензия

Проект лицензирован под MIT.

## Контакты

- Telegram: [@Red0Core](https://t.me/Red0Core)
