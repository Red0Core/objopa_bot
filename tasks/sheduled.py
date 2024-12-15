import asyncio
from config import ZA_IDEU_CHAT_ID
from services.mexc import get_new_activities

async def scheduled_message(bot):
    await bot.send_message(
        ZA_IDEU_CHAT_ID, 
        text="Бот стартовал и готов к работе!"
    )

    while True:
        message = await get_new_activities()
        if message:
            await bot.send_message(ZA_IDEU_CHAT_ID, message)
        await asyncio.sleep(600)

async def on_startup(bot):
    asyncio.create_task(scheduled_message(bot))
