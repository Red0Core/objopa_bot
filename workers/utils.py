import asyncio
from pathlib import Path

from httpx import AsyncClient, HTTPStatusError, NetworkError, TimeoutException

from core.config import BACKEND_ROUTE
from core.logger import logger


async def send_notification(text: str, send_to: str) -> None:
    try:
        async with AsyncClient() as session:
            session.headers.update({"accept": "application/json", "Content-Type": "application/json"})
            req = await session.post(f"{BACKEND_ROUTE}/notify", json={"text": text, "send_to": send_to})
            req.raise_for_status()
        logger.info(f"Уведомление отправлено: {text} для {send_to}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления: {e}")


async def upload_file_to_backend(
    file_path: Path, backend_url: str = BACKEND_ROUTE, is_video=False, is_archive=False
) -> str:
    """
    Загружает файл на сервер с повторными попытками и возвращает путь к нему.

    Args:
        file_path: Путь к локальному файлу для загрузки
        backend_url: Базовый URL бэкенда
        is_video: Флаг, указывающий, является ли файл видео
        is_archive: Флаг, указывающий, является ли файл архивом

    Returns:
        Путь к файлу на сервере или URL для скачивания (если это архив).

    Raises:
        FileNotFoundError: Если исходный файл не найден.
        RuntimeError: Если загрузка не удалась после всех попыток.
    """
    max_retries = 3
    base_delay = 1  # Секунды для первой задержки

    # Проверяем существование файла один раз перед циклом
    if not file_path.exists():
        logger.error(f"Файл не найден перед началом загрузки: {file_path}")
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    last_exception = None  # Храним последнюю ошибку

    for attempt in range(max_retries):
        try:
            # Открываем файл в двоичном режиме для каждой попытки,
            # так как file_data может быть прочитан в предыдущей неудачной попытке
            with open(file_path, "rb") as file_data:
                file_name = file_path.name
                files = {"file": (file_name, file_data, "application/octet-stream")}

                async with AsyncClient(timeout=60.0) as client:
                    logger.debug(f"Попытка {attempt + 1}/{max_retries}: Загрузка файла {file_name}...")
                    response = await client.post(
                        f"{backend_url}/worker/upload{'-video' if is_video else ''}{'-archive' if is_archive else ''}",
                        files=files,
                    )

                    # Проверяем статус ответа - ошибки 4xx не повторяем, 5xx повторяем
                    response.raise_for_status()

                    result = response.json()
                    filename_on_backend = result["filepath"]
                    logger.info(
                        f"Файл {file_name} успешно загружен сек (попытка {attempt + 1}). Backend: {filename_on_backend}"
                    )
                    return filename_on_backend

        # --- Обработка ошибок, при которых стоит повторить ---
        except (TimeoutException, NetworkError) as e:
            last_exception = e
            logger.warning(f"Попытка {attempt + 1}/{max_retries} не удалась (сетевая ошибка/таймаут): {e}")
        except HTTPStatusError as e:
            last_exception = e
            # Повторяем только при серверных ошибках (5xx)
            if 500 <= e.response.status_code < 600:
                logger.warning(
                    f"Попытка {attempt + 1}/{max_retries} не удалась (ошибка сервера {e.response.status_code}): {e.response.text}"
                )
            else:
                # Клиентские ошибки (4xx) или другие HTTP ошибки - не повторяем
                logger.error(
                    f"Ошибка HTTP {e.response.status_code} при загрузке файла (не будет повторена): {e.response.text}"
                )
                raise RuntimeError(f"Ошибка при загрузке файла: {e.response.status_code}") from e
        # --- Обработка других ошибок, которые не повторяем ---
        except Exception as e:
            # Ловим остальные непредвиденные ошибки - не повторяем
            logger.exception(
                f"Непредвиденная ошибка при загрузке файла на попытке {attempt + 1} (не будет повторена): {e}"
            )
            raise RuntimeError(f"Не удалось загрузить файл: {str(e)}") from e

        # Если это не последняя попытка, ждем перед следующей
        if attempt < max_retries - 1:
            delay = base_delay * (2**attempt)  # Экспоненциальная задержка (1, 2, 4, ...)
            logger.info(f"Ожидание {delay} сек перед следующей попыткой...")
            await asyncio.sleep(delay)

    # Если цикл завершился без return, значит все попытки не удались
    logger.error(f"Не удалось загрузить файл {file_path.name} после {max_retries} попыток.")
    raise RuntimeError(f"Не удалось загрузить файл {file_path.name} после {max_retries} попыток") from last_exception
