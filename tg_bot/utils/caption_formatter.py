from telegramify_markdown import markdownify

from core.logger import logger
from tg_bot.services.gpt import get_gpt_formatted_chunks, split_message_by_paragraphs


class CaptionFormatter:
    """Форматирует и разбивает текст для отправки в Telegram."""
    
    @staticmethod
    def format_and_split(
        text: str | None, 
        first_max: int = 1024, 
        rest_max: int = 4096
    ) -> list[str]:
        """
        Форматирует текст для Telegram и разбивает на части.
        
        Args:
            text: Исходный текст
            first_max: Максимальная длина первого чанка (для caption в media)
            rest_max: Максимальная длина остальных чанков
            
        Returns:
            Список отформатированных частей текста, готовых для отправки
        """
        if not text:
            return []
        
        try:
            # Если одинаковые лимиты - просто используем базовую функцию
            if first_max == rest_max:
                return get_gpt_formatted_chunks(text, max_length=first_max)
            
            # Сначала форматируем весь текст
            formatted_text = markdownify(text)
            
            # Если помещается в первый лимит
            if len(formatted_text) <= first_max:
                return [formatted_text]
            
            # Разбиваем УЖЕ ОТФОРМАТИРОВАННЫЙ текст
            first_parts = split_message_by_paragraphs(formatted_text, max_length=first_max)
            if not first_parts:
                return CaptionFormatter._fallback_split(text, first_max, rest_max)
            
            first_chunk = first_parts[0]
            
            # Если весь текст поместился в первый чанк
            if len(first_parts) == 1:
                return [first_chunk]
            
            # Остальные чанки из ОТФОРМАТИРОВАННОГО текста (не исходного!)
            rest_text = formatted_text[len(first_chunk):]
            rest_chunks = split_message_by_paragraphs(rest_text, max_length=rest_max) if rest_text else []
            
            return [first_chunk] + rest_chunks
            
        except Exception as e:
            logger.warning(f"Error formatting caption: {e}, using fallback")
            return CaptionFormatter._fallback_split(text, first_max, rest_max)
    
    @staticmethod
    def _fallback_split(text: str, first_max: int, rest_max: int) -> list[str]:
        """Простое разбиение по строкам без форматирования."""
        if not text:
            return []
        
        chunks = []
        current = ""
        max_len = first_max
        
        for line in text.split("\n"):
            add = ("\n" if current else "") + line
            if len(current) + len(add) <= max_len:
                current += add
            else:
                if current:
                    chunks.append(current)
                    max_len = rest_max  # После первого чанка используем rest_max
                current = line
        
        if current:
            chunks.append(current)
        
        return chunks


# Глобальный экземпляр для удобства
caption_formatter = CaptionFormatter()
