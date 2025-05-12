from pathlib import Path
import zipfile
from core.config import BACKEND_ROUTE, BASE_DIR
from core.logger import logger
from workers.base_pipeline import BasePipeline
from workers.generator_factory import GeneratorFactory
from workers.utils import send_notification, upload_file_to_backend
from workers.worker_status_manager import WorkerStatusManager

def create_zip_archive(files_to_archive: list[Path], output_zip_path: Path) -> bool:
    """
    Создает ZIP-архив из указанных файлов и директорий.

    Args:
        files_to_archive: Список объектов Path, указывающих на файлы или директории для архивации.
        output_zip_path: Объект Path, указывающий, где сохранить созданный ZIP-архив.

    Returns:
        True, если архив успешно создан, иначе False.
    """
    try:
        # Убедимся, что родительская директория для выходного архива существует
        output_zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for item_path in files_to_archive:
                if not item_path.exists():
                    logger.warning(f"Предупреждение: Путь не существует и будет пропущен: {item_path}")
                    continue

                if item_path.is_file():
                    # Добавляем файл. arcname определяет имя файла внутри архива.
                    # Используем item_path.name, чтобы сохранить только имя файла, а не полный путь.
                    zipf.write(item_path, arcname=item_path.name)
                    logger.info(f"Добавлен файл: {item_path} как {item_path.name}")
                elif item_path.is_dir():
                    # Добавляем содержимое директории рекурсивно
                    logger.info(f"Добавление директории: {item_path}")
                    for file_in_dir in item_path.rglob('*'): # rglob('*') находит все файлы и поддиректории
                        if file_in_dir.is_file():
                            # arcname здесь важен для сохранения структуры директорий внутри архива.
                            # file_in_dir.relative_to(item_path.parent) создаст относительный путь
                            # от родителя item_path, чтобы включить саму item_path в структуру.
                            # Если вы хотите, чтобы содержимое item_path было в корне архива,
                            # используйте file_in_dir.relative_to(item_path)
                            archive_path = file_in_dir.relative_to(item_path.parent)
                            zipf.write(file_in_dir, arcname=archive_path)
                            logger.info(f"  Добавлен файл из директории: {file_in_dir} как {archive_path}")
                        # Можно добавить обработку пустых директорий, если это необходимо,
                        # но обычно zipfile не добавляет пустые директории явно,
                        # они создаются при добавлении файлов в них.
        logger.success(f"Архив успешно создан: {output_zip_path}")
        return True
    except FileNotFoundError as e:
        logger.error(f"Ошибка: Файл или директория не найдены во время архивации - {e}")
        return False
    except Exception as e:
        logger.error(f"Произошла ошибка при создании архива {output_zip_path}: {e}")
        return False

class ConcatAnimationsPipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.worker_status_manager = WorkerStatusManager(params.get("worker_id", "default_id"))
        self.created_at = params.get("created_at")
        data = params.get("data", {})
        self.user_id = data.get("user_id")
        self.animation_prompts = data.get("animation_prompts", [])

        self.video_generator = GeneratorFactory.create_video_generator()

    async def run(self) -> None:
        """
        Запуск пайплайна конкатенации анимаций.
        """
        if not await self.worker_status_manager.check_worker_animations_ready_flag(self.worker_status_manager.worker_id):
            logger.error("No animations ready for concatenation.")
            await send_notification(
                f"Нет готовых анимаций для конкатенации.",
                str(self.user_id)
            )
            raise ValueError("No animations ready for concatenation.")

        video_path = await self.video_generator.generate(videos=[], prompts=self.animation_prompts)
        video_server_path = await upload_file_to_backend(video_path, is_video=True)
        video_server_path = f"{BACKEND_ROUTE}/worker/download-video/{video_server_path}"
        logger.info(f"Ссылка на видео: {video_server_path}")
        output = f"Ссылка на видео: {video_server_path}\n"

        # Создаем ZIP-архив
        archive_path = BASE_DIR / f"{self.task_id}.zip"
        paths_to_archive = self.get_paths_to_archive()
        archive_server_path = ""
        if not create_zip_archive(paths_to_archive, archive_path):
            logger.error("Не удалось создать ZIP-архив.")
            output += "Не удалось создать ZIP-архив.\n"
        else:
            logger.info(f"ZIP-архив успешно создан: {archive_path}")
            archive_server_path = await upload_file_to_backend(archive_path, is_archive=True)
            archive_server_path = f"{BACKEND_ROUTE}/worker/download-archive/{archive_server_path}"
            logger.info(f"Ссылка на архив: {archive_server_path}")
            output += f"Ссылка на архив: {archive_server_path}\n"

        # Отправляем уведомление пользователю
        await send_notification(
            f"Конкатенация анимаций завершена.\n{output}",
            str(self.user_id)
        )
    
    def get_paths_to_archive(self) -> list[Path]:
        """
        Получить пути к файлам и директориям для архивации.

        Returns:
            Список объектов Path, указывающих на файлы и директории для архивации.
        """
        paths = [
            BASE_DIR / "animations",
            BASE_DIR / "sounds",
            BASE_DIR / "final_video.mp4",
            BASE_DIR / "final_with_sounds.mp4",
            BASE_DIR / "merged_video.mp4",
        ]
        return paths