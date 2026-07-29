# === Параметры проекта ===

UV      ?= uv
VENV    ?= .venv

HOST    ?= 127.0.0.1
PORT    ?= 8888

PROJECT_DIR := $(abspath $(CURDIR))
PYTHON      := $(PROJECT_DIR)/$(VENV)/bin/python

# === systemd ===

SYSTEMD_DIR := $(HOME)/.config/systemd/user

BOT_SERVICE := objopa-bot.service
API_SERVICE := objopa-api.service

BOT_SERVICE_FILE := $(SYSTEMD_DIR)/$(BOT_SERVICE)
API_SERVICE_FILE := $(SYSTEMD_DIR)/$(API_SERVICE)

.PHONY: all init sync prod upgrade-lock check \
        install-services uninstall-services reload-services \
        run-bot run-api run-all \
        restart-bot restart-api restart-all \
        stop-bot stop-api stop-all \
        logs-bot logs-api logs-all \
        status-bot status-api status \
        shell clean reset help

# По умолчанию — создать окружение и установить dev-зависимости
all: init sync

# === Окружение и зависимости ===

## Создать .venv под Python 3.13, если его ещё нет
init:
	@if [ ! -x "$(VENV)/bin/python" ]; then \
		echo "🐍 Creating project venv ($(VENV))..."; \
		$(UV) venv "$(VENV)" --python 3.13; \
	else \
		echo "🐍 Virtual environment already exists: $(VENV)"; \
	fi

## Установить зависимости для разработки
sync:
	@echo "📦 uv sync (dev)..."
	$(UV) sync

## Установить production-зависимости строго из uv.lock
prod:
	@echo "🚀 uv sync (prod: --frozen --no-dev)..."
	$(UV) sync --frozen --no-dev

## Обновить uv.lock
upgrade-lock:
	@echo "🔒 uv lock --upgrade..."
	$(UV) lock --upgrade

## Проверить окружение перед созданием systemd-сервисов
check:
	@command -v "$(UV)" >/dev/null 2>&1 || { \
		echo "❌ uv не найден в PATH"; \
		exit 1; \
	}
	@test -f "$(PROJECT_DIR)/pyproject.toml" || { \
		echo "❌ pyproject.toml не найден"; \
		exit 1; \
	}
	@test -x "$(PYTHON)" || { \
		echo "❌ $(PYTHON) не найден. Выполни: make prod"; \
		exit 1; \
	}

# === Установка systemd-сервисов ===

install-services: check
	@echo "⚙️ Creating systemd user services..."
	@mkdir -p "$(SYSTEMD_DIR)"

	@printf '%s\n' \
		'[Unit]' \
		'Description=Objopa Telegram Bot' \
		'StartLimitIntervalSec=0' \
		'' \
		'[Service]' \
		'Type=simple' \
		'WorkingDirectory=$(PROJECT_DIR)' \
		'ExecStart=$(PYTHON) -m tg_bot.main' \
		'Environment=PYTHONUNBUFFERED=1' \
		'Restart=always' \
		'RestartSec=3' \
		'TimeoutStopSec=30' \
		'' \
		'[Install]' \
		'WantedBy=default.target' \
		> "$(BOT_SERVICE_FILE)"

	@printf '%s\n' \
		'[Unit]' \
		'Description=Objopa FastAPI Backend' \
		'StartLimitIntervalSec=0' \
		'' \
		'[Service]' \
		'Type=simple' \
		'WorkingDirectory=$(PROJECT_DIR)' \
		'ExecStart=$(PYTHON) -m uvicorn backend.main:app --host $(HOST) --port $(PORT)' \
		'Environment=PYTHONUNBUFFERED=1' \
		'Restart=always' \
		'RestartSec=3' \
		'TimeoutStopSec=30' \
		'' \
		'[Install]' \
		'WantedBy=default.target' \
		> "$(API_SERVICE_FILE)"

	@systemctl --user daemon-reload
	@systemctl --user enable --now "$(BOT_SERVICE)" "$(API_SERVICE)"

	@echo "✅ Services installed and started:"
	@echo "   $(BOT_SERVICE)"
	@echo "   $(API_SERVICE)"

reload-services:
	@echo "🔄 Reloading systemd units..."
	@systemctl --user daemon-reload
	@systemctl --user restart "$(BOT_SERVICE)" "$(API_SERVICE)"

uninstall-services:
	@echo "🗑 Removing systemd services..."
	-@systemctl --user disable --now "$(BOT_SERVICE)" 2>/dev/null
	-@systemctl --user disable --now "$(API_SERVICE)" 2>/dev/null
	@rm -f "$(BOT_SERVICE_FILE)" "$(API_SERVICE_FILE)"
	@systemctl --user daemon-reload
	@systemctl --user reset-failed
	@echo "✅ Services removed"

# === Запуск ===

run-bot:
	@echo "🤖 Starting Telegram Bot..."
	@systemctl --user start "$(BOT_SERVICE)"

run-api:
	@echo "🚀 Starting FastAPI..."
	@systemctl --user start "$(API_SERVICE)"

run-all:
	@echo "▶️ Starting all services..."
	@systemctl --user start "$(BOT_SERVICE)" "$(API_SERVICE)"

# === Перезапуск ===

restart-bot:
	@echo "♻️ Restarting Telegram Bot..."
	@systemctl --user restart "$(BOT_SERVICE)"

restart-api:
	@echo "♻️ Restarting FastAPI..."
	@systemctl --user restart "$(API_SERVICE)"

restart-all:
	@echo "♻️ Restarting all services..."
	@systemctl --user restart "$(BOT_SERVICE)" "$(API_SERVICE)"

# === Остановка ===

stop-bot:
	@echo "⛔ Stopping Telegram Bot..."
	@systemctl --user stop "$(BOT_SERVICE)"

stop-api:
	@echo "⛔ Stopping FastAPI..."
	@systemctl --user stop "$(API_SERVICE)"

stop-all:
	@echo "⛔ Stopping all services..."
	@systemctl --user stop "$(BOT_SERVICE)" "$(API_SERVICE)"

# === Логи ===

logs-bot:
	@journalctl --user -u "$(BOT_SERVICE)" -n 100 -f

logs-api:
	@journalctl --user -u "$(API_SERVICE)" -n 100 -f

logs-all:
	@journalctl --user \
		-u "$(BOT_SERVICE)" \
		-u "$(API_SERVICE)" \
		-n 100 -f

# === Статус ===

status-bot:
	@systemctl --user status "$(BOT_SERVICE)" --no-pager

status-api:
	@systemctl --user status "$(API_SERVICE)" --no-pager

status:
	@systemctl --user status \
		"$(BOT_SERVICE)" \
		"$(API_SERVICE)" \
		--no-pager

# === Остальное ===

shell:
	@echo "🐚 Shell in $(VENV)..."
	@/usr/bin/env bash -lc 'source "$(VENV)/bin/activate" && exec $$SHELL -l'

clean:
	@echo "🧹 Removing $(VENV)..."
	rm -rf "$(VENV)"

reset: clean init sync

help:
	@echo "Dependencies:"
	@echo "  make init                - создать .venv с Python 3.13"
	@echo "  make sync                - установить dev-зависимости"
	@echo "  make prod                - установить production-зависимости"
	@echo "  make upgrade-lock        - обновить uv.lock"
	@echo ""
	@echo "Systemd:"
	@echo "  make install-services    - создать, включить и запустить сервисы"
	@echo "  make reload-services     - перечитать unit-файлы и перезапустить"
	@echo "  make uninstall-services  - удалить systemd-сервисы"
	@echo ""
	@echo "Control:"
	@echo "  make run-bot / run-api / run-all"
	@echo "  make restart-bot / restart-api / restart-all"
	@echo "  make stop-bot / stop-api / stop-all"
	@echo ""
	@echo "Monitoring:"
	@echo "  make logs-bot / logs-api / logs-all"
	@echo "  make status-bot / status-api / status"
