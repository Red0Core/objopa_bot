from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from datetime import datetime
import json
import os
import html

from logger import logger
from .mention_dice import markdown_to_telegram_html, split_message_by_paragraphs, AI_CLIENT

track_router = Router()
TRACK_FILE = "trackers.json"


# ===== Хранилище =====
def load_trackers():
    if not os.path.exists(TRACK_FILE):
        return {}
    with open(TRACK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_trackers(data):
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@track_router.message(Command("track"))
async def handle_tracking(message: Message):
    args = message.text.strip().split(maxsplit=2)
    if len(args) < 2 or args[1] not in ["start", "stop", "status", "desc"]:
        await message.reply("Использование:\n"
                            "/track start название(слитно)\n"
                            "/track stop название(слитно)\n"
                            "/track status\n"
                            "/track desc название(слитно) описание")
        return

    action = args[1]
    user_id = str(message.from_user.id)
    user_name = message.from_user.full_name
    chat_id = message.chat.id

    data = load_trackers()
    if user_id not in data:
        user_name = user_name or message.from_user.first_name or "Безымянный герой 🚀"
        data[user_id] = {"name": user_name, "trackers": {}, "chat_id": chat_id}

    trackers = data[user_id]["trackers"]

    if action == "status":
        if not trackers:
            await message.reply("У тебя пока нет активных трекеров.")
            return
        reply = ["🧮 <b>Твои трекеры:</b>"]
        for track_name, info in trackers.items():
            days = (datetime.now().timestamp() - info["start"]) // 86400
            desc = info.get("description", "")
            reply.append(f"• <b>{track_name}</b>: <code>{int(days)} дней</code>{' — ' + desc if desc else ''}")
        await message.reply("\n".join(reply), parse_mode="HTML")
        return

    if action == "desc":
        if len(args) < 3:
            await message.reply("Укажи описание: /track desc название(слиитно) описание")
            return
        parts = args[2].split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Укажи описание: /track desc название(слиитно) описание")
            return
        name, description = parts
        if name not in trackers:
            await message.reply(f"Трекер <b>{name}</b> не найден.", parse_mode="HTML")
            return
        trackers[name]["description"] = description
        save_trackers(data)
        await message.reply(f"📝 Описание для <b>{name}</b> обновлено!", parse_mode="HTML")
        logger.info(f"[TRACK] User {user_id} ({user_name}) stopped tracking '{name}' in chat {chat_id}")
        return

    if len(args) < 3:
        await message.reply("Укажи название: /track start название(слиитно)")
        return

    name = args[2].strip()

    if action == "start":
        if name in trackers:
            await message.reply(f"Трекер <b>{name}</b> уже запущен.", parse_mode="HTML")
            return
        trackers[name] = {
            "start": datetime.now().timestamp(),
            "description": ""
        }
        save_trackers(data)
        await message.reply(f"🟢 Отслеживание <b>{name}</b> начато!", parse_mode="HTML")
        logger.info(f"[TRACK] User {user_id} ({user_name}) started tracking '{name}' in chat {chat_id}")

    elif action == "stop":
        if name not in trackers:
            await message.reply(f"Нет активного трекера <b>{name}</b>.", parse_mode="HTML")
            return
        tracker = trackers[name]
        days = int((datetime.now().timestamp() - tracker["start"]) // 86400)
        prompt = (
            f"Пользователь {user_name} завершил отслеживание цели «{name}».\n"
            f"Описание цели: {tracker['description'] or 'Без описания'}.\n"
            f"Он продержался {days} дней. Старт был с timestamp = {int(tracker['start'])}.\n"
            f"Напиши язвительное и немного унизительное мотивационное сообщение (1 абзац), чтобы его задело, но в то же время замотивировало не облажаться в следующий раз.\n"
            f"Можешь использовать иронию, сарказм, подколы, тёмный юмор. Не жалей — цель одна: взбодрить и пнуть вперёд.\n"
            f"Не используй HTML, можешь добавить немного эмодзи. Без воды, просто жёсткий, но запоминающийся текст."
        )
        try:
            gpt_response = await AI_CLIENT.get_response(prompt)
            cleaned = markdown_to_telegram_html(gpt_response)
            mention = f'<a href="tg://user?id={user_id}">{html.escape(user_name)}</a>'
            cleaned = f"{mention}, {cleaned}"
            for i in split_message_by_paragraphs(cleaned):
                await message.reply(i, parse_mode="HTML")
            del trackers[name]
            save_trackers(data)
        except Exception as e:
            logger.error(f"[TRACK] GPT error for user {user_id} on tracker '{name}': {e}")
            await message.reply("Ошибка при генерации сообщения. Попробуй позже.")
        logger.info(f"[TRACK] User {user_id} ({user_name}) stopped tracking '{name}' in chat {chat_id}")

# Отпрвка ежедневного сообщения
async def send_daily_message(bot):
    data = load_trackers()
    for user_id, user_info in data.items():
        for track_name, track_data in user_info["trackers"].items():
            days = int((datetime.now().timestamp() - track_data["start"]) // 86400)
            description = track_data.get("description", "")
            user_name = user_info["name"]
            chat_id = user_info["chat_id"] or user_id

            prompt = (
                f"Ты мотивирующий ассистент с иронией и чёрным юмором. "
                f"Пользователь по имени {user_name} отслеживает цель «{track_name}» ({description}).\n"
                f"Прошло уже {days} дней! Старт был с timestamp = {int(track_data['start'])}.\n"
                f"Сгенерируй короткое сообщение (1–2 абзаца) в стиле: мотивационно, с лёгким запоминающимся посылом и долей черного юмора. "
                f"Без HTML, но можно немного эмодзи (без перегруза).\n"
                f"Говори с пользователем как с другом — с подколами, шутками, но в рамках бодрящей поддержки. "
                f"Главное — чтобы захотелось продолжать дальше.\n"
                f"Нецензурные слова допустимы, если они уместны и работают на стиль."
            )

            try:
                gpt_response = await AI_CLIENT.get_response(prompt)
                cleaned = markdown_to_telegram_html(gpt_response)
                mention = f'<a href="tg://user?id={user_id}">{html.escape(user_name)}</a>'
                cleaned = f"{mention}, {cleaned}"
                for i in split_message_by_paragraphs(cleaned):
                    await bot.send_message(chat_id, i, parse_mode="HTML")
                    logger.info(f"[DAILY] Sent motivation to user {user_id} for tracker '{track_name}' ({days} days)")
            except Exception as e:
                logger.error(f"[DAILY] GPT error for user {user_id} on tracker '{track_name}': {e}")
