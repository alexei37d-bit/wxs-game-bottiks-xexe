import asyncio
import logging
import sqlite3
import sys
import time
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8930414834:AAFH5ZuU_V7w7kI60TOCKIBL57aPasV4TGk"
ADMIN_ID = 6130985988
PAY_PER_MESSAGE = 0.00056
MIN_WITHDRAW = 0.04  # Минимальная сумма вывода ($)
DB_NAME = "bot_data.db"
TARGET_CHAT_USERNAME = "wxs_chat"

# Время задержки в секундах между засчитываемыми сообщениями
COOLDOWN_SECONDS = 5

# Словарь для хранения времени последнего засчитанного сообщения {user_id: timestamp}
user_cooldowns = {}

# Обязательные фразы в BIO пользователя (в нижнем регистре)
REQUIRED_BIO_TEXTS = [
    "@wxs_robot - лучшие кефы и быстрые выводы",
    "@wxs_chat фрибеты каждому в чате проекта",
]


# --- БАЗА ДАННЫХ ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                messages_count INTEGER DEFAULT 0,
                balance REAL DEFAULT 0.0
            )
        """)
        conn.commit()


def get_or_create_user(user_id: int, username: str):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT messages_count, balance FROM users WHERE user_id = ?",
            (user_id,),
        )
        user = cursor.fetchone()

        if user is None:
            cursor.execute(
                "INSERT INTO users (user_id, username, messages_count, balance) VALUES (?, ?, 0, 0.0)",
                (user_id, username),
            )
            conn.commit()
            return 0, 0.0

        count = user[0] if user[0] is not None else 0
        balance = round(float(user[1]), 5) if user[1] is not None else 0.0
        return count, balance


def increment_message_and_pay(user_id: int, username: str, pay_amount: float):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, username, messages_count, balance)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                messages_count = messages_count + 1,
                balance = balance + ?
        """,
            (user_id, username, pay_amount, pay_amount),
        )
        conn.commit()


def withdraw_user_balance(user_id: int) -> float:
    """Обнуляет баланс пользователя и возвращает сумму, которая была на балансе."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,)
        )
        res = cursor.fetchone()

        if not res or res[0] is None or res[0] < MIN_WITHDRAW:
            return 0.0

        amount = round(float(res[0]), 5)

        cursor.execute(
            "UPDATE users SET balance = 0.0 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
        return amount


def add_user_balance(user_id: int, amount: float) -> bool:
    """Начисляет указанную сумму на баланс пользователя."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            return False

        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        conn.commit()
        return True


# --- ПРОВЕРКА БИО ПОЛЬЗОВАТЕЛЯ ---
async def check_user_bio(bot: Bot, user_id: int) -> bool:
    """Проверяет наличие необходимого текста в BIO пользователя"""
    try:
        user_chat = await bot.get_chat(user_id)
        user_bio = user_chat.bio

        if not user_bio:
            return False

        user_bio_lower = user_bio.lower()
        return any(
            required_text in user_bio_lower
            for required_text in REQUIRED_BIO_TEXTS
        )
    except Exception as e:
        logging.error(f"Ошибка при получении BIO пользователя {user_id}: {e}")
        return False


# --- ВСПОМОГАТЕЛЬНЫЕ КНОПКИ И ТЕКСТ ---
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для главного меню (только 1 кнопка Статистика)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Статистика", callback_data="view_stats"
                )
            ]
        ]
    )


def get_stats_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для статистики (в 2 ряда: Обновить и Вывести)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data="refresh_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💸 Вывести", callback_data="request_withdraw"
                )
            ]
        ]
    )


def build_stats_text(user_id: int, username: str) -> str:
    user_data = get_or_create_user(user_id, username)
    count, balance = user_data if user_data else (0, 0.0)

    return (
        f"📊 <b>Ваша статистика:</b>\n\n"
        f"💬 Отправлено сообщений: <b>{count}</b>\n"
        f"💵 Доступно к выводу: <b>{balance:.5f}$</b>\n"
        f"⚙️ За сообщение: <b>{PAY_PER_MESSAGE:.5f}$</b>\n"
        f"💳 Минимальный вывод: <b>{MIN_WITHDRAW}$</b>\n\n"
        f"<b>Учитываются только сообщения из чата @{TARGET_CHAT_USERNAME} при наличии приписки в био.</b>"
    )


# --- ЛОГИКА БОТА ---
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    get_or_create_user(user_id, username)

    text = (
        f"👋 <b>Привет, {message.from_user.mention_html()}!</b>\n\n"
        f"💰 Оплата за каждое сообщение в чате @{TARGET_CHAT_USERNAME}: <b>{PAY_PER_MESSAGE:.5f} $</b>\n"
        f"⏱ Засчитывается <b>не чаще 1 раза в {COOLDOWN_SECONDS} секунд</b>.\n\n"
        f"✅ Чтобы участвовать, обязательно укажите в описании профиля (BIO):\n"
        f"➡️ <code>@wxs_robot - лучшие кефы и быстрые выводы</code>\n"
        f"или\n"
        f"➡️ <code>@wxs_chat фрибеты каждому в чате проекта</code>\n\n"
        f"📊 Нажмите на кнопку ниже или отправьте /stats для проверки баланса."
    )
    await message.answer(
        text, reply_markup=get_main_menu_keyboard(), parse_mode=ParseMode.HTML
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    text = build_stats_text(user_id, username)
    await message.answer(
        text, reply_markup=get_stats_keyboard(), parse_mode=ParseMode.HTML
    )


@dp.callback_query(F.data == "view_stats")
async def callback_view_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name

    text = build_stats_text(user_id, username)

    try:
        await callback.message.edit_text(
            text, reply_markup=get_stats_keyboard(), parse_mode=ParseMode.HTML
        )
        await callback.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer(
                "ℹ️ Данные уже актуальны!", show_alert=False
            )
        else:
            await callback.answer("❌ Не удалось открыть статистику")


@dp.callback_query(F.data == "refresh_stats")
async def callback_refresh_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name

    text = build_stats_text(user_id, username)

    try:
        await callback.message.edit_text(
            text, reply_markup=get_stats_keyboard(), parse_mode=ParseMode.HTML
        )
        await callback.answer("✅ Статистика обновлена!")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer(
                "ℹ️ Данные уже актуальны!", show_alert=False
            )
        else:
            await callback.answer("❌ Не удалось обновить сообщение")


@dp.callback_query(F.data == "request_withdraw")
async def callback_request_withdraw(callback: types.CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name

    _, current_balance = get_or_create_user(user_id, username)

    if current_balance < MIN_WITHDRAW:
        await callback.answer(
            f"❌ Минимальная сумма вывода: {MIN_WITHDRAW}$\n"
            f"Ваш баланс: {current_balance:.5f}$",
            show_alert=True,
        )
        return

    withdrawn_amount = withdraw_user_balance(user_id)

    if withdrawn_amount <= 0:
        await callback.answer(
            "❌ Не удалось обработать вывод.", show_alert=True
        )
        return

    await callback.answer(
        "✅ Ваша заявка была передана администратору!", show_alert=True
    )

    text = build_stats_text(user_id, username)
    try:
        await callback.message.edit_text(
            text, reply_markup=get_stats_keyboard(), parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    user_mention = callback.from_user.mention_html()
    username_str = (
        f"@{callback.from_user.username}"
        if callback.from_user.username
        else "отсутствует"
    )

    admin_message = (
        f"📥 <b>Новая заявка на вывод!</b>\n\n"
        f"👤 Пользователь: {user_mention} (ID: <code>{user_id}</code>)\n"
        f"🏷 Юзернейм: {username_str}\n"
        f"💰 Сумма к выплате: <b>{withdrawn_amount:.5f} $</b>"
    )

    try:
        await bot.send_message(
            ADMIN_ID, admin_message, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(
            f"Не удалось отправить сообщение администратору {ADMIN_ID}: {e}"
        )


@dp.message(Command("addbalance"))
async def cmd_add_balance(message: types.Message, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()

    if len(args) != 3:
        await message.answer(
            "❌ <b>Неверный формат команды!</b>\n\n"
            "Используйте: <code>/addbalance <user_id> <сумма></code>\n"
            "Пример: <code>/addbalance 123456789 0.05</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        target_user_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом, а сумма — числом (через точку).")
        return

    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше 0.")
        return

    success = add_user_balance(target_user_id, amount)

    if success:
        await message.answer(
            f"✅ Успешно начислено <b>{amount:.5f}$</b> пользователю <code>{target_user_id}</code>!",
            parse_mode=ParseMode.HTML
        )

        try:
            await bot.send_message(
                target_user_id,
                f"🎉 Вам начислено <b>{amount:.5f}$</b> на баланс!",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить сообщение пользователю {target_user_id}: {e}")
    else:
        await message.answer(f"❌ Пользователь с ID <code>{target_user_id}</code> не найден в базе данных.")


@dp.message(F.text)
async def process_messages(message: types.Message, bot: Bot):
    if message.text.startswith("/"):
        return

    chat_username = message.chat.username
    if (
        not chat_username
        or chat_username.lower() != TARGET_CHAT_USERNAME.lower()
    ):
        return

    if message.from_user.is_bot:
        return

    user_id = message.from_user.id
    current_time = time.time()

    last_message_time = user_cooldowns.get(user_id, 0)
    if current_time - last_message_time < COOLDOWN_SECONDS:
        return

    has_required_bio = await check_user_bio(bot, user_id)
    if not has_required_bio:
        return

    user_cooldowns[user_id] = current_time
    username = message.from_user.username or message.from_user.first_name

    increment_message_and_pay(user_id, username, PAY_PER_MESSAGE)


# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    init_db()

    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())