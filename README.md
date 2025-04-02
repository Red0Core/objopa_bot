
# Objopa Bot

![Python Version](https://img.shields.io/badge/python-3.13.2-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Objopa Bot — это Telegram-бот, разработанный для развлечения и выполнения различных команд. Проект включает в себя как самого бота, так и backend на FastAPI.

## Содержание

- [Структура проекта](#структура-проекта)
- [Начало работы](#начало-работы)
  - [Предварительные требования](#предварительные-требования)
  - [Установка](#установка)
- [Использование](#использование)
  - [Запуск бота](#запуск-бота)
  - [Запуск API](#запуск-api)
- [Docker](#docker)
- [Instaloader и prepare.sh](#instaloader-и-prepare.sh)
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

## Начало работы

### Предварительные требования

- Python 3.13.2
- [uv](https://github.com/astral-sh/uv)
- [tmux](https://github.com/tmux/tmux)
- [Make](https://www.gnu.org/software/make/)
- [Redis](https://github.com/redis/redis)

### Установка

```bash
git clone https://github.com/Red0Core/objopa_bot.git
cd objopa_bot
make install
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

## Instaloader и prepare.sh

Если вы используете `instaloader`, убедитесь, что логин происходит корректно. Для этого создан вспомогательный скрипт `prepare_instaloader_session.sh`, который:

- Авторизует через `instaloader.load_session_file(INSTAGRAM_USERNAME)`
- Копирует его в нужную директорию

```bash
bash prepare.sh
```

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
