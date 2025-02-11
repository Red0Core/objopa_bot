import instaloader
import os
import re
import asyncio
from curl_cffi.requests import AsyncSession
from logger import logger

INSTAGRAM_REGEX = re.compile(
    r"(https?:\/\/(?:www\.)?instagram\.com\/(?:share\/)?(p|reel|tv|stories)\/[\w\-]+)"
)

async def get_instagram_shortcode(url):
    """
    Определяет shortcode поста Instagram (редиректим сразу).
    """
    async with AsyncSession() as session:
        try:
            response = await session.get(url, allow_redirects=True, impersonate='chrome')
            final_url = response.url  # Итоговый URL
            match = re.search(r"/(p|reel|tv)/([\w-]+)", final_url)
            if match:
                return match.group(2)

        except Exception as e:
            print(f"Ошибка при редиректе: {e}")
            return None

    return None  # Shortcode не найден

# Имя пользователя для сессии
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")

def init_instaloader():
    """ Инициализация Instaloader с авторизацией """
    bot_loader = instaloader.Instaloader(filename_pattern='{shortcode}')
    
    try:
        bot_loader.load_session_from_file(INSTAGRAM_USERNAME)
        print("✅ Успешная авторизация в Instagram.")
    except FileNotFoundError:
        print("⚠️ Файл сессии не найден. Будут загружены только открытые посты.")
    
    return bot_loader

# Создаём Instaloader сессию при старте
INSTALOADER_SESSION = init_instaloader()

async def download_instagram_media(url):
    """ Асинхронно загружает посты Instagram (фото, видео, текст) """
    bot_loader = INSTALOADER_SESSION
    try:
        shortcode = await get_instagram_shortcode(url)
        if not shortcode:
            return None, "❌ Ошибка: Не удалось извлечь shortcode из ссылки."

        download_path = "downloads"
        os.makedirs(download_path, exist_ok=True)

        post = await asyncio.to_thread(instaloader.Post.from_shortcode, bot_loader.context, shortcode)
        await asyncio.to_thread(bot_loader.download_post, post, download_path)

        return shortcode, None

    except instaloader.exceptions.LoginRequiredException:
        return None, "❌ Ошибка: Требуется вход в Instagram. Сессия устарела."

    except instaloader.exceptions.ConnectionException:
        return None, "❌ Ошибка: Проблемы с соединением. Проверьте интернет или VPN."

    except instaloader.exceptions.BadResponseException:
        return None, "❌ Ошибка: Instagram заблокировал доступ. Попробуйте позже."

    except Exception as e:
        return None, f"❌ Ошибка: {e}"

async def select_instagram_media(shortcode, download_path="downloads"):
    """
    Определяет, какие файлы скачал Instaloader, и выбирает нужный формат для отправки.
    """
    files = [f for f in os.listdir(download_path) if f.startswith(shortcode)]
    
    images = []
    videos = []
    caption = None

    for file in files:
        file_path = os.path.join(download_path, file)
        
        # Определяем тип файла
        if file.endswith((".jpg", ".jpeg", ".png")):
            images.append(file_path)
        elif file.endswith((".mp4", ".mov")):
            videos.append(file_path)
        elif file.endswith(".txt"):
            with open(file_path, "r", encoding="utf-8") as f:
                caption = f.read()  # Читаем описание поста

    return {
        "images": images,
        "videos": videos,
        "caption": caption
    }

