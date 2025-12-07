"""
Фабрики для создания генераторов изображений и видео.
Позволяет отделить логику интеграции с внешними сервисами от основного кода.
"""

import asyncio
import random
import string
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from core.config import BASE_DIR
from core.logger import logger

# Создаем директорию для демо-изображений, если она не существует
DEMO_STORAGE_DIR = BASE_DIR / "storage" / "demo_images"
DEMO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


class ImageGenerator(ABC):
    @abstractmethod
    async def generate(
        self,
        prompts: list[str],
        indices_to_generate: list[int] | None = None,  # Новый аргумент: 0-based индексы
    ) -> list[list[Path]]:
        """
        Генерирует изображения для указанных промптов.
        Если indices_to_generate предоставлен, генерирует ТОЛЬКО для промптов с этими индексами.
        Возвращаемый список ДОЛЖЕН соответствовать длине prompts,
        содержа пустые списки для индексов, которые не генерировались в этом вызове.
        """
        pass


class AnimationsGenerator(ABC):
    """Абстрактный класс для генераторов анимаций"""

    @abstractmethod
    async def generate(self, images: list[Path], prompts: list[str]) -> Path:
        """
        Создает анимаций на основе изображений и промптов.

        Args:
            images: Список путей к изображениям
            prompts: Список текстовых промптов для анимации

        Returns:
            Путь к созданным видеофайлам(папки)
        """
        pass


class VideoGenerator(ABC):
    """Абстрактный класс для генераторов видео"""

    @abstractmethod
    async def generate(self, videos: list[Path], prompts: list[str]) -> Path:
        """
        Создает видео на основе списка анимаций.

        Args:
            videos: Список путей к анимациям
            prompts: Список текстовых промптов для видео

        Returns:
            Путь к созданному видеофайлу
        """
        pass


class DummyImageGenerator(ImageGenerator):
    """
    Демонстрационная реализация генератора изображений с использованием dummyimage.com.
    Генерирует реальные изображения для каждого промпта.
    """

    async def generate(self, prompts: list[str], indices_to_generate: list[int] | None = None) -> list[list[Path]]:
        """
        Возвращает реальные изображения, сгенерированные через dummyimage.com

        Args:
            prompts: Список текстовых промптов
            indices_to_generate: Список индексов для генерации изображений (0-based)

        Returns:
            Список групп изображений
        """
        logger.info("Используется DummyImageGenerator с dummyimage.com")
        result = []

        for idx, prompt in enumerate(prompts):
            # Создаем случайные параметры для изображений
            group_images = []
            image_generation_tasks = []
            if indices_to_generate is not None and idx not in indices_to_generate:
                continue  # Пропускаем, если индекс не в списке для генерации
            # Создаем 4 изображения для каждого промпта
            for i in range(4):
                # Добавляем задачу на генерацию изображения
                task = self._generate_single_image(prompt, idx, i)
                image_generation_tasks.append(task)

            # Ожидаем завершения всех задач генерации для этой группы
            group_images = await asyncio.gather(*image_generation_tasks)
            result.append(group_images)

        return result

    async def _generate_single_image(self, prompt: str, group_idx: int, img_idx: int) -> Path:
        """
        Генерирует одно изображение через dummyimage.com

        Args:
            prompt: Текстовый промпт
            group_idx: Индекс группы
            img_idx: Индекс изображения в группе

        Returns:
            Путь к сгенерированному изображению
        """
        # Создаем различные размеры для разнообразия
        width, height = self._get_random_dimensions()

        # Получаем случайные цвета для фона и текста
        bg_color, text_color = self._get_random_colors()

        # Формируем URL для запроса изображения
        # Ограничиваем текст промпта до 20 символов
        text = prompt[:20].replace(" ", "+")
        image_url = f"https://dummyimage.com/{width}x{height}/{bg_color}/{text_color}&text={text}+{img_idx + 1}"

        # Создаем уникальное имя файла
        random_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        file_name = f"scene_{group_idx + 1}_img_{img_idx + 1}_{random_str}.png"
        file_path = DEMO_STORAGE_DIR / file_name

        # Скачиваем изображение
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(image_url)
                response.raise_for_status()

                # Сохраняем изображение
                with open(file_path, "wb") as f:
                    f.write(response.content)

                logger.info(f"Изображение успешно сгенерировано: {file_path}")
                return file_path

        except Exception as e:
            logger.error(f"Ошибка при генерации изображения: {e}")
            # В случае ошибки возвращаем путь по умолчанию
            return DEMO_STORAGE_DIR / f"default_scene_{group_idx + 1}_img_{img_idx + 1}.png"

    def _get_random_dimensions(self) -> tuple[int, int]:
        """Возвращает случайные размеры изображения"""
        dimensions = [
            (600, 400),  # Стандартный размер
            (800, 600),  # Больший размер
            (400, 400),  # Квадратный
            (700, 500),  # Альтернативный
        ]
        return random.choice(dimensions)

    def _get_random_colors(self) -> tuple[str, str]:
        """Возвращает случайные цвета для фона и текста"""
        colors = [
            ("000000", "ffffff"),  # Черный фон, белый текст
            ("ffffff", "000000"),  # Белый фон, черный текст
            ("3498db", "ffffff"),  # Синий фон, белый текст
            ("2ecc71", "ffffff"),  # Зеленый фон, белый текст
            ("e74c3c", "ffffff"),  # Красный фон, белый текст
            ("f39c12", "000000"),  # Желтый фон, черный текст
        ]
        return random.choice(colors)


class DemoAnimationsGenerator(AnimationsGenerator):
    """Демонстрационная реализация генератора анимаций"""

    async def generate(self, images: list[Path], prompts: list[str]) -> Path:
        """Возвращает тестовый путь к анимациям"""
        logger.info("Используется демо-генератор анимаций")
        logger.info(f"Виртуальное создание анимаций из {len(images)} изображений с {len(prompts)} промптами")

        return DEMO_STORAGE_DIR


class DemoVideoGenerator(VideoGenerator):
    """Демонстрационная реализация генератора видео"""

    async def generate(self, videos: list[Path], prompts: list[str]) -> Path:
        """Возвращает тестовый путь к видео"""
        logger.info("Используется демо-генератор видео")
        logger.info(f"Виртуальное создание видео из {len(videos)} видеофайлов")

        return DEMO_STORAGE_DIR / "demo_video.mp4"


class GeneratorFactory:
    """
    Фабрика для создания генераторов изображений и видео.
    """

    @staticmethod
    def create_image_generator() -> ImageGenerator:
        """
        Создает и возвращает генератор изображений.
        Пытается использовать реальную реализацию, если доступна.
        """
        try:
            # Пытаемся импортировать реальную реализацию
            from workers.private_generators import RealImageGenerator

            logger.info("Используется реальный генератор изображений")
            return RealImageGenerator()
        except ImportError:
            # Если не удалось, используем демо-реализацию с dummyimage.com
            logger.warning("Реальный генератор изображений не найден, используется DummyImageGenerator")
            return DummyImageGenerator()

    @staticmethod
    def create_animations_generator() -> AnimationsGenerator:
        """
        Создает и возвращает генератор анимаций.
        Пытается использовать реальную реализацию, если доступна.
        """
        try:
            # Пытаемся импортировать реальную реализацию
            from workers.private_generators import RealAnimationsGenerator

            logger.info("Используется реальный генератор анимаций")
            return RealAnimationsGenerator()
        except ImportError:
            # Если не удалось, используем демо-реализацию
            logger.warning("Реальный генератор анимаций не найден, используется демо-версия")
            return DemoAnimationsGenerator()

    @staticmethod
    def create_video_generator() -> VideoGenerator:
        """
        Создает и возвращает генератор видео.
        Пытается использовать реальную реализацию, если доступна.
        """
        try:
            # Пытаемся импортировать реальную реализацию
            from workers.private_generators import RealVideoGenerator

            logger.info("Используется реальный генератор видео")
            return RealVideoGenerator()
        except ImportError:
            # Если не удалось, используем демо-реализацию
            logger.warning("Реальный генератор видео не найден, используется демо-версия")
            return DemoVideoGenerator()
