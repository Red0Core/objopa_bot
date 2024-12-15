from aiogram import Dispatcher
from .commands import router as commands_router
from .utils import router as utils_router
from .mention_dice import router as mention_dice_router

def setup_routers(dp: Dispatcher):
    dp.include_router(commands_router)
    dp.include_router(utils_router)
    dp.include_router(mention_dice_router)
    