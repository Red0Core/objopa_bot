import asyncio
from pathlib import Path
from typing import Any, Sequence

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    FSInputFile,
    InputMediaPhoto,
    MediaUnion,
    Message,
)

from core.config import DOWNLOADS_PATH
from tg_bot.downloaders import (
    INSTAGRAM_REGEX,
    TWITTER_REGEX,
    download_instagram_media,
    download_twitter_media,
    download_with_gallery_dl,
    download_with_ytdlp,
)
from tg_bot.services.gpt import split_message_by_paragraphs

router = Router()


async def send_images_in_chunks(message: Message, images: list[Path], caption: str | None = None):
    """Разбивает список изображений на чанки по 10 и отправляет их в Telegram"""

    def chunk_list(lst: Sequence[Any], size: int = 10) -> Sequence[Any]:
        """Функция разбивает список на части по size элементов"""
        return [lst[i : i + size] for i in range(0, len(lst), size)]

    image_chunks = chunk_list(images, 10)

    for i, chunk in enumerate(image_chunks):
        media_group: list[MediaUnion] = [InputMediaPhoto(media=FSInputFile(img)) for img in chunk]

        # Отправляем первый альбом с подписью, остальные без
        if i == 0 and caption:
            await message.reply_media_group(media=media_group, caption=caption)
        else:
            await message.reply_media_group(media=media_group)
        await asyncio.sleep(5)


async def process_instagram(message: Message, url: str) -> None:
    """Handle Instagram URL download and sending."""
    status_message = await message.answer("⏳ Загружаю медиа из Instagram...")

    shortcode, error = await download_instagram_media(url)

    if shortcode:
        download_path = DOWNLOADS_PATH
        files = sorted([f for f in download_path.iterdir() if f.name.startswith(shortcode)])

        images: list[Path] = []
        videos: list[Path] = []
        caption: str | None = None

        for file_path in files:
            suffix = file_path.suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png"):
                if "reel" in url:
                    continue
                images.append(file_path)
            elif suffix in (".mp4", ".mov"):
                videos.append(file_path)
            elif suffix == ".txt":
                caption = file_path.read_text(encoding="utf-8")

        if videos:
            for video in videos:
                await message.reply_video(FSInputFile(video), caption=caption)

        caption_arr = split_message_by_paragraphs(caption or "")
        if len(images) > 1:
            await send_images_in_chunks(message, images, caption_arr[0])
            for part in caption_arr[1:]:
                await message.reply(part)
        elif len(images) == 1:
            replied_photo = await message.reply_photo(
                FSInputFile(images[0]), caption=caption_arr[0]
            )
            for part in caption_arr[1:]:
                await replied_photo.reply(part)

        await status_message.delete()
    else:
        await status_message.edit_text(error if error else "❌ Не удалось загрузить медиа.")


@router.message(Command("insta"))
async def instagram_handler(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("❌ Ты не указал ссылку! Используй: `/insta <ссылка>`")
        return

    url = command.args.strip()

    if not INSTAGRAM_REGEX.match(url):
        await message.answer("❌ Это не похоже на ссылку Instagram. Попробуй еще раз.")
        return

    await process_instagram(message, url)


@router.message(Command("d"))
async def universal_download_handler(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("❌ Ты не указал ссылку! Используй: `/d <ссылка>`")
        return

    url = command.args.strip()

    if INSTAGRAM_REGEX.match(url):
        await process_instagram(message, url)
        return

    if TWITTER_REGEX.match(url):
        status_message = await message.answer("⏳ Загружаю медиа из Twitter...")
        img_files, vid_files, tw_caption, tw_error = await download_twitter_media(url)

        success = False
        caption_arr = split_message_by_paragraphs(tw_caption or "")
        if img_files:
            if len(img_files) > 1:
                await send_images_in_chunks(message, img_files, caption_arr[0])
                for part in caption_arr[1:]:
                    await message.reply(part, parse_mode="MarkdownV2")
            else:
                replied = await message.reply_photo(
                    FSInputFile(img_files[0]),
                    caption=caption_arr[0],
                    parse_mode="MarkdownV2",
                )
                for part in caption_arr[1:]:
                    await replied.reply(part, parse_mode="MarkdownV2")
            success = True

        if vid_files:
            for idx, video in enumerate(vid_files):
                await message.reply_video(
                    FSInputFile(video),
                    caption=tw_caption if not success and idx == 0 else None,
                )
            success = True

        if success:
            await status_message.delete()
        else:
            await status_message.edit_text(tw_error or "❌ Не удалось скачать медиа.")
        return

    status_message = await message.answer("⏳ Загружаю медиа...")

    files, caption, error = await download_with_ytdlp(url)
    if not files:
        g_files, g_caption, g_error = await download_with_gallery_dl(url)
        files = g_files
        if not caption:
            caption = g_caption
        if not error:
            error = g_error

    if files:
        for i, file_path in enumerate(files):
            suffix = file_path.suffix.lower()
            input_file = FSInputFile(file_path)
            if suffix in (".jpg", ".jpeg", ".png", ".webp"):
                if i == 0:
                    await message.reply_photo(input_file, caption=caption)
                else:
                    await message.reply_photo(input_file)
            elif suffix in (".mp4", ".mov", ".mkv", ".webm"):
                if i == 0:
                    await message.reply_video(input_file, caption=caption)
                else:
                    await message.reply_video(input_file)
            else:
                if i == 0:
                    await message.reply_document(input_file, caption=caption)
                else:
                    await message.reply_document(input_file)

        await status_message.delete()
    else:
        await status_message.edit_text(error if error else "❌ Не удалось скачать медиа.")
