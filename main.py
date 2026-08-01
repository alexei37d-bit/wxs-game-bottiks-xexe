import asyncio

from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from handlers.start import router as start_router
from handlers.callbacks import router as callbacks_router

# 1. Импортируем init_db из вашего модуля работы с БД
from database.db import init_db  # <-- Укажите ваш правильный путь к файлу БД!


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # 2. СНАЧАЛА создаем таблицы в БД
    init_db()

    # 3. Подключаем роутеры
    dp.include_router(start_router)
    dp.include_router(callbacks_router)

    # 4. Сообщаем о запуске и начинаем поллинг
    print("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())