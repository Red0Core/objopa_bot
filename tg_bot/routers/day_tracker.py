import html
import os
from datetime import datetime, timedelta
from typing import cast

import ujson
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.config import STORAGE_PATH

from .mention_dice import AI_CLIENT, markdown_to_telegram_html, split_message_by_paragraphs

track_router = Router()
TRACK_FILE = STORAGE_PATH / "trackers.json"


# ===== Хранилище =====
def load_trackers():
    if not os.path.exists(TRACK_FILE):
        return {}
    with open(TRACK_FILE, "r", encoding="utf-8") as f:
        return ujson.load(f)


def save_trackers(data):
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        ujson.dump(data, f, ensure_ascii=False, indent=2)


@track_router.message(Command("track"))
async def handle_tracking(message: Message):
    args = message.text.strip().split(maxsplit=2) if message.text else []
    if len(args) < 2 or args[1] not in ["start", "stop", "status", "desc", "stats"]:
        await message.reply(
            "Использование:\n"
            "/track start название\n"
            "/track stop название\n"
            "/track stats название\n"
            "/track status\n"
            "/track desc название описание"
        )
        return
    if message.from_user is None:
        await message.reply("Пользователь не найден.")
        return
    action = args[1]
    user_id = str(message.from_user.id)
    user_name = message.from_user.full_name or message.from_user.first_name or "Безымянный герой"
    chat_id = message.chat.id

    data = load_trackers()
    if user_id not in data:
        data[user_id] = {"name": user_name, "trackers": {}, "chat_id": chat_id}

    trackers = data[user_id]["trackers"]

    if action == "status":
        if not trackers:
            await message.reply("У тебя пока нет активных трекеров.")
            return

        now = datetime.now().timestamp()
        reply = ["🧮 <b>Твои трекеры:</b>"]
        for track_name, info in trackers.items():
            desc = info.get("description", "")
            history = info.get("history", [])

            if "start" in info:
                duration = int(now - info["start"])
                days, rem = divmod(duration, 86400)
                hours = rem // 3600
                reply.append(
                    f"• <b>{track_name}</b>: <code>{days}д {hours}ч</code>{' — ' + desc if desc else ''}"
                )
            else:
                reply.append(
                    f"• <b>{track_name}</b>: <i>неактивен</i>{' — ' + desc if desc else ''}"
                )

        await message.reply("\n".join(reply), parse_mode="HTML")
        return

    if action == "desc":
        if len(args) < 3:
            await message.reply("Укажи описание: /track desc название описание")
            return
        parts = args[2].split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Укажи описание: /track desc название описание")
            return
        name, description = parts
        if name not in trackers:
            await message.reply(f"Трекер <b>{name}</b> не найден.", parse_mode="HTML")
            return
        trackers[name]["description"] = description
        save_trackers(data)
        await message.reply(f"📝 Описание для <b>{name}</b> обновлено!", parse_mode="HTML")
        return

    if len(args) < 3:
        await message.reply("Укажи название: /track start|stop|stats название")
        return

    name = args[2].strip()

    if action == "start":
        if name in trackers and "start" in trackers[name]:
            await message.reply(f"Трекер <b>{name}</b> уже запущен.", parse_mode="HTML")
            return
        if name not in trackers:
            trackers[name] = {"description": "", "history": []}
        trackers[name]["start"] = datetime.now().timestamp()
        save_trackers(data)
        await message.reply(f"🟢 Отслеживание <b>{name}</b> начато!", parse_mode="HTML")

    elif action == "stop":
        if name not in trackers or "start" not in trackers[name]:
            await message.reply(f"Нет активного трекера <b>{name}</b>.", parse_mode="HTML")
            return
        track = trackers[name]
        end = datetime.now().timestamp()
        history = track.get("history", [])
        history.append({"start": track["start"], "end": end})
        track["history"] = history
        del track["start"]
        save_trackers(data)

        # GPT сообщение после остановки
        duration = end - history[-1]["start"]
        days, rem = divmod(int(duration), 86400)
        hours = rem // 3600
        prompt = (
            f"Пользователь {user_name} остановил трекер «{name}».\n"
            f"Он продержался {days} дней и {hours} часов.\n"
            f"Это была попытка номер {len(history)}. "
            f"Напиши уничижительно-мотивирующее сообщение: не хвали, а подстёбывай и вдохнови в следующий раз держаться дольше. "
            f"Можно использовать немного эмодзи, но без HTML. Говори жёстко, с юмором."
        )
        try:
            gpt_response = await AI_CLIENT.get_response(prompt)
            cleaned = markdown_to_telegram_html(gpt_response)
            mention = f'<a href="tg://user?id={user_id}">{html.escape(user_name)}</a>'
            cleaned = f"{mention}, {cleaned}"
            for i in split_message_by_paragraphs(cleaned):
                await message.answer(i, parse_mode="HTML")
        except Exception as e:
            print(f"[GPT STOP] Ошибка: {e}")
        await message.reply(
            f"🛑 Трекер <b>{name}</b> остановлен и записан в историю.", parse_mode="HTML"
        )

    elif action == "stats":
        if name not in trackers or "start" not in trackers[name]:
            await message.reply(f"Нет активного трекера <b>{name}</b>.", parse_mode="HTML")
            return

        reply = [f"📊 <b>Статистика по трекеру {name}:</b>"]
        track = trackers[name]
        history = track.get("history", [])

        # Если активен сейчас — добавить это
        if "start" in track:
            current = datetime.now() - datetime.fromtimestamp(track["start"])
            d, h = current.days, current.seconds // 3600
            reply.append(f"  ➕ Сейчас идёт {d} д. {h} ч.")
        # Если есть завершённые попытки — добавить их
        if not history:
            reply.append(f"\n<b>{name}</b> — нет завершённых попыток.")
        else:
            reply.append(f"\n<b>{name}</b> — {len(history)} попыток")
            for history_iter, attempt in enumerate(history, 1):
                attempt = cast(dict[str, float], attempt)
                # Преобразуем временные метки в объекты datetime
                start_time: datetime = datetime.fromtimestamp(attempt["start"])
                end_time: datetime = datetime.fromtimestamp(attempt["end"])
                
                # Вычисляем разницу, которая будет объектом timedelta
                duration_attempt: timedelta = end_time - start_time
                
                # Теперь можно обращаться к атрибутам timedelta
                days = duration_attempt.days
                hours = duration_attempt.seconds // 3600
                
                reply.append(
                    f"  {history_iter}) {days} д. {hours} ч. (с {start_time:%d.%m %H:%M} по {end_time:%d.%m %H:%M})"
                )

        await message.reply("\n".join(reply), parse_mode="HTML")
        return


async def send_daily_message(bot):
    data = load_trackers()
    now = datetime.now().timestamp()

    for user_id, user_info in data.items():
        for track_name, track_data in user_info["trackers"].items():
            if "start" not in track_data:
                continue

            duration = int(now - track_data["start"])
            days, rem = divmod(duration, 86400)
            hours = rem // 3600
            history = track_data.get("history", [])

            user_name = user_info["name"]
            chat_id = user_info["chat_id"] or user_id
            desc = track_data.get("description", "")

            prompt = (
                f"Ты мотивирующий ассистент с чёрным юмором.\n"
                f"Пользователь {user_name} отслеживает цель «{track_name}» ({desc}).\n"
                f"Текущая попытка длится уже {days} дней и {hours} часов.\n"
                f"Всего попыток: {len(history) + 1}.\n"
                f"Прошлые: "
                + ", ".join(
                    f"{int((h['end'] - h['start']) // 86400)}д {(int((h['end'] - h['start']) % 86400)) // 3600}ч"
                    for h in history
                )
                + ".\n"
                "Напиши бодрящее сообщение (1–2 абзаца) с иронией и лёгким уничижением. Можно немного эмодзи, но без HTML."
            )

            try:
                gpt_response = await AI_CLIENT.get_response(prompt)
                cleaned = markdown_to_telegram_html(gpt_response)
                mention = f'<a href="tg://user?id={user_id}">{html.escape(user_name)}</a>'
                cleaned = f"{mention}, {cleaned}"
                for i in split_message_by_paragraphs(cleaned):
                    await bot.send_message(chat_id, i, parse_mode="HTML")
            except Exception as e:
                print(f"[DAILY GPT] Ошибка: {e}")
