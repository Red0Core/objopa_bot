from pathlib import Path
from typing import Any, Sequence, cast
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile, InputMediaPhoto, MediaUnion
import wolframalpha # type: ignore
from core.config import GIFS_ID, WOLFRAMALPHA_TOKEN
from services.horoscope_mail_ru import format_horoscope, get_horoscope_mail_ru
from services.instagram_loader import download_instagram_media, INSTAGRAM_REGEX
from core.logger import logger
import asyncio

router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer_animation(
        animation=GIFS_ID["Салам дай брад"],
        caption="MEXC за идею.\nА тут /cbr - курсы валют на сегодня и каждый день в 12 отправляю\n/price BTC и тд с бинанса выкачивает прайс\nПока что все"
    )

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
        zodiac_sign = cast(str, message.text).split()[1].lower()
        # Проверяет если знак зодиака на русском или на английском
        reversed_zodiac_map = {v: k for k, v in zodiac_map.items()}
        if zodiac_sign in reversed_zodiac_map:
            zodiac_eng = reversed_zodiac_map[zodiac_sign]
            text = format_horoscope(await get_horoscope_mail_ru(zodiac_eng))
        else:
            text = format_horoscope(await get_horoscope_mail_ru(zodiac_sign))
        await message.answer(text=text)
        logger.info(f"Отправляем гороскоп в чат {message.chat.id} для {zodiac_sign}")
        return
    except (IndexError, KeyError):
        await message.answer("Пожалуйста, укажите знак зодиака на английском или русском. Например: /horoscope libra или /horoscope весы")
        return

@router.message(Command("calc"))
async def calculator_wolframaplha_math(message: Message):
    arr = cast(str, message.text).split(maxsplit=1)
    if len(arr) == 2:
        try:
            # 🔹 Безопасное выполнение eval (только числа и операторы)
            result = eval(arr[1], {"__builtins__": {}})
            await message.answer(str(result))
        except Exception:
            client = wolframalpha.Client(WOLFRAMALPHA_TOKEN)
            res = await client.aquery(arr[1]) # type: ignore
            await message.answer(next(res.results).text)
    else:
        await message.answer("Использовать /calc и тут ваша матеша")

async def send_images_in_chunks(message: Message, images: list[Path], caption: str | None = None):
    """ Разбивает список изображений на чанки по 10 и отправляет их в Telegram """
    
    def chunk_list(lst: Sequence[Any], size: int = 10) -> Sequence[Any]:
        """Функция разбивает список на части по size элементов"""
        return [lst[i:i + size] for i in range(0, len(lst), size)]

    image_chunks = chunk_list(images, 10)

    for i, chunk in enumerate(image_chunks):
        media_group: list[MediaUnion] = [InputMediaPhoto(media=FSInputFile(img)) for img in chunk]
        
        # Отправляем первый альбом с подписью, остальные без
        if i == 0 and caption:
            await message.reply_media_group(media=media_group, caption=caption)
        else:
            await message.reply_media_group(media=media_group)
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
        download_path = Path("downloads")
        files = sorted([f for f in download_path.iterdir() if f.name.startswith(shortcode)])

        images: list[Path] = []
        videos: list[Path] = []
        caption: str | None = None

        for file_path in files:
            suffix = file_path.suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png"):
                if 'reel' in url:
                    continue
                images.append(file_path)
            elif suffix in (".mp4", ".mov"):
                videos.append(file_path)
            elif suffix == ".txt":
                caption = file_path.read_text(encoding="utf-8")

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

@router.callback_query(lambda c: c.data == "close_menu") # type: ignore
async def close_menu(callback: CallbackQuery):
    if callback.message is not None:
        await callback.message.edit_text("Меню игр закрыто.", reply_markup=None) # type: ignore
    await callback.answer()