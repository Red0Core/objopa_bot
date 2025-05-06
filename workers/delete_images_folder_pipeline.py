from core.logger import logger

# Импортируем чужие библиотеки и модули
try:
    import scripts.delete_pics as delete_pics # type: ignore

except ImportError as e:
    logger.critical(f"МИХ АЛО ПОЧИНИ ИМПОРТЫ: {e}")
    raise

class DeleteImagesFolderPipeline:
    def __init__(self, **params) -> None:
        pass

    async def run(self):
        """
        Запускает процесс удаления изображений.
        """
        try:
            delete_pics.main()
            logger.info("Удаление изображений завершено.")
        except Exception as e:
            logger.exception(f"Ошибка при удалении изображений: {e}")
