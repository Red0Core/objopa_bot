import asyncio
from config import ZA_IDEU_CHAT_ID, OBZHORA_CHAT_ID, MAIN_ACC
from services.mexc import get_new_activities
from tasks.cbr_task import send_daily_cbr_rates

async def scheduled_message(bot):
    await bot.send_message(
        MAIN_ACC, 
        text="Бот стартовал и готов к работе!"
    )

    while True:
        message = await get_new_activities()
        if message:
            await bot.send_message(ZA_IDEU_CHAT_ID, message)
        await asyncio.sleep(600)

async def on_startup(bot):
    asyncio.gather(scheduled_message(bot), send_daily_cbr_rates(bot, OBZHORA_CHAT_ID))
