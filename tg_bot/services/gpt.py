import asyncio
import mimetypes
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import openai
import telegramify_markdown
from google import genai
from google.genai import types
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

from core.logger import logger


def split_text_smart(text: str, max_length: int) -> list[str]:
    """
    Разделяет текст на части, не превышающие max_length символов.
    Пытается сохранить целостность слов и предложений.

    Args:
        text: Текст для разделения
        max_length: Максимальная длина одной части

    Returns:
        Список частей текста
    """
    if not text or max_length <= 0:
        return []

    if len(text) <= max_length:
        return [text]

    parts = []
    current_pos = 0
    text_length = len(text)

    while current_pos < text_length:
        # Определяем границы текущего фрагмента
        end_pos = min(current_pos + max_length, text_length)

        # Если это последний фрагмент, добавляем весь остаток
        if end_pos == text_length:
            parts.append(text[current_pos:])
            break

        # Ищем оптимальное место для разрыва
        fragment = text[current_pos:end_pos]
        split_position = _find_best_split_position(fragment)

        if split_position > 0:
            # Найдено хорошее место для разрыва
            parts.append(text[current_pos : current_pos + split_position])
            current_pos += split_position
        else:
            # Принудительный разрыв по максимальной длине
            parts.append(text[current_pos:end_pos])
            current_pos = end_pos

    return [part for part in parts if part.strip()]


def _find_best_split_position(text: str) -> int:
    """
    Находит лучшую позицию для разрыва текста.
    Приоритет: перенос строки > точка + пробел > пробел

    Returns:
        Позиция для разрыва (0 если не найдена)
    """
    # Ищем перенос строки
    newline_pos = text.rfind("\n")
    if newline_pos != -1:
        return newline_pos + 1

    # Ищем точку с последующим пробелом (конец предложения)
    for i in range(len(text) - 2, -1, -1):
        if text[i] == "." and i + 1 < len(text) and text[i + 1] == " ":
            return i + 2

    # Ищем просто пробел
    space_pos = text.rfind(" ")
    if space_pos != -1:
        return space_pos + 1

    return 0


def split_message_by_paragraphs(text: str, max_length: int = 4096) -> list[str]:
    """
    Разбивает текст на части по абзацам с учетом максимальной длины.

    Args:
        text: Исходный текст
        max_length: Максимальная длина одной части

    Returns:
        Список частей текста, готовых для отправки
    """
    if not text:
        return []

    # Разделяем на абзацы
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        # Если абзац слишком длинный, разбиваем его
        if len(paragraph) > max_length:
            # Сохраняем накопленный чанк
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            # Разбиваем длинный абзац и добавляем части
            paragraph_parts = split_text_smart(paragraph, max_length)
            chunks.extend(paragraph_parts)
            continue

        # Проверяем, поместится ли абзац в текущий чанк
        separator = "\n\n" if current_chunk else ""
        potential_chunk = current_chunk + separator + paragraph

        if len(potential_chunk) <= max_length:
            # Помещается - добавляем к текущему чанку
            current_chunk = potential_chunk
        else:
            # Не помещается - сохраняем текущий чанк и начинаем новый
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = paragraph

    # Добавляем последний чанк
    if current_chunk:
        chunks.append(current_chunk.strip())

    return [chunk for chunk in chunks if chunk]


def get_gpt_formatted_chunks(text: str, max_length: int = 4096) -> list[str]:
    """
    Форматирует текст для Telegram и разбивает на части.

    Args:
        text: Исходный текст
        max_length: Максимальная длина одной части

    Returns:
        Список отформатированных частей текста
    """
    if not text:
        return []

    try:
        # Применяем форматирование Telegram
        formatted_text = telegramify_markdown.markdownify(text)
    except Exception as e:
        logger.warning(f"Ошибка форматирования markdown: {e}")
        formatted_text = text

    # Разбиваем на части
    return split_message_by_paragraphs(formatted_text, max_length)


# Исключения
class AIModelError(Exception):
    """Базовый класс для ошибок модели ИИ."""

    pass


class APIKeyError(AIModelError):
    """Ошибка неверного или отсутствующего API-ключа."""

    pass


class QuotaExceededError(AIModelError):
    """Ошибка превышения квоты API."""

    pass


class RateLimitError(AIModelError):
    """Ошибка превышения лимита запросов."""

    pass


class UnexpectedResponseError(AIModelError):
    """Ошибка при неожиданном ответе от API."""

    pass


class ModelOverloadedError(AIModelError):
    """Ошибка перегрузки модели."""

    pass


# Интерфейсы
class AIModelInterface(ABC):
    """Интерфейс для моделей ИИ."""

    @abstractmethod
    async def get_response(self, prompt: str, system_prompt: str = "") -> str:
        """Получить ответ от модели ИИ."""
        pass


class AIChatInterface(ABC):
    """Интерфейс для чат-моделей ИИ."""

    @abstractmethod
    def new_chat(self, system_prompt: str = "") -> None:
        """Создать новый чат."""
        pass

    @abstractmethod
    async def send_message(self, prompt: str) -> str:
        """Отправить сообщение в чат."""
        pass


# Реализации моделей
class BaseOpenAIModel(AIModelInterface):
    """Базовый класс для моделей на основе OpenAI API."""

    def __init__(self, api_key: str, model: str, base_url: str):
        if not api_key:
            raise APIKeyError("API ключ не может быть пустым")

        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)

    def _prepare_messages(self, prompt: str, system_prompt: str = "") -> list:
        """Подготавливает сообщения для API."""
        messages = [{"role": "user", "content": prompt}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        return messages

    async def get_response(self, prompt: str, system_prompt: str = "") -> str:
        """Получает ответ от модели."""
        if not prompt.strip():
            return ""

        messages = self._prepare_messages(prompt, system_prompt)

        try:
            completion = await self.client.chat.completions.create(model=self.model, messages=messages)

            if not completion.choices:
                logger.warning("Получен пустой ответ от модели")
                return ""

            choice = completion.choices[0]

            # Логируем причину завершения
            if choice.finish_reason == "length":
                logger.warning("Ответ был обрезан из-за лимита токенов")
            elif choice.finish_reason == "stop":
                logger.debug("Ответ завершён корректно")

            content = choice.message.content or ""
            logger.debug(f"Получен ответ от {self.base_url}: {len(content)} символов")

            return content

        except openai.AuthenticationError as e:
            raise APIKeyError(f"Ошибка аутентификации для {self.base_url}") from e
        except openai.RateLimitError as e:
            raise RateLimitError(f"Превышен лимит запросов к {self.base_url}") from e
        except openai.APIConnectionError as e:
            raise AIModelError(f"Ошибка подключения к {self.base_url}: {e}") from e
        except openai.OpenAIError as e:
            raise UnexpectedResponseError(f"Неожиданная ошибка {self.base_url}: {e}") from e


class OpenAIModel(BaseOpenAIModel):
    """Модель OpenAI GPT."""

    def __init__(self, api_key: str, model: str = "gpt-4"):
        super().__init__(api_key, model, "https://api.openai.com/v1")

    def _prepare_messages(self, prompt: str, system_prompt: str = "") -> list[dict]:
        """OpenAI использует роль 'system' для системных промптов."""
        messages = [{"role": "user", "content": prompt}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        return messages


class OpenRouterModel(BaseOpenAIModel):
    """Модель через OpenRouter."""

    def __init__(self, api_key: str, model: str = "google/gemini-2.0-flash-exp:free"):
        super().__init__(api_key, model, "https://openrouter.ai/api/v1")


@dataclass
class GeminiFile:
    """Класс для представления файла в Gemini."""

    file_path: Path
    mime_type: str | None = None

    def __post_init__(self):
        if not self.file_path.exists():
            raise FileNotFoundError(f"Файл {self.file_path} не найден")

        if self.mime_type is None:
            # Пытаемся угадать MIME-тип на основе расширения файла
            guessed_mime, _ = mimetypes.guess_type(str(self.file_path))
            if guessed_mime:
                self.mime_type = guessed_mime
                logger.debug(f"Guessed MIME type for {self.file_path}: {self.mime_type}")
            else:
                # Специальные случаи для текстовых файлов без стандартных расширений,
                # или если угадать не удалось. Gemini может принимать некоторые text/* MIME-типы.
                if self.file_path.suffix.lower() == ".txt":
                    self.mime_type = "text/plain"
                elif self.file_path.suffix.lower() == ".md":
                    self.mime_type = "text/markdown"
                elif self.file_path.suffix.lower() == ".html":
                    self.mime_type = "text/html"
                elif self.file_path.suffix.lower() == ".xml":
                    self.mime_type = "text/xml"
                else:
                    logger.warning(
                        f"Could not reliably determine MIME type for {self.file_path}. Using 'application/octet-stream'."
                    )
                    self.mime_type = "application/octet-stream"  # Fallback to generic binary


async def wait_for_file_active(client: genai.Client, file_obj: types.File) -> types.File:
    """Ожидает, пока файл Gemini перейдет в состояние ACTIVE."""
    start_time = time.time()
    # Максимальное время ожидания для файла (например, 5 минут для больших видео)
    # Можно настроить в зависимости от ожидаемых размеров файлов.
    MAX_WAIT_TIME_SECONDS = 5 * 60
    POLLING_INTERVAL_SECONDS = 5  # Интервал между проверками

    logger.info(f"Ожидание активации файла Gemini: {file_obj.name} (URI: {file_obj.uri})")

    while file_obj.state.name == "PROCESSING":
        if time.time() - start_time > MAX_WAIT_TIME_SECONDS:
            logger.error(f"Время ожидания активации файла {file_obj.name} истекло.")
            raise TimeoutError(f"Файл {file_obj.name} не перешел в ACTIVE состояние за {MAX_WAIT_TIME_SECONDS} секунд.")

        await asyncio.sleep(POLLING_INTERVAL_SECONDS)
        file_obj = await client.aio.files.get(name=file_obj.name)  # Повторно получаем статус файла

    if file_obj.state.name != "ACTIVE":
        logger.error(f"Файл {file_obj.name} перешел в состояние: {file_obj.state.name}, но не ACTIVE.")
        raise ValueError(f"Файл {file_obj.name} не активен. Текущее состояние: {file_obj.state.name}")

    logger.info(f"Файл Gemini {file_obj.name} успешно активирован за {time.time() - start_time:.2f} секунд.")
    return file_obj


class GeminiModel(AIModelInterface):
    """Модель Google Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        if not api_key:
            raise APIKeyError("API ключ для Gemini не может быть пустым")

        self.api_key = api_key
        self.model = model
        self.client = genai.Client(api_key=api_key)
        self.files_to_upload: list[GeminiFile] = []

    def add_file(self, gemini_file: GeminiFile) -> None:
        """Добавляет файл для использования в запросах к Gemini."""
        self.files_to_upload.append(gemini_file)

    async def get_response(self, prompt: str, system_prompt: str = "") -> str:
        """Получает ответ от Gemini."""
        if not prompt.strip() and not self.files_to_upload:
            return ""

        # Список путей к локальным файлам, которые нужно удалить после попытки загрузки в Gemini API
        files_to_delete_locally: list[Path] = []

        try:
            contents: types.ContentListUnion = []
            if prompt.strip():
                contents.append(types.Part.from_text(text=prompt))

            config_params = {}

            google_search_tool = Tool(google_search=GoogleSearch())
            config_params["tools"] = [google_search_tool]

            if system_prompt:
                config_params["system_instruction"] = system_prompt

            if self.files_to_upload:
                uploaded_files = []
                for gemini_file in self.files_to_upload:
                    try:
                        # Загружаем файл, используя file_path и mime_type из объекта GeminiFile
                        # Передаем mime_type=None, если он 'application/octet-stream', чтобы Gemini сам определил
                        uploaded_file = await self.client.aio.files.upload(
                            file=gemini_file.file_path,
                            config=types.UploadFileConfig(
                                mime_type=gemini_file.mime_type
                                if gemini_file.mime_type != "application/octet-stream"
                                else None
                            ),
                        )
                        # Добавляем загруженный файл в список содержимого
                        uploaded_files.append(uploaded_file)
                        # Добавляем путь в список для удаления ТОЛЬКО после успешной загрузки в Gemini
                        files_to_delete_locally.append(gemini_file.file_path)
                    except Exception as upload_e:
                        logger.error(f"Ошибка загрузки файла {gemini_file.file_path} в Gemini File API: {upload_e}")
                        # Если загрузка не удалась, файл останется локально для возможной отладки
                        continue
                contents.extend(
                    await asyncio.gather(*[wait_for_file_active(self.client, file) for file in uploaded_files])
                )
                self.files_to_upload.clear()  # Очищаем список объектов GeminiFile

            # Выполняем запрос к Gemini API
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(**config_params),
            )

            if not self._is_valid_response(response):
                logger.warning("Получен некорректный ответ от Gemini")
                return ""

            return self._extract_response_text(response)

        except Exception as e:
            logger.error(f"Ошибка при запросе к Gemini: {e}")
            if "error" in str(e).lower() and "model is overloaded" in str(e).lower():
                raise ModelOverloadedError("Модель временно перегружена брадок.") from e
            raise UnexpectedResponseError(f"Ошибка Gemini: {e}") from e
        finally:
            # Удаляем локальные временные файлы, которые были успешно загружены в Gemini API
            for local_path in files_to_delete_locally:
                if local_path.exists():
                    local_path.unlink(missing_ok=True)
                    logger.info(
                        f"Deleted temporary local file after successful upload to Gemini File API: {local_path}"
                    )

    def _is_valid_response(self, response) -> bool:
        """Проверяет валидность ответа."""
        return (
            response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts
        )

    def _extract_response_text(self, response) -> str:
        """Извлекает текст из ответа."""
        result_parts = []

        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call:
                logger.debug(f"Gemini function_call: {part.function_call}")
            elif hasattr(part, "text") and part.text:
                result_parts.append(part.text)

        return "".join(result_parts)


class GeminiChatModel(AIChatInterface):
    """Чат-модель Google Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        if not api_key:
            raise APIKeyError("API ключ для Gemini Chat не может быть пустым")

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.chat = None
        self.files_to_upload: list[GeminiFile] = []

    def add_file(self, gemini_file: GeminiFile) -> None:
        """Добавляет файл для использования в следующем сообщении к Gemini Chat."""
        self.files_to_upload.append(gemini_file)

    def new_chat(self, system_prompt: str = "") -> None:
        """Создает новый чат."""
        try:
            google_search_tool = Tool(google_search=GoogleSearch())

            config_params = {
                "tools": [google_search_tool],
                "response_modalities": ["TEXT"],
            }

            if system_prompt:
                config_params["system_instruction"] = system_prompt

            self.chat = self.client.aio.chats.create(model=self.model, config=GenerateContentConfig(**config_params))

        except Exception as e:
            logger.error(f"Ошибка создания чата Gemini: {e}")
            raise AIModelError(f"Не удалось создать чат: {e}") from e

    async def send_message(self, prompt: str) -> str:
        """Отправляет сообщение в чат, включая добавленные файлы."""
        if not self.chat:
            raise AIModelError("Чат не инициализирован. Вызовите new_chat() сначала.")

        if not prompt.strip() and not self.files_to_upload:
            return ""

        # Список путей к локальным файлам, которые нужно удалить после попытки загрузки в Gemini API
        files_to_delete_locally: list[Path] = []

        try:
            message_parts: list[types.PartUnionDict] = []
            if prompt.strip():
                message_parts.append(types.Part.from_text(text=prompt))

            if self.files_to_upload:
                uploaded_files = []
                for gemini_file in self.files_to_upload:
                    try:
                        # Асинхронно загружаем файл, используя file_path и mime_type из объекта GeminiFile
                        uploaded_file = await self.client.aio.files.upload(
                            file=gemini_file.file_path,
                            config=types.UploadFileConfig(
                                mime_type=gemini_file.mime_type
                                if gemini_file.mime_type != "application/octet-stream"
                                else None
                            ),
                        )
                        uploaded_files.append(uploaded_file)
                        # Добавляем путь в список для удаления ТОЛЬКО после успешной загрузки в Gemini
                        files_to_delete_locally.append(gemini_file.file_path)
                    except Exception as upload_e:
                        logger.error(f"Ошибка загрузки файла {gemini_file.file_path} в Gemini File API: {upload_e}")
                        # Если загрузка не удалась, файл останется локально
                        continue

                message_parts.extend(
                    await asyncio.gather(*[wait_for_file_active(self.client, file) for file in uploaded_files])
                )

                self.files_to_upload.clear()  # Очищаем список объектов GeminiFile

            # Выполняем запрос к Gemini API
            response = await self.chat.send_message(message_parts)

            return response.text if response and response.text else ""

        except Exception as e:
            logger.error(f"Ошибка отправки сообщения в Gemini чат: {e}")
            if "error" in str(e).lower() and "model is overloaded" in str(e).lower():
                raise ModelOverloadedError("Модель временно перегружена брадок.") from e
            raise UnexpectedResponseError(f"Ошибка чата Gemini: {e}") from e
        finally:
            # Удаляем локальные временные файлы, которые были успешно загружены в Gemini API
            for local_path in files_to_delete_locally:
                if local_path.exists():
                    local_path.unlink(missing_ok=True)
                    logger.info(
                        f"Deleted temporary local file after successful upload to Gemini File API: {local_path}"
                    )
