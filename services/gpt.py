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
    
    finish_reason = completion.choices[0].finish_reason.lower()
    if finish_reason == "length":
        logger.info("Ответ был обрезан. Возможно, стоит запросить меньше данных или увеличить токены.")
    elif finish_reason == "stop":
        logger.info("Ответ завершён корректно.")

    logger.info(f"Ответ гпт: {completion.choices[0].message.content}")

    return completion.choices[0].message
