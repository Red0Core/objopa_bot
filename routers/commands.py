from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile, InputMediaPhoto
import wolframalpha
from config import GIFS_ID, WOLFRAMALPHA_TOKEN
from services.cbr import generate_cbr_output
from services.alphavantage import fetch_currency_data, parse_currency_data, calculate_change
from services.horoscope_mail_ru import get_horoscope_mail_ru
from services.instagram_loader import download_instagram_media, INSTAGRAM_REGEX
from logger import logger
import asyncio
import os

import traceback

router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer_animation(
        animation=GIFS_ID["Салам дай брад"],
        caption="MEXC за идею.\nА тут /cbr - курсы валют на сегодня и каждый день в 12 отправляю\n/price BTC и тд с бинанса выкачивает прайс\nПока что все"
    )

@router.message(Command("cbr"))
async def get_cbr_rates_handler(message: Message):
    """
    Отправляет курсы ЦБ РФ за сегодня и его изменение
    """
    logger.info(f"Отправляем курсы валют пользователю {message.from_user.id}")
    await message.reply(await generate_cbr_output())

@router.message(Command("rub"))
async def get_forex_rub_rates_handler(message: Message):
    """
    Отправляет текстовое сообщение и график курса валют в Telegram.
    """
    output = await generate_cbr_output()
    try:
        # Получаем данные
        arr = asyncio.gather(fetch_currency_data("USD", "RUB"), fetch_currency_data("EUR", "RUB"))
        for data in (await arr):
            symbol = data["Meta Data"]["2. From Symbol"]
            market = data["Meta Data"]["3. To Symbol"]

            today, yesterday, price_7d, price_30d = parse_currency_data(data)

            change_1d = calculate_change(today, yesterday)
            change_7d = calculate_change(today, price_7d)
            change_30d = calculate_change(today, price_30d)

            # Формируем сообщение
            output += (
                    f"\n💹 <b>Курс {symbol}/{market}:</b>\n"
                    f"Текущая цена: <code>{today:.2f} {market}</code>\n"
                    f"🔸 За 1 день: <code>{change_1d[0]:+.2f} ({change_1d[1]:+.2f}%)</code>\n"
                )
            if price_7d:
                output += f"🔹 За 7 дней: <code>{change_7d[0]:+.2f} ({change_7d[1]:+.2f}%)</code>\n"
            if price_30d:
                output += f"🔸 За 30 дней: <code>{change_30d[0]:+.2f} ({change_30d[1]:+.2f}%)</code>\n"

    except Exception as e:
        logger.error(f"Ошибка: {traceback.format_exc()}")
        await message.reply(f"Ошибка: {e}")

    await message.reply(output, parse_mode="html")
    logger.info(f"Успешно отправил рубль для {message.from_user.id}")

@router.message(Command("horoscope"))
async def horoscope_command(message: Message):
    zodiac_map = {
        "taurus": "телец",
        "cancer": "рак",
        "libra": "весы",
        "scorpio": "скорпион",
        "sagittarius": "стрелец",
        "capricorn": "козерог",
        "aquarius": "водолей",
        "pisces": "рыбы",
        "aries": "овен",
        "gemini": "близнецы",
        "leo": "лев",
        "virgo": "дева"
    }
    try:
        # Получает знак зодиака из сообщения
        zodiac_sign = message.text.split()[1].lower()
        # Проверяет если знак зодиака на русском или на английском
        reversed_zodiac_map = {v: k for k, v in zodiac_map.items()}
        if zodiac_sign in reversed_zodiac_map:
            zodiac_eng = reversed_zodiac_map[zodiac_sign]
            text = await get_horoscope_mail_ru(zodiac_eng, zodiac_sign)
        else:
            text = await get_horoscope_mail_ru(zodiac_sign, zodiac_map.get(zodiac_sign))
        await message.answer(text=text)
        logger.info(f"Отправляем гороскоп в чат {message.chat.id} для {zodiac_sign}")
        return
    except (IndexError, KeyError):
        await message.answer("Пожалуйста, укажите знак зодиака на английском или русском. Например: /horoscope libra или /horoscope весы")
        return

@router.message(Command("calc"))
async def calculator_wolframaplha_math(message: Message):
    arr = message.text.split(maxsplit=1)
    if len(arr) == 2:
        try:
            # 🔹 Безопасное выполнение eval (только числа и операторы)
            result = eval(arr[1], {"__builtins__": {}})
            await message.answer(str(result))
        except Exception:
            client = wolframalpha.Client(WOLFRAMALPHA_TOKEN)
            res = await client.aquery(arr[1])
            await message.answer(next(res.results).text)
    else:
        await message.answer("Использовать /calc и тут ваша матеша")

async def send_images_in_chunks(message, images, caption=None):
    """ Разбивает список изображений на чанки по 10 и отправляет их в Telegram """
    
    def chunk_list(lst, size=10):
        """Функция разбивает список на части по size элементов"""
        return [lst[i:i + size] for i in range(0, len(lst), size)]

    image_chunks = chunk_list(images, 10)

    for i, chunk in enumerate(image_chunks):
        media_group = [InputMediaPhoto(media=FSInputFile(img)) for img in chunk]
        
        # Отправляем первый альбом с подписью, остальные без
        if i == 0 and caption:
            await message.reply_media_group(media_group, caption=caption)
        else:
            await message.reply_media_group(media_group)
        await asyncio.sleep(5)

@router.message(Command("insta"))
async def instagram_handler(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("❌ Ты не указал ссылку! Используй: `/insta <ссылка>`")
        return

    url = command.args.strip()

    if not INSTAGRAM_REGEX.match(url):
        await message.answer("❌ Это не похоже на ссылку Instagram. Попробуй еще раз.")
        return

    status_message = await message.answer("⏳ Загружаю медиа из Instagram...")

    # Загружаем файл
    shortcode, error = await download_instagram_media(url)

    if shortcode:
        download_path = "downloads"
        files = [f for f in os.listdir(download_path) if f.startswith(shortcode)]

        images, videos, caption = [], [], None

        for file in sorted(files):
            file_path = os.path.join(download_path, file)
            if file.endswith((".jpg", ".jpeg", ".png")):
                if 'reel' in url:
                    continue
                images.append(file_path)
            elif file.endswith((".mp4", ".mov")):
                 videos.append(file_path)
            elif file.endswith(".txt"):
                with open(file_path, "r", encoding="utf-8") as f:
                    caption = f.read()

        # 🔹 Отправляем медиа
        if videos:
            for video in videos:
               await message.reply_video(FSInputFile(video), caption=caption)

        if len(images) > 1:
            await send_images_in_chunks(message, images, caption)
        elif len(images) == 1:
            await message.reply_photo(FSInputFile(images[0]), caption=caption)

        await status_message.delete()
    else:
        await status_message.edit_text(error if error else "❌ Не удалось загрузить медиа.")

# Генерация меню игр
def games_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Блэкджек", callback_data="start_blackjack")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close_menu")],
    ])
    return keyboard

@router.message(Command("games"))
async def games_command(message: Message):
    await message.answer("Выберите игру из списка:", reply_markup=games_menu())

@router.callback_query(lambda c: c.data == "close_menu")
async def close_menu(callback: CallbackQuery):
    await callback.message.edit_text("Меню игр закрыто.", reply_markup=None)
