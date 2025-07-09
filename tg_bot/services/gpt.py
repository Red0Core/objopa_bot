from abc import ABC, abstractmethod

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
            completion = await self.client.chat.completions.create(
                model=self.model, messages=messages
            )

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


class GeminiModel(AIModelInterface):
    """Модель Google Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-preview-05-20"):
        if not api_key:
            raise APIKeyError("API ключ для Gemini не может быть пустым")

        self.api_key = api_key
        self.model = model
        self.client = genai.Client(api_key=api_key)

    async def get_response(self, prompt: str, system_prompt: str = "") -> str:
        """Получает ответ от Gemini."""
        if not prompt.strip():
            return ""

        try:
            config_params = {}
            if system_prompt:
                config_params["system_instruction"] = system_prompt

            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_params),
            )

            if not self._is_valid_response(response):
                logger.warning("Получен некорректный ответ от Gemini")
                return ""

            return self._extract_response_text(response)

        except Exception as e:
            logger.error(f"Ошибка при запросе к Gemini: {e}")
            raise UnexpectedResponseError(f"Ошибка Gemini: {e}") from e

    def _is_valid_response(self, response) -> bool:
        """Проверяет валидность ответа."""
        return (
            response
            and response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        )

    def _extract_response_text(self, response) -> str:
        """Извлекает текст из ответа."""
        result_parts = []

        for part in response.candidates[0].content.parts:
            if not part.text:
                continue

            if hasattr(part, "thought") and part.thought:
                logger.debug(f"Gemini thought: {part.text}")
            else:
                result_parts.append(part.text)

        return "".join(result_parts)


class GeminiChatModel(AIChatInterface):
    """Чат-модель Google Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        if not api_key:
            raise APIKeyError("API ключ для Gemini Chat не может быть пустым")

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.chat = None

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

            self.chat = self.client.aio.chats.create(
                model=self.model, config=GenerateContentConfig(**config_params)
            )

        except Exception as e:
            logger.error(f"Ошибка создания чата Gemini: {e}")
            raise AIModelError(f"Не удалось создать чат: {e}") from e

    async def send_message(self, prompt: str) -> str:
        """Отправляет сообщение в чат."""
        if not self.chat:
            raise AIModelError("Чат не инициализирован. Вызовите new_chat() сначала.")

        if not prompt.strip():
            return ""

        try:
            response = await self.chat.send_message(prompt)
            return response.text if response and response.text else ""

        except Exception as e:
            logger.error(f"Ошибка отправки сообщения в Gemini чат: {e}")
            raise UnexpectedResponseError(f"Ошибка чата Gemini: {e}") from e
