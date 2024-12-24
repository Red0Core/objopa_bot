from abc import ABC, abstractmethod
import openai
from logger import logger

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
            messages.insert(0, {"role": "system", "content": [{"type": "text", "text": system_prompt}]})
        return messages

    async def get_response(self, prompt: str, system_prompt: str = "") -> str:
        messages = self.prepare_messages(prompt, system_prompt)
        try:
            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )

            if completion.choices:
                finish_reason = completion.choices[0].finish_reason.lower()
                if finish_reason == "length":
                    logger.info("Ответ был обрезан. Возможно, стоит запросить меньше данных или увеличить токены.")
                elif finish_reason == "stop":
                    logger.info("Ответ завершён корректно.")

                logger.info(f"Ответ от {self.base_url}: {completion.choices[0].message.content}")
                return completion.choices[0].message.content

        except openai.AuthenticationError:
            raise APIKeyError(f"Неверный или отсутствующий API-ключ для {self.base_url}.")
        except openai.RateLimitError:
            raise RateLimitError(f"Превышен лимит запросов к {self.base_url}.")
        except openai.APIConnectionError as e:
            raise AIModelError(f"Проблемы с подключением к {self.base_url}: {str(e)}")
        except openai.OpenAIError as e:
            raise UnexpectedResponseError(f"Неожиданная ошибка {self.base_url}: {str(e)}")

class OpenAIModel(BaseOpenAIModel):
    def __init__(self, api_key: str, model: str = "gpt-4"):
        super().__init__(api_key, model, base_url="https://api.openai.com/v1")

    def prepare_messages(self, prompt: str, system_prompt: str = "") -> list:
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        if system_prompt:
            messages.append({"role": "developer", "content": [{"type": "text", "text": system_prompt}]})
        return messages

class OpenRouterModel(BaseOpenAIModel):
    def __init__(self, api_key: str, model: str = "google/gemini-2.0-flash-exp:free"):
        super().__init__(api_key, model, base_url="https://openrouter.ai/api/v1")
