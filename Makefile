# === Параметры ===
UV      ?= uv
VENV    ?= .venv
TMUX    ?= tmux

HOST    ?= 127.0.0.1
PORT    ?= 8888

BOT_SESSION := objopa-bot
API_SESSION := objopa-api

.PHONY: all init sync dev prod lock upgrade-lock run-bot run-api \
        restart-bot restart-api stop-bot stop-api logs-bot logs-api \
        shell clean reset help

# По умолчанию — установка зависимостей (dev)
all: init sync

## Создать проектное окружение .venv под Python 3.13 (один раз)
init:
	@echo "🐍 Creating project venv ($(VENV))..."
	$(UV) venv $(VENV) --python 3.13

## Синхронизация зависимостей для разработки (с dev-группами)
sync:
	@echo "📦 uv sync (dev)..."
	$(UV) sync

## Прод-синхронизация: строго по lock и без dev-зависимостей
prod:
	@echo "🚀 uv sync (prod: --frozen --no-dev)..."
	$(UV) sync --frozen --no-dev

## Обновить uv.lock в рамках диапазонов версий
upgrade-lock:
	@echo "🔒 uv lock --upgrade..."
	$(UV) lock --upgrade

## === Запуски через tmux (без активации venv; uv сам найдёт .venv) ===
run-bot:
	@echo "🤖 Running Telegram Bot in tmux: $(BOT_SESSION)"
	$(TMUX) new-session -d -s $(BOT_SESSION) '$(UV) run python -m tg_bot.main'

run-api:
	@echo "🚀 Running FastAPI in tmux: $(API_SESSION)"
	$(TMUX) new-session -d -s $(API_SESSION) '$(UV) run uvicorn backend.main:app --host $(HOST) --port $(PORT)'

restart-bot:
	@echo "♻️ Restarting Telegram Bot..."
	-$(TMUX) kill-session -t $(BOT_SESSION)
	$(MAKE) run-bot

restart-api:
	@echo "♻️ Restarting FastAPI..."
	-$(TMUX) kill-session -t $(API_SESSION)
	$(MAKE) run-api

stop-bot:
	@echo "⛔ Stopping Telegram Bot..."
	-$(TMUX) kill-session -t $(BOT_SESSION)

stop-api:
	@echo "⛔ Stopping FastAPI..."
	-$(TMUX) kill-session -t $(API_SESSION)

logs-bot:
	@echo "📜 Attaching to Telegram Bot logs..."
	$(TMUX) attach -t $(BOT_SESSION)

logs-api:
	@echo "📜 Attaching to FastAPI logs..."
	$(TMUX) attach -t $(API_SESSION)

## Зайти в shell с активированной .venv (по желанию)
shell:
	@echo "🐚 Shell in $(VENV)..."
	@/usr/bin/env bash -lc 'source $(VENV)/bin/activate && exec $$SHELL -l'

## Удалить .venv
clean:
	@echo "🧹 Removing $(VENV)..."
	rm -rf $(VENV)

## Полный ресет: снести .venv и пересобрать
reset: clean all

help:
	@echo "Targets:"
	@echo "  init           - создать .venv (Python 3.13)"
	@echo "  sync           - uv sync (dev)"
	@echo "  prod           - uv sync --frozen --no-dev"
	@echo "  upgrade-lock   - обновить uv.lock в рамках диапазонов"
	@echo "  run-bot        - запустить tg_bot в tmux"
	@echo "  run-api        - запустить uvicorn backend в tmux"
	@echo "  restart-*, stop-*, logs-*"
	@echo "  shell          - shell с активированной .venv"
	@echo "  clean / reset  - удалить/пересобрать окружение"
