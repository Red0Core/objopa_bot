import instaloader
import os
import re

# Регулярка для поиска ссылок Instagram
INSTAGRAM_REGEX = re.compile(r"(https?:\/\/(?:www\.)?instagram\.com\/(?:share\/)?(p|reel|tv)\/[\w-]+)")

# Функция скачивания контента
def download_instagram_media(url):
    bot_loader = instaloader.Instaloader()

    try:
        # Получаем shortcode из ссылки
        shortcode = url.split("/")[-1]
        print(shortcode)
        post = instaloader.Post.from_shortcode(bot_loader.context, shortcode)

        # Папка для скачивания
        download_path = "downloads"
        os.makedirs(download_path, exist_ok=True)

        # Скачиваем пост
        bot_loader.download_post(post, target=download_path)

        # Ищем скачанный файл
        for filename in os.listdir(download_path):
            if filename.startswith(shortcode):
                return os.path.join(download_path, filename)

    except Exception as e:
        print(f"Ошибка загрузки: {e}")
        return None
