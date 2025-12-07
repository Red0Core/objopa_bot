"""
Утилиты для работы с видео файлами и FFmpeg
"""

import asyncio
import subprocess
import time
import traceback
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

import aiofiles
import ujson as json

from core.logger import logger


@dataclass
class VideoInfo:
    """Информация о видео файле"""

    duration: float
    size_mb: float
    format_name: str
    has_faststart: bool
    bitrate: Optional[int] = None
    resolution: Optional[Tuple[int, int]] = None


@dataclass
class OptimizationConfig:
    """Конфигурация оптимизации видео"""

    max_size_mb: int = 50
    target_quality_crf: int = 23
    max_bitrate_factor: float = 1.2
    buffer_size_factor: float = 2.0
    small_file_threshold_mb: int = 10
    compression_preset: str = "fast"

    # Прогрессивные настройки качества
    quality_profiles = {
        "high": {"crf": 20, "preset": "slow"},
        "medium": {"crf": 23, "preset": "medium"},
        "fast": {"crf": 26, "preset": "fast"},
        "ultrafast": {"crf": 30, "preset": "ultrafast"},
    }


class VideoProcessor:
    """Класс для обработки видео файлов с FFmpeg"""

    def __init__(self):
        self.config = OptimizationConfig()
        self._video_info_cache: Dict[str, VideoInfo] = {}

    @staticmethod
    @lru_cache(maxsize=32)
    def _get_ffmpeg_command_base() -> list[str]:
        """Кэшированная базовая команда FFmpeg"""
        return ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

    async def get_video_info(self, video_path: Path, use_cache: bool = True) -> Optional[VideoInfo]:
        """
        Получает подробную информацию о видео файле.
        Результат кэшируется для повторного использования.
        """
        cache_key = f"{video_path}:{video_path.stat().st_mtime}"

        if use_cache and cache_key in self._video_info_cache:
            return self._video_info_cache[cache_key]

        try:
            # Команда для получения информации о видео
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(video_path)]

            # Используем asyncio.to_thread для Windows совместимости
            result = await asyncio.to_thread(lambda: self._run_ffprobe(cmd))

            if result is None:
                return None

            stdout, returncode = result
            data = json.loads(stdout)
            format_info = data.get("format", {})
            video_stream = None

            # Находим видео поток
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break

            if not video_stream:
                logger.warning(f"No video stream found in {video_path.name}")
                return None

            # Извлекаем информацию
            duration = float(format_info.get("duration", 0))
            size_mb = video_path.stat().st_size / (1024 * 1024)
            format_name = format_info.get("format_name", "").lower()

            # Получаем битрейт
            bitrate = None
            if "bit_rate" in format_info:
                bitrate = int(format_info["bit_rate"])
            elif "bit_rate" in video_stream:
                bitrate = int(video_stream["bit_rate"])

            # Получаем разрешение
            resolution = None
            if "width" in video_stream and "height" in video_stream:
                resolution = (int(video_stream["width"]), int(video_stream["height"]))

            # Проверяем faststart
            has_faststart = await self.check_faststart(video_path)

            video_info = VideoInfo(
                duration=duration,
                size_mb=size_mb,
                format_name=format_name,
                has_faststart=has_faststart,
                bitrate=bitrate,
                resolution=resolution,
            )

            # Кэшируем результат
            if use_cache:
                self._video_info_cache[cache_key] = video_info

            return video_info

        except FileNotFoundError:
            logger.error("ffprobe command not found. Please install FFmpeg/ffprobe")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse ffprobe output for {video_path.name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting video info for {video_path}: {type(e).__name__}: {e}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            return None

    @staticmethod
    def _run_ffprobe(cmd: list[str], timeout: int = 30, strip_output: bool = False) -> Optional[tuple[str, int] | str]:
        """Вспомогательный метод для запуска ffprobe в потоке"""
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if process.returncode == 0:
                output = process.stdout.strip() if strip_output else process.stdout
                return output if strip_output else (output, process.returncode)
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"ffprobe timeout for command: {cmd}")
            return None
        except Exception as e:
            logger.error(f"Error running ffprobe: {e}")
            return None

    async def check_faststart(self, video_path: Path) -> bool:
        """
        Проверяет, включен ли faststart для видео файла.
        Использует более точный метод проверки структуры MP4.
        """
        try:
            # Простая эвристика: если файл небольшой (< 10MB),
            # считаем что faststart не критичен
            file_size = video_path.stat().st_size
            if file_size < self.config.small_file_threshold_mb * 1024 * 1024:
                return True

            # Проверяем формат файла
            format_cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "format=format_name",
                "-of",
                "csv=p=0",
                str(video_path),
            ]

            # Используем asyncio.to_thread для Windows совместимости
            result = await asyncio.to_thread(lambda: self._run_ffprobe(format_cmd, timeout=10, strip_output=True))
            if not result:
                return False
            format_name = result.lower()

            # Faststart применим только к MP4 контейнерам
            if "mp4" not in format_name and "mov" not in format_name:
                return True

            # Проверяем позицию moov atom через qtfaststart logic
            # Читаем начало файла и ищем структуру атомов
            try:
                async with aiofiles.open(video_path, "rb") as f:
                    # Читаем первые 64 байта для поиска ftyp и moov атомов
                    header = await f.read(64)

                    # Ищем сигнатуры MP4 атомов
                    if b"ftyp" in header and b"moov" in header:
                        # Если moov находится в начале (первые 64 байта), то faststart уже включен
                        return True

                    # Читаем больше данных для более точной проверки
                    await f.seek(0)
                    larger_header = await f.read(1024)

                    # Если moov не в первом килобайте, то нужен faststart
                    return b"moov" in larger_header

            except (IOError, OSError) as e:
                logger.warning(f"Could not read file {video_path.name}: {e}")
                return True

        except Exception as e:
            logger.error(f"Error checking faststart for {video_path}: {e}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            return True

    async def convert_to_faststart(
        self, input_path: Path, output_path: Optional[Path] = None
    ) -> Tuple[bool, Optional[Path], Optional[str]]:
        """
        Преобразует видео в faststart формат.

        Returns:
            Tuple[success, output_file_path, error_message]
        """
        try:
            if output_path is None:
                # Создаем временное имя файла
                output_path = input_path.parent / f"{input_path.stem}_faststart{input_path.suffix}"

            # Команда для преобразования в faststart
            cmd = self._get_ffmpeg_command_base().copy()
            cmd.extend(
                [
                    "-i",
                    str(input_path),
                    "-c",
                    "copy",  # Копируем потоки без перекодирования
                    "-movflags",
                    "faststart",  # Включаем faststart
                    "-y",  # Перезаписываем выходной файл если существует
                    str(output_path),
                ]
            )

            logger.info(f"Converting {input_path.name} to faststart format...")

            # Используем asyncio.to_thread для Windows совместимости
            success, error = await asyncio.to_thread(lambda: self._run_ffmpeg(cmd))

            if success:
                logger.success(f"Successfully converted {input_path.name} to faststart")
                return True, output_path, None
            else:
                logger.error(f"FFmpeg conversion failed: {error}")
                return False, None, f"FFmpeg error: {error}"

        except Exception as e:
            error_msg = f"Error during video conversion: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg

    @staticmethod
    def _run_ffmpeg(cmd: list[str], timeout: int = 300) -> Tuple[bool, Optional[str]]:
        """Вспомогательный метод для запуска ffmpeg в потоке"""
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if process.returncode == 0:
                return True, None
            return False, process.stderr
        except subprocess.TimeoutExpired:
            return False, f"FFmpeg operation timed out after {timeout}s"
        except FileNotFoundError:
            return False, "FFmpeg not found. Please install FFmpeg."
        except Exception as e:
            return False, str(e)

    async def optimize_video_for_telegram(
        self, video_path: Path, max_size_mb: Optional[int] = None, quality_profile: str = "medium"
    ) -> Tuple[bool, Optional[Path], Optional[str]]:
        """
        Оптимизирует видео для отправки в Telegram с интеллектуальной адаптацией качества.
        """
        if max_size_mb is None:
            max_size_mb = self.config.max_size_mb

        try:
            # Получаем информацию о видео
            video_info = await self.get_video_info(video_path)
            if not video_info:
                return False, None, "Could not analyze video file"

            logger.info(
                f"Analyzing {video_path.name}: {video_info.size_mb:.1f}MB, "
                f"{video_info.duration:.1f}s, faststart: {video_info.has_faststart}"
            )

            # Определяем нужны ли изменения
            needs_faststart = not video_info.has_faststart and "mp4" in video_info.format_name
            needs_compression = video_info.size_mb > max_size_mb

            if not needs_faststart and not needs_compression:
                logger.info(f"Video {video_path.name} is already optimized")
                return True, video_path, None

            # Выбираем профиль качества на основе размера файла
            if video_info.size_mb > max_size_mb * 2:
                quality_profile = "fast"
            elif video_info.size_mb > max_size_mb * 1.5:
                quality_profile = "medium"
            else:
                quality_profile = "high"

            # Создаем команду оптимизации
            output_path = video_path.parent / f"{video_path.stem}_optimized{video_path.suffix}"
            cmd = self._get_ffmpeg_command_base().copy()
            cmd.extend(["-i", str(video_path)])

            if needs_compression:
                # Рассчитываем целевой битрейт
                target_bitrate_kbps = await self._calculate_target_bitrate(video_info, max_size_mb)

                profile = self.config.quality_profiles[quality_profile]
                cmd.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-preset",
                        profile["preset"],
                        "-crf",
                        str(profile["crf"]),
                        "-b:v",
                        f"{target_bitrate_kbps}k",
                        "-maxrate",
                        f"{int(target_bitrate_kbps * self.config.max_bitrate_factor)}k",
                        "-bufsize",
                        f"{int(target_bitrate_kbps * self.config.buffer_size_factor)}k",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                    ]
                )
                logger.info(f"Compressing with {quality_profile} profile, target bitrate: {target_bitrate_kbps}k")
            else:
                # Только копируем потоки
                cmd.extend(["-c", "copy"])

            # Добавляем faststart если нужно
            if needs_faststart:
                cmd.extend(["-movflags", "faststart"])

            cmd.extend(["-y", str(output_path)])

            # Выполняем оптимизацию с таймаутом
            start_time = time.time()
            timeout = min(300, max(60, video_info.duration * 2))  # Адаптивный таймаут

            logger.info(f"Starting optimization with {timeout}s timeout...")

            # Используем asyncio.to_thread для Windows совместимости
            success, error = await asyncio.to_thread(lambda: self._run_ffmpeg(cmd, int(timeout)))

            processing_time = time.time() - start_time

            if success:
                new_size_mb = output_path.stat().st_size / (1024 * 1024)
                compression_ratio = (video_info.size_mb - new_size_mb) / video_info.size_mb * 100

                logger.success(
                    f"Video optimized in {processing_time:.1f}s: "
                    f"{video_info.size_mb:.1f}MB -> {new_size_mb:.1f}MB "
                    f"({compression_ratio:.1f}% reduction)"
                )

                warning = None
                if new_size_mb > max_size_mb:
                    warning = f"Optimized video is still {new_size_mb:.1f}MB (limit: {max_size_mb}MB)"

                return True, output_path, warning
            else:
                logger.error(f"Video optimization failed: {error}")
                return False, None, f"FFmpeg error: {error}"

        except Exception as e:
            error_msg = f"Error during video optimization: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg

    async def _calculate_target_bitrate(self, video_info: VideoInfo, max_size_mb: int) -> int:
        """Рассчитывает оптимальный битрейт для достижения целевого размера"""
        if video_info.duration <= 0:
            return 1000  # Fallback битрейт

        # Оставляем место для аудио (примерно 128k)
        audio_size_mb = (128 * video_info.duration) / (8 * 1024)
        video_target_mb = max_size_mb * 0.9 - audio_size_mb  # 90% от лимита минус аудио

        # Рассчитываем битрейт: (размер в MB * 8 * 1024) / длительность в секундах
        target_bitrate_kbps = int((video_target_mb * 8 * 1024) / video_info.duration)

        # Ограничиваем разумными пределами
        target_bitrate_kbps = max(200, min(target_bitrate_kbps, 5000))

        return target_bitrate_kbps

    async def optimize_multiple_videos(
        self, video_paths: list[Path], max_concurrent: int = 2
    ) -> list[Tuple[Path, bool, Optional[Path], Optional[str]]]:
        """
        Оптимизирует несколько видео параллельно с ограничением на количество одновременных операций.
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def optimize_single(video_path: Path):
            async with semaphore:
                success, optimized_path, error = await self.optimize_video_for_telegram(video_path)
                return video_path, success, optimized_path, error

        tasks = [optimize_single(path) for path in video_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Обрабатываем исключения
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                processed_results.append((Path(), False, None, str(result)))
            else:
                processed_results.append(result)

        return processed_results

    def get_optimization_stats(self) -> dict:
        """Возвращает статистику оптимизации за текущую сессию"""
        return {
            "cache_size": len(self._video_info_cache),
            "config": {
                "max_size_mb": self.config.max_size_mb,
                "small_file_threshold": self.config.small_file_threshold_mb,
                "compression_preset": self.config.compression_preset,
            },
            "quality_profiles": list(self.config.quality_profiles.keys()),
        }

    def clear_cache(self):
        """Очищает кэш информации о видео"""
        self._video_info_cache.clear()
        logger.info("Video info cache cleared")

    @staticmethod
    def cleanup_temp_files(original_path: Path, optimized_path: Path):
        """Очищает временные файлы после успешной отправки"""
        try:
            if optimized_path != original_path and optimized_path.exists():
                optimized_path.unlink()
                logger.debug(f"Cleaned up temporary file: {optimized_path.name}")
        except Exception as e:
            logger.warning(f"Could not clean up temporary file {optimized_path}: {e}")


# Глобальный экземпляр процессора
video_processor = VideoProcessor()
