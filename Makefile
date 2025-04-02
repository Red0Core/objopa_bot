# Путь до виртуального окружения
VENV_DIR := ./venv_objopa
UV := uv
PYTHON := $(VENV_DIR)/bin/python

# Названия tmux-сессий
BOT_SESSION := objopa-bot
API_SESSION := objopa-api

.PHONY: all install run-bot run-api clean reset restart-bot restart-api stop-bot stop-api

all: install

install:
	@echo "📦 Creating venv and installing dependencies..."
	$(UV) venv $(VENV_DIR) --python=3.13.2
	$(UV) pip install -r tg_bot/requirements.txt -p $(VENV_DIR)
	$(UV) pip install -r backend/requirements.txt -p $(VENV_DIR)
	$(UV) pip install uvloop -p $(VENV_DIR)

run-bot:
	@echo "🤖 Running Telegram Bot in tmux: $(BOT_SESSION)"
	tmux new-session -d -s $(BOT_SESSION) 'cd tg_bot && ../$(PYTHON) main.py'

run-api:
	@echo "🚀 Running FastAPI in tmux: $(API_SESSION)"
	tmux new-session -d -s $(API_SESSION) 'cd backend && ../$(PYTHON) -m uvicorn main:app --host 127.0.0.1 --port 8888'

restart-bot:
	@echo "♻️ Restarting Telegram Bot..."
	tmux kill-session -t $(BOT_SESSION) || true
	$(MAKE) run-bot

restart-api:
	@echo "♻️ Restarting FastAPI..."
	tmux kill-session -t $(API_SESSION) || true
	$(MAKE) run-api

stop-bot:
	@echo "⛔ Stopping Telegram Bot..."
	tmux kill-session -t $(BOT_SESSION) || true

stop-api:
	@echo "⛔ Stopping FastAPI..."
	tmux kill-session -t $(API_SESSION) || true

clean:
	@echo "🧹 Removing virtual environment..."
	rm -rf $(VENV_DIR)

reset: clean all

logs-bot:
	@echo "📜 Attaching to Telegram Bot logs..."
	tmux attach -t objopa-bot

logs-api:
	@echo "📜 Attaching to FastAPI logs..."
	tmux attach -t objopa-api
