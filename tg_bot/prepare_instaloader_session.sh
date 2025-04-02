#!/bin/bash

# Загружаем переменные из .env
set -a
source .env
set +a

# Проверяем, что INSTA_USERNAME задан
if [ -z "$INSTAGRAM_USERNAME" ]; then
  echo "[!] INSTAGRAM_USERNAME не задан в .env"
  exit 1
fi

SESSION_SRC="$HOME/.config/instaloader/session-$INSTAGRAM_USERNAME"
SESSION_DEST="./tg_bot/session/session-$INSTAGRAM_USERNAME"

if [ -f "$SESSION_SRC" ]; then
    echo "[+] Копирую instaloader session файл для $INSTAGRAM_USERNAME..."
    mkdir -p ./tg_bot/session
    cp "$SESSION_SRC" "$SESSION_DEST"
    echo "[+] Готово!"
else
    echo "[!] Session файл не найден. Залогинься через: instaloader -l $INSTAGRAM_USERNAME"
    exit 1
fi
