from aiogram import Dispatcher
from .commands import router as commands_router
from .utils import router as utils_router

def setup_routers(dp: Dispatcher):
    dp.include_router(commands_router)
    dp.include_router(utils_router)