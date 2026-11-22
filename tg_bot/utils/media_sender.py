import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast

from aiogram.types import (
    FSInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    MediaUnion,
    Message,
)

from core.logger import logger
from core.memory import MAX_MEDIA_BYTES, cleanup_paths
from tg_bot.utils.caption_formatter import caption_formatter

if TYPE_CHECKING:
    from tg_bot.downloaders.spotify import TrackInfo


class MediaSender:
    """Простой отправщик медиа без логики форматирования."""

    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".ts", ".mpeg", ".mpg"}
    AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus"}

    @staticmethod
    async def send(
        message: Message,
        files: list[Path],
        caption: str | None = None,
        *,
        spotify_tracks: list["TrackInfo"] | None = None,
        **_: object,
    ) -> bool:
        if not files:
            if caption:
                parts = caption_formatter.format_and_split(caption, first_max=4096, rest_max=4096)
                for part in parts:
                    await message.reply(part, parse_mode="MarkdownV2")
                return True
            return False

        try:
            caption_parts = caption_formatter.format_and_split(caption) if caption else []
            sendable = []
            for path in files:
                try:
                    if path.stat().st_size > MAX_MEDIA_BYTES:
                        logger.warning(f"Skip {path.name}: {path.stat().st_size / (1024 * 1024):.1f}MB > 50MB")
                        continue
                except OSError:
                    continue
                sendable.append(path)

            audio_metadata = {track.local_path: track for track in spotify_tracks or ()}
            audio_covers = {track.local_cover_path for track in audio_metadata.values()}
            images = [f for f in sendable if f.suffix.lower() in MediaSender.IMAGE_EXTS and f not in audio_covers]
            videos = [f for f in sendable if f.suffix.lower() in MediaSender.VIDEO_EXTS]
            audio = [f for f in sendable if f.suffix.lower() in MediaSender.AUDIO_EXTS]
            docs = [
                f
                for f in sendable
                if f.suffix.lower() not in (MediaSender.IMAGE_EXTS | MediaSender.VIDEO_EXTS | MediaSender.AUDIO_EXTS)
            ]

            caption_used = False

            if images:
                await MediaSender._send_images(message, images, caption_parts[0] if caption_parts else None)
                caption_used = True

            if videos:
                first_caption = caption_parts[0] if caption_parts and not caption_used else None
                await MediaSender._send_videos(message, videos, first_caption)
                caption_used = True

            if audio:
                first_caption = caption_parts[0] if caption_parts and not caption_used else None
                await MediaSender._send_audio(message, audio, first_caption, audio_metadata)
                caption_used = True

            if docs:
                first_caption = caption_parts[0] if caption_parts and not caption_used else None
                await MediaSender._send_documents(message, docs, first_caption)
                caption_used = True

            if not sendable and files:
                await message.reply("Файл больше 50 МБ, Telegram его не примет. Скачиваю только то, что влезает.")

            start_idx = 1 if caption_used else 0
            for part in caption_parts[start_idx:]:
                await message.reply(part, parse_mode="MarkdownV2")

            return True

        except Exception as e:
            logger.error(f"Error sending media: {e}")
            return False
        finally:
            cleanup_paths(files)

    @staticmethod
    async def _send_images(message: Message, images: list[Path], caption: str | None):
        for i in range(0, len(images), 10):
            chunk = images[i : i + 10]
            media_group = [InputMediaPhoto(media=FSInputFile(img)) for img in chunk]

            chunk_caption = caption if i == 0 and caption else None
            if chunk_caption and media_group:
                media_group[0] = InputMediaPhoto(
                    media=FSInputFile(chunk[0]), caption=chunk_caption, parse_mode="MarkdownV2"
                )

            await message.reply_media_group(media=cast(list[MediaUnion], media_group))

            if i + 10 < len(images):
                await asyncio.sleep(5)

    @staticmethod
    async def _send_videos(message: Message, videos: list[Path], caption: str | None):
        for idx, video in enumerate(videos):
            video_caption = caption if idx == len(videos) - 1 and caption else None
            await message.reply_video(
                FSInputFile(video),
                caption=video_caption,
                parse_mode="MarkdownV2" if video_caption else None,
                supports_streaming=True,
            )

    @staticmethod
    async def _send_audio(message: Message, audio: list[Path], caption: str | None, metadata: dict[Path, "TrackInfo"]):
        for i in range(0, len(audio), 10):
            chunk = audio[i : i + 10]
            media_group = []

            for index, path in enumerate(chunk, start=i):
                track = metadata.get(path)
                cover_path = track.local_cover_path if track else path.parent / f"{path.parent.name}.jpg"
                thumbnail = FSInputFile(cover_path) if cover_path and cover_path.is_file() else None
                track_caption = caption if index == 0 else None
                media_group.append(
                    InputMediaAudio(
                        media=FSInputFile(path),
                        caption=track_caption,
                        parse_mode="MarkdownV2" if track_caption else None,
                        title=track.title if track else None,
                        performer=track.artist if track else None,
                        thumbnail=thumbnail,
                    )
                )

            if len(media_group) == 1:
                item = media_group[0]
                await message.reply_audio(
                    item.media,
                    caption=item.caption,
                    parse_mode=item.parse_mode,
                    title=item.title,
                    performer=item.performer,
                    thumbnail=cast(FSInputFile | None, item.thumbnail),
                )
            else:
                await message.reply_media_group(media=cast(list[MediaUnion], media_group))

            if i + 10 < len(audio):
                await asyncio.sleep(5)

    @staticmethod
    async def _send_documents(message: Message, docs: list[Path], caption: str | None):
        for i in range(0, len(docs), 10):
            chunk = docs[i : i + 10]
            media_group = [InputMediaDocument(media=FSInputFile(d)) for d in chunk]

            chunk_caption = caption if i == 0 and caption else None
            if chunk_caption and media_group:
                media_group[0] = InputMediaDocument(
                    media=FSInputFile(chunk[0]), caption=chunk_caption, parse_mode="MarkdownV2"
                )

            await message.reply_media_group(media=cast(list[MediaUnion], media_group))

            if i + 10 < len(docs):
                await asyncio.sleep(5)


media_sender = MediaSender()
