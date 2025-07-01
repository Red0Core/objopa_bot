from aiogram import Dispatcher

from .blackjack import router as blackjack_router
from .crypto import router as crypto_router
from .currencies import router as currencies_router
from .day_tracker import track_router as day_tracker_router
from .download import router as download_router
from .gpt_router import router as gpt_msg_router
from .mention_dice import router as mention_dice_router
from .misc import router as commands_router
from .redis_workers_routers import router as redis_workers_router
from .utils import router as utils_router
from .video_generation_pipeline import video_router
from .user_agent_management import router as user_agent_management_router


def setup_routers(dp: Dispatcher):
    dp.include_routers(
        commands_router,
        utils_router,
        mention_dice_router,
        crypto_router,
        blackjack_router,
        day_tracker_router,
        currencies_router,
        redis_workers_router,
        video_router,
        download_router,
        user_agent_management_router,
        gpt_msg_router,  # Всегда в конце
    )
