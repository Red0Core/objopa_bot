from abc import ABC, abstractmethod

import openai
from google import genai
from google.genai import types
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

from core.logger import logger


class AIModelError(Exception):
    """Базовый класс для ошибок модели."""

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


class AIModelInterface(ABC):
    @abstractmethod
    async def get_response(self, prompt: str, system_prompt: str = "") -> str:
        """Отправить запрос к модели и получить ответ."""
        pass


class BaseOpenAIModel(AIModelInterface):
    """
    Класс для модели на основе OpenAI библиотеки
    """

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.client = openai.AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

    def prepare_messages(self, prompt: str, system_prompt: str = "") -> list:
        """
        Формирует сообщения для модели.
        Может быть переопределено в подклассах, если формат сообщений отличается.
        """
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        if system_prompt:
            messages.insert(
                0, {"role": "system", "content": [{"type": "text", "text": system_prompt}]}
            )
        return messages

    async def get_response(self, prompt: str, system_prompt: str = "") -> str:
        messages = self.prepare_messages(prompt, system_prompt)
        content = None
        try:
            completion = await self.client.chat.completions.create(
                model=self.model, messages=messages
            )

            if completion.choices:
                finish_reason = completion.choices[0].finish_reason.lower()
                if finish_reason == "length":
                    logger.info(
                        "Ответ был обрезан. Возможно, стоит запросить меньше данных или увеличить токены."
                    )
                elif finish_reason == "stop":
                    logger.info("Ответ завершён корректно.")

                content = completion.choices[0].message.content
                logger.info(f"Ответ от {self.base_url}: {content}")

        except openai.AuthenticationError:
            raise APIKeyError(f"Неверный или отсутствующий API-ключ для {self.base_url}.") from None
        except openai.RateLimitError:
            raise RateLimitError(f"Превышен лимит запросов к {self.base_url}.") from None
        except openai.APIConnectionError as e:
            raise AIModelError(f"Проблемы с подключением к {self.base_url}: {str(e)}") from e
        except openai.OpenAIError as e:
            raise UnexpectedResponseError(f"Неожиданная ошибка {self.base_url}: {str(e)}") from e

        return content if content is not None else ""


class OpenAIModel(BaseOpenAIModel):
    def __init__(self, api_key: str, model: str = "gpt-4"):
        super().__init__(api_key, model, base_url="https://api.openai.com/v1")

    def prepare_messages(self, prompt: str, system_prompt: str = "") -> list:
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        if system_prompt:
            messages.append(
                {"role": "developer", "content": [{"type": "text", "text": system_prompt}]}
            )
        return messages


class OpenRouterModel(BaseOpenAIModel):
    def __init__(self, api_key: str, model: str = "google/gemini-2.0-flash-exp:free"):
        super().__init__(api_key, model, base_url="https://openrouter.ai/api/v1")


class GeminiModel(AIModelInterface):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        self.api_key = api_key
        self.model = model
        self.client = genai.Client(api_key=self.api_key)

    async def get_response(self, prompt: str, system_prompt: str | None = None) -> str:
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system_prompt),
        )

        # Early return if no valid response
        if (
            not response
            or not response.candidates
            or not response.candidates[0].content
            or not response.candidates[0].content.parts
        ):
            logger.error("Пустой ответ от Gemini.")
            return ""

        # Process all non-thought parts and join them
        result_parts = []
        for part in response.candidates[0].content.parts:
            if not part.text:
                continue

            if part.thought:
                logger.info(f"Model Thought:\n{part.text}\n")
            else:
                logger.info(f"Model Response:\n{part.text}\n")
                result_parts.append(part.text)

        return "".join(result_parts) if result_parts else ""


class AIChatInterface:
    def new_chat(self, system_prompt: str = ""):
        """Инициализация чата с системным промптом"""
        pass

    async def send_message(self, prompt: str) -> str:
        """Отправить сообщение и получить ответ"""
        return ""


class GeminiChatModel(AIChatInterface):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def new_chat(self, system_prompt: str | None = None):
        google_search_tool = Tool(google_search=GoogleSearch())
        if system_prompt is None:
            self.chat = self.client.aio.chats.create(
                model=self.model,
                config=GenerateContentConfig(
                    tools=[google_search_tool],
                    response_modalities=["TEXT"],
                ),
            )
        else:
            self.chat = self.client.aio.chats.create(
                model=self.model,
                config=GenerateContentConfig(
                    tools=[google_search_tool],
                    response_modalities=["TEXT"],
                    system_instruction=system_prompt,
                ),
            )

    async def send_message(self, prompt: str) -> str:
        response = await self.chat.send_message(prompt)
        return response.text if response and response.text is not None else ""
