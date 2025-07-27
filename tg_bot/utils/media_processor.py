"""
Универсальный процессор медиа файлов для Telegram бота
"""
import asyncio
from pathlib import Path
from typing import List, Optional, Union
from dataclasses import dataclass
from enum import Enum

from aiogram.types import (
    FSInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    MediaUnion,
    Message,
)
import telegramify_markdown

from core.logger import logger
from tg_bot.services.gpt import get_gpt_formatted_chunks
from tg_bot.utils.video_utils import video_processor


class MediaType(Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


@dataclass
class MediaFile:
    path: Path
    type: MediaType
    optimized_path: Optional[Path] = None


class MediaProcessor:
    """Универсальный процессор медиа файлов"""
    
    # Поддерживаемые расширения
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
    
    def __init__(self):
        self.chunk_size = 10
        self.chunk_delay = 5
    
    def categorize_files(self, file_paths: List[Path]) -> List[MediaFile]:
        """Категоризирует файлы по типам медиа"""
        media_files = []
        
        for file_path in file_paths:
            suffix = file_path.suffix.lower()
            
            if suffix in self.IMAGE_EXTENSIONS:
                media_type = MediaType.IMAGE
            elif suffix in self.VIDEO_EXTENSIONS:
                media_type = MediaType.VIDEO
            elif suffix in self.AUDIO_EXTENSIONS:
                media_type = MediaType.AUDIO
            else:
                media_type = MediaType.DOCUMENT
            
            media_files.append(MediaFile(path=file_path, type=media_type))
        
        return media_files
    
    async def process_videos(self, video_files: List[MediaFile], status_message: Optional[Message] = None) -> List[MediaFile]:
        """Оптимизирует видео файлы"""
        processed_videos = []
        
        for i, media_file in enumerate(video_files):
            if status_message and len(video_files) > 1:
                await status_message.edit_text(f"🔧 Обрабатываю видео {i+1}/{len(video_files)}...")
            
            try:
                success, optimized_path, error = await video_processor.optimize_video_for_telegram(media_file.path)
                if success and optimized_path:
                    media_file.optimized_path = optimized_path
                processed_videos.append(media_file)
                
            except Exception as e:
                logger.error(f"Error optimizing video {media_file.path.name}: {e}")
                # Добавляем оригинальный файл если оптимизация не удалась
                processed_videos.append(media_file)
        
        return processed_videos
    
    def split_caption(self, caption: str, max_length: int = 1024) -> List[str]:
        """Разбивает длинную подпись на части"""
        if not caption:
            return []
        
        if len(caption) <= max_length:
            return [caption]
        
        try:
            # Используем GPT форматирование для разбиения
            return get_gpt_formatted_chunks(caption)
        except Exception:
            # Fallback: простое разбиение
            chunks = []
            current_chunk = ""
            
            for line in caption.split('\n'):
                if len(current_chunk) + len(line) + 1 <= max_length:
                    current_chunk += line + '\n'
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = line + '\n'
            
            if current_chunk:
                chunks.append(current_chunk.strip())
            
            return chunks
    
    async def send_media_groups(
        self, 
        message: Message, 
        media_files: List[MediaFile], 
        caption_parts: List[str],
        use_optimization: bool = True
    ) -> bool:
        """Отправляет медиа группы в Telegram"""
        try:
            # Группируем файлы по типам
            images = [mf for mf in media_files if mf.type == MediaType.IMAGE]
            videos = [mf for mf in media_files if mf.type == MediaType.VIDEO]
            audio = [mf for mf in media_files if mf.type == MediaType.AUDIO]
            documents = [mf for mf in media_files if mf.type == MediaType.DOCUMENT]
            
            # Оптимизируем видео если нужно
            if use_optimization and videos:
                videos = await self.process_videos(videos)
            
            caption_index = 0
            
            # Отправляем видео по одному (для оптимизации)
            for video in videos:
                video_path = video.optimized_path or video.path
                video_caption = caption_parts[caption_index] if caption_index < len(caption_parts) else None
                
                await message.reply_video(FSInputFile(video_path), caption=video_caption)
                
                if video_caption:
                    caption_index += 1
                
                # Очищаем временные файлы
                if video.optimized_path and video.optimized_path != video.path:
                    video_processor.cleanup_temp_files(video.path, video.optimized_path)
            
            # Отправляем изображения группами
            if images:
                await self._send_image_chunks(
                    message, 
                    images, 
                    caption_parts[caption_index] if caption_index < len(caption_parts) and not videos else None
                )
                if images and not videos and caption_parts:
                    caption_index += 1
            
            # Отправляем аудио группами
            if audio:
                await self._send_audio_chunks(
                    message,
                    audio,
                    caption_parts[caption_index] if caption_index < len(caption_parts) and not videos and not images else None
                )
                if audio and not videos and not images and caption_parts:
                    caption_index += 1
            
            # Отправляем документы группами
            if documents:
                await self._send_document_chunks(
                    message,
                    documents,
                    caption_parts[caption_index] if caption_index < len(caption_parts) and not videos and not images and not audio else None
                )
                caption_index += 1
            
            # Отправляем оставшиеся части подписи
            for part in caption_parts[caption_index:]:
                await message.reply(telegramify_markdown.markdownify(part), parse_mode="MarkdownV2")
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending media groups: {e}")
            return False
    
    async def _send_image_chunks(self, message: Message, images: List[MediaFile], caption: Optional[str] = None):
        """Отправляет изображения группами по 10"""
        for i in range(0, len(images), self.chunk_size):
            chunk = images[i:i + self.chunk_size]
            media_group: List[MediaUnion] = [
                InputMediaPhoto(media=FSInputFile(img.path)) for img in chunk
            ]
            
            chunk_caption = caption if i == 0 else None
            await message.reply_media_group(media=media_group, caption=chunk_caption)
            
            if i + self.chunk_size < len(images):
                await asyncio.sleep(self.chunk_delay)
    
    async def _send_audio_chunks(self, message: Message, audio_files: List[MediaFile], caption: Optional[str] = None):
        """Отправляет аудио группами по 10"""
        for i in range(0, len(audio_files), self.chunk_size):
            chunk = audio_files[i:i + self.chunk_size]
            media_group: List[MediaUnion] = [
                InputMediaAudio(media=FSInputFile(audio.path)) for audio in chunk
            ]
            
            chunk_caption = caption if i == 0 else None
            await message.reply_media_group(media=media_group, caption=chunk_caption)
            
            if i + self.chunk_size < len(audio_files):
                await asyncio.sleep(self.chunk_delay)
    
    async def _send_document_chunks(self, message: Message, documents: List[MediaFile], caption: Optional[str] = None):
        """Отправляет документы группами по 10"""
        for i in range(0, len(documents), self.chunk_size):
            chunk = documents[i:i + self.chunk_size]
            media_group: List[MediaUnion] = [
                InputMediaDocument(media=FSInputFile(doc.path)) for doc in chunk
            ]
            
            chunk_caption = caption if i == 0 else None
            await message.reply_media_group(media=media_group, caption=chunk_caption)
            
            if i + self.chunk_size < len(documents):
                await asyncio.sleep(self.chunk_delay)
    
    async def send_single_media(
        self, 
        message: Message, 
        media_file: MediaFile, 
        caption: Optional[str] = None,
        use_optimization: bool = True
    ) -> bool:
        """Отправляет одиночный медиа файл"""
        try:
            file_path = media_file.path
            
            # Оптимизируем видео если нужно
            if use_optimization and media_file.type == MediaType.VIDEO:
                optimized_videos = await self.process_videos([media_file])
                if optimized_videos:
                    file_path = optimized_videos[0].optimized_path or media_file.path
            
            input_file = FSInputFile(file_path)
            
            if media_file.type == MediaType.IMAGE:
                await message.reply_photo(input_file, caption=caption)
            elif media_file.type == MediaType.VIDEO:
                await message.reply_video(input_file, caption=caption)
                # Очищаем временные файлы
                if file_path != media_file.path:
                    video_processor.cleanup_temp_files(media_file.path, file_path)
            elif media_file.type == MediaType.AUDIO:
                await message.reply_audio(input_file, caption=caption)
            else:
                await message.reply_document(input_file, caption=caption)
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending single media {media_file.path.name}: {e}")
            return False
    
    async def process_and_send(
        self, 
        message: Message, 
        file_paths: List[Path], 
        caption: Optional[str] = None,
        use_optimization: bool = True
    ) -> bool:
        """Основной метод: обрабатывает и отправляет медиа файлы"""
        if not file_paths:
            return False
        
        try:
            # Категоризируем файлы
            media_files = self.categorize_files(file_paths)
            caption_parts = self.split_caption(caption) if caption else []
            
            # Если один файл - отправляем как единичное медиа
            if len(media_files) == 1:
                return await self.send_single_media(
                    message, 
                    media_files[0], 
                    caption_parts[0] if caption_parts else None,
                    use_optimization
                )
            
            # Если несколько файлов - отправляем группами
            return await self.send_media_groups(
                message, 
                media_files, 
                caption_parts,
                use_optimization
            )
            
        except Exception as e:
            logger.error(f"Error processing and sending media: {e}")
            return False


# Глобальный экземпляр процессора
media_processor = MediaProcessor()
