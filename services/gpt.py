from xml.etree.ElementTree import QName
from openai import AsyncOpenAI, OpenAIError
from config import OPENROUTER_API_KEY
from logger import logger

client = AsyncOpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=OPENROUTER_API_KEY,
)

async def get_openrouter_gemini_2_0_response(prompt: str, system_prompt: str = ""):
    """
    Отправляет запрос в OPENROUTER API gemini-2.0-flash и возвращает сгенерированный текст.
    """
    messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]

    if system_prompt:
        messages.append({
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt
                    }
                ]
            })
    try:
        completion = await client.chat.completions.create(
            model="google/gemini-2.0-flash-exp:free",
            messages=messages
        )
    except OpenAIError as e:
        logger.error(f"Ошибка OpenAI API: {e}")
    
    if completion.choices is not None:
        finish_reason = completion.choices[0].finish_reason.lower()
        if finish_reason == "length":
            logger.info("Ответ был обрезан. Возможно, стоит запросить меньше данных или увеличить токены.")
        elif finish_reason == "stop":
            logger.info("Ответ завершён корректно.")

        logger.info(f"Ответ гпт: {completion.choices[0].message.content}")

        return completion.choices[0].message.content
    else:
        errors_open_router = {
            400: "Bad Request (invalid or missing params, CORS)",
            401: "Invalid credentials (OAuth session expired, disabled/invalid API key)",
            402: "Your account or API key has insufficient credits. Add more credits and retry the request.",
            403: "Your chosen model requires moderation and your input was flagged",
            408: "Your request timed out",
            429: "You are being rate limited",
            502: "Your chosen model is down or we received an invalid response from it",
            503: "There is no available model provider that meets your routing requirements"
        }
        logger.error(f"Ошибка OpenRouter: {errors_open_router[completion.error['code']]}")
        return errors_open_router[completion.error['code']]
