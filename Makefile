# === Параметры окружения ===
VENV_DIR ?= ./venv_objopa
UV       ?= uv
PYTHON   := $(VENV_DIR)/bin/python
TMUX     ?= tmux

# Сети/порты
HOST ?= 127.0.0.1
PORT ?= 8888

# Названия tmux-сессий
BOT_SESSION := objopa-bot
API_SESSION := objopa-api

.PHONY: all install sync dev prod lock upgrade-lock run-bot run-api clean reset \
        restart-bot restart-api stop-bot stop-api logs-bot logs-api shell

# По умолчанию — установка зависимостей
all: install

# 1) Создать venv (если нет) и накатить зависимости по pyproject.toml + uv.lock
install: sync

# Полная синхронизация (дев-окружение, с dev-группами)
sync:
	@echo "📦 Creating venv and syncing dependencies (dev)..."
	$(UV) venv $(VENV_DIR) --python=3.13
	. $(VENV_DIR)/bin/activate && $(UV) sync

# Прод-синхронизация: ровно по lock и без dev-зависимостей
prod:
	@echo "🚀 Syncing dependencies for PROD (frozen, no-dev)..."
	$(UV) venv $(VENV_DIR) --python=3.13
	. $(VENV_DIR)/bin/activate && $(UV) sync --frozen --no-dev

# Дев-синхронизация (если lock уже есть, но с dev-группами)
dev:
	@echo "🛠  Syncing dependencies for DEV..."
	. $(VENV_DIR)/bin/activate && $(UV) sync

# Пересобрать lock в рамках диапазонов (обновить версии)
upgrade-lock:
	@echo "🔒 Upgrading uv.lock..."
	$(UV) lock --upgrade

# 2) Запуски через tmux из общего venv
run-bot:
	@echo "🤖 Running Telegram Bot in tmux: $(BOT_SESSION)"
	$(TMUX) new-session -d -s $(BOT_SESSION) 'source $(VENV_DIR)/bin/activate; $(PYTHON) -m tg_bot.main'

run-api:
	@echo "🚀 Running FastAPI in tmux: $(API_SESSION)"
	$(TMUX) new-session -d -s $(API_SESSION) 'source $(VENV_DIR)/bin/activate; $(PYTHON) -m uvicorn backend.main:app --host $(HOST) --port $(PORT)'

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

# Удобно зайти в общий venv
shell:
	@echo "🐍 Spawning shell in venv..."
	@/usr/bin/env bash -lc 'source $(VENV_DIR)/bin/activate; exec $$SHELL -l'

# Снести venv полностью
clean:
	@echo "🧹 Removing virtual environment..."
	rm -rf $(VENV_DIR)

# Полный ресет: снести venv и пересобрать
reset: clean install
