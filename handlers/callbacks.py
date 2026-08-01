import asyncio
import math
import base64
import random
import sqlite3
import time
import re
import aiohttp
from aiogram.enums import ChatType
from html import escape
from datetime import datetime

processing_users = set()
from aiogram import BaseMiddleware
import html as py_html
from decimal import Decimal

from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    BufferedInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# Ваша БД и сервисы
from handlers.start import main_menu
from config import ADMIN_ID
from handlers.admin_states import AdminBalance, AdminDecreaseBalance
from database.db import (
    save_invoice,
    get_invoice,
    invoice_paid,
    is_paid,
    get_bonus,
    take_bonus,
    increase_bonus_day,
    add_balance,
    subtract_balance,
    get_user,
    add_turnover,
    get_top_turnover,
    get_top_users_by_balance
)

from services.cryptobot import create_cryptobot_invoice, check_cryptobot_invoice
from services.xrocket import create_xrocket_invoice, check_xrocket_invoice
from contextlib import suppress
import time
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

game_edit_timestamps = {}
active_games = {}
game_counter = 0
active_pve_3cube_games = {}
tower_locks = {}
active_pve_bowling_games = {}
bowling_game_counter = 0


class Deposit(StatesGroup):
    amount = State()
    cryptobot = State()
    xrocket = State()


class CubeGame(StatesGroup):
    mode = State()
    bet = State()
    choice = State()
    waiting_for_bet = State()


class GameState(StatesGroup):
    waiting_for_bet = State()


class MinesState(StatesGroup):
    menu = State()
    setting_mines = State()
    entering_bet = State()
    playing = State()


class WithdrawState(StatesGroup):
    select_method = State()  # Выбор платежной системы
    enter_amount = State()  # Ввод суммы
    confirm_gamble = State()  # Выбор: рисковать или нет


router = Router()

ADMIN_ID = 7921743592

# --- Вспомогательные клавиатуры ---
def get_withdraw_methods_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="xRocket",
                                     callback_data="withdraw_method:xrocket",
                                     icon_custom_emoji_id="5415897719522744378"
                                     ),
                InlineKeyboardButton(text="CryptoBot",
                                     callback_data="withdraw_method:cryptobot",
                                     icon_custom_emoji_id="5361914370068613491"
                                     )
            ],
            [
                InlineKeyboardButton(text="Отмена",
                                     callback_data="cancel",
                                     icon_custom_emoji_id="4958526153955476488")
            ]
        ]
    )


def get_gamble_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Увеличить на +50% (Шанс 70%)",
                                     callback_data="gamble_yes",
                                     icon_custom_emoji_id="5310156780041414433"),
            ],
            [
                InlineKeyboardButton(text="Вывести без риска",
                                     callback_data="gamble_no",
                                     icon_custom_emoji_id="5420323339723881652"),
            ]
        ]
    )


def get_user_real_deposits_total(user_id: int) -> float:
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       CREATE TABLE IF NOT EXISTS deposits
                       (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           user_id INTEGER NOT NULL,
                           amount REAL NOT NULL,
                           timestamp INTEGER NOT NULL
                       )
                       """)
        cursor.execute(
            "SELECT SUM(amount) FROM deposits WHERE user_id = ?",
            (user_id,)
        )
        result = cursor.fetchone()[0]
        return result if result is not None else 0.0


@router.message(Command("start"), F.chat.type == ChatType.PRIVATE)
async def cmd_start_pm(message: types.Message):
    await message.answer("Добро пожаловать в личные сообщения!")


@router.message(Command("deposit"), F.chat.type == ChatType.PRIVATE)
async def cmd_deposit_pm(message: types.Message):
    text, kb = get_deposit_menu()
    await message.answer(text=text, parse_mode="HTML", reply_markup=kb)


@router.message(
    Command("start", "deposit"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def ignore_group_commands(message: types.Message):
    pass


class IgnoreGroupCommandsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        if isinstance(event, Message) and event.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            if event.text and event.text.startswith("/"):
                return
        return await handler(event, data)


QUOTES = [
    "«Удача любит смелых! <tg-emoji emoji-id=\"5366389751760851269\">😏</tg-emoji>»",
    "«Не пробуешь — не выигрываешь <tg-emoji emoji-id=\"5893281616486209299\">💤</tg-emoji>»",
    "«Удача — это постоянная готовность к использованию шанса <tg-emoji emoji-id=\"5350387077278096583\">📆</tg-emoji>»",
    "«Сегодня точно твой день! <tg-emoji emoji-id=\"5469940992360586876\">❤️</tg-emoji>»",
    "«Ты сегодня словишь куш! <tg-emoji emoji-id=\"5310156780041414433\">🎰</tg-emoji>»",
]


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def parse_bet_amount(amount_raw: str, user_balance: float) -> float:
    """Парсер ставки: поддерживаются числа с запятой/точкой и ва-банк выражения"""
    clean_raw = amount_raw.strip().lower()
    if clean_raw in ["вб", "все", "all", "ва-банк", "вабанк"]:
        return round(user_balance, 2)
    clean_raw = clean_raw.replace(",", ".")
    return round(float(clean_raw), 2)


def get_bet_keyboard(dice_count: int, mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    amounts = [1, 5, 10, 50, 100]
    for amount in amounts:
        builder.button(
            text=f"{amount}",
            callback_data=f"place_bet:{dice_count}:{mode}:{amount}",
            icon_custom_emoji_id="5893473283696759404"
        )
    builder.adjust(3, 2)
    builder.row(InlineKeyboardButton(text="Отмена", callback_data=f"back_to_modes:{dice_count}",
                                     icon_custom_emoji_id="4958526153955476488"))
    return builder.as_markup()


def get_winning_targets(dice_count: int, mode_code: str) -> str:
    EMOJI_DICE = {
        1: '<tg-emoji emoji-id=\"5778423436393061708\">1️⃣</tg-emoji>',
        2: '<tg-emoji emoji-id=\"5778559466597261367\">2️⃣</tg-emoji>',
        3: '<tg-emoji emoji-id=\"5780555617072587562\">3️⃣</tg-emoji>',
        4: '<tg-emoji emoji-id=\"5778418467115900127\">4️⃣</tg-emoji>',
        5: '<tg-emoji emoji-id=\"5778197284890091321\">5️⃣</tg-emoji>',
        6: '<tg-emoji emoji-id=\"5778543227325915184\">6️⃣</tg-emoji>',
    }

    targets_map = {
        "even": [2, 4, 6],
        "odd": [1, 3, 5],
        "under": [1, 2, 3],
        "low": [1, 2, 3],
        "over": [4, 5, 6],
        "high": [4, 5, 6],
    }

    numbers = targets_map.get(mode_code, [])
    if numbers:
        return " ".join([EMOJI_DICE[num] for num in numbers if num in EMOJI_DICE])
    return "Другое число"


def get_deposit_menu():
    text = "<tg-emoji emoji-id=\"5449683594425410231\">🔼</tg-emoji> <b>Выберите способ пополнения</b>"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="CryptoBot",
                    callback_data="deposit_cryptobot",
                    icon_custom_emoji_id="5361914370068613491"
                )
            ],
            [
                InlineKeyboardButton(
                    text="xRocket",
                    callback_data="deposit_xrocket",
                    icon_custom_emoji_id="5415897719522744378"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data="balance",
                    icon_custom_emoji_id="5877629862306385808"
                )
            ]
        ]
    )
    return text, kb


def calculate_coeff(mines: int, step: int) -> float:
    total_cells = 25
    safe_cells = total_cells - mines

    total_combinations = math.comb(total_cells, step)
    safe_combinations = math.comb(safe_cells, step)

    probability = safe_combinations / total_combinations
    raw_coeff = (1 / probability) * 0.95
    return max(1.01, round(raw_coeff, 2))


def get_mines_board_keyboard(
        game_data: dict,
        reveal: bool = False,
        show_cashout: bool = True,
        state: str = "playing"
) -> InlineKeyboardMarkup:
    keyboard = []
    game_data = game_data or {}

    board = game_data.get('board', ['safe'] * 25)
    opened = game_data.get('opened', [])
    game_over = game_data.get('game_over', False)
    exploded_idx = game_data.get('exploded_idx', None)

    is_finished = game_over or reveal

    for row in range(5):
        row_buttons = []
        for col in range(5):
            idx = row * 5 + col

            if state == "preview":
                text = " "
                icon_id = "5309871117471587827"
                cb = "mines_finished_game"
            else:
                if idx in opened:
                    if idx == exploded_idx:
                        text = " "
                        icon_id = "5204449199571115075"
                    elif board[idx] == 'mine':
                        text = " "
                        icon_id = "5204096054475132864"
                    else:
                        text = " "
                        icon_id = "5237907553152672597"
                else:
                    if is_finished and board[idx] == 'mine':
                        text = " "
                        icon_id = "5204096054475132864"
                    else:
                        text = " "
                        icon_id = "5309871117471587827"

                cb = f"mine_click:{idx}" if not is_finished and idx not in opened else "mines_finished_game"

            row_buttons.append(
                InlineKeyboardButton(
                    text=text,
                    callback_data=cb,
                    icon_custom_emoji_id=icon_id
                )
            )
        keyboard.append(row_buttons)

    if state == "preview":
        mines_count = game_data.get('mines_count', 5)
        keyboard.append([
            InlineKeyboardButton(text="Играть",
                                 callback_data="mines_start_game",
                                 icon_custom_emoji_id="5355268724221812442"),
            InlineKeyboardButton(text=f"Мин: {mines_count}",
                                 callback_data="mines_change_count",
                                 icon_custom_emoji_id="5204096054475132864")
        ])
        keyboard.append([
            InlineKeyboardButton(text="Назад",
                                 callback_data="play",
                                 icon_custom_emoji_id="5877629862306385808")
        ])

    elif state == "playing" and not reveal and show_cashout:
        opened_count = len(opened)
        if opened_count > 0:
            mines_count = game_data.get('mines_count', 5)
            coeff = calculate_coeff(mines_count, opened_count)
            win_amount = round(game_data.get('bet', 0) * coeff, 2)

            keyboard.append([
                InlineKeyboardButton(
                    text=f"Забрать {win_amount}$ (x{coeff})",
                    callback_data=f"mines_cashout:{game_data.get('id', 0)}",
                    icon_custom_emoji_id="5201691993775818138"
                )
            ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


WHITELISTED_USERS = {6130985988, 6716387090, 8872549356}


def calculate_rigged_loss_chance(bet: float, mines_count: int, step_number: int, user_id: int) -> float:
    if user_id is not None and user_id in WHITELISTED_USERS:
        return 0.0

    if step_number <= 1:
        return 0.0

    step_penalty = (step_number - 1) * 0.037
    bet_penalty = 0.03 if bet >= 2 else 0.0
    total_chance = min(step_penalty + bet_penalty, 0.15)

    return total_chance


@router.message(
    F.text.lower().startswith(("деп", "депозит"))
)
async def fast_deposit_handler(message: Message):
    parts = message.text.strip().split()

    if len(parts) < 2:
        return await message.answer(
            '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Укажите сумму. Пример: <code>деп 10</code>',
            parse_mode="HTML"
        )

    raw_amount = parts[1].replace(",", ".")

    try:
        amount = float(raw_amount)
        if amount < 0.1:
            return await message.answer(
                '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Минимальная сумма пополнения — 0.1 USDT',
                parse_mode="HTML"
            )
    except ValueError:
        return await message.answer(
            '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Некорректная сумма.',
            parse_mode="HTML"
        )

    cb_invoice = await create_cryptobot_invoice(amount)
    xr_invoice = await create_xrocket_invoice(amount)

    if not cb_invoice or not xr_invoice:
        return await message.answer(
            '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Ошибка создания счёта. Попробуйте позже.',
            parse_mode="HTML"
        )

    cb_id = str(cb_invoice.get("invoice_id") or cb_invoice.get("id"))
    xr_id = str(xr_invoice.get("id"))

    save_invoice(cb_id, message.from_user.id, amount)
    save_invoice(xr_id, message.from_user.id, amount)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="CryptoBot",
                                 url=cb_invoice.get("pay_url") or cb_invoice.get("bot_invoice_url"),
                                 icon_custom_emoji_id="5361914370068613491"
                                 ),
            InlineKeyboardButton(text="xRocket",
                                 url=xr_invoice.get("link"),
                                 icon_custom_emoji_id="5415897719522744378"
                                 )
        ],
        [
            InlineKeyboardButton(text="Проверить оплату",
                                 callback_data=f"check_fast_dep:{cb_id}:{xr_id}",
                                 icon_custom_emoji_id="4956721670690702265"
                                 )
        ],
    ])

    text = f"""
<tg-emoji emoji-id="5443127283898405358">📥</tg-emoji> <b>Счёт успешно создан!</b>

<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> Сумма: <b>{amount:.2f} USDT</b>

После оплаты средства автоматически поступят на баланс. <tg-emoji emoji-id="4956721670690702265">✔️</tg-emoji>
"""

    await message.reply(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("check_fast_dep:"))
async def check_fast_deposit_callback(call: CallbackQuery):
    _, cb_id, xr_id = call.data.split(":")
    user_id = call.from_user.id

    invoice = get_invoice(cb_id)

    if not invoice or invoice["paid"] == 1:
        return await call.answer("⚠️ Этот счёт уже обработан или не существует.", show_alert=True)

    amount = float(invoice["amount"])

    is_paid_status = False
    paid_system = ""

    cb_data = await check_cryptobot_invoice(cb_id)
    if cb_data:
        cb_status = cb_data.get("status") if isinstance(cb_data, dict) else getattr(cb_data, "status", None)
        if cb_status == "paid":
            is_paid_status = True
            paid_system = "CryptoBot"

    if not is_paid_status:
        xr_data = await check_xrocket_invoice(xr_id)
        if xr_data and xr_data.get("status") == "paid":
            is_paid_status = True
            paid_system = "xRocket"

    if is_paid_status:
        invoice_paid(cb_id)
        invoice_paid(xr_id)
        add_balance(user_id, amount)

        try:
            await call.message.delete()
        except Exception:
            pass
    else:
        await call.answer("🕓 Ожидаю, похоже, что счет не оплачен", show_alert=True)


# ==========================================
# ОСНОВНЫЕ ИГРОВЫЕ ХЭНДЛЕРЫ
# ==========================================
@router.message(
    F.reply_to_message,
    F.text,
    F.text.regexp(r"(?i)^(?:нк|передать)\s+(\d+(?:[\.,]\d+)?)$"),
)
async def transfer_balance_reply(message: types.Message):
    """Перевод баланса пользователю через ответ на его сообщение"""
    if (
        not message.text
        or not message.reply_to_message
        or not message.reply_to_message.from_user
    ):
        return

    match = re.match(
        r"^(?:нк|передать)\s+(\d+(?:[\.,]\d+)?)$",
        message.text.strip(),
        re.IGNORECASE,
    )
    if not match:
        return

    # Округляем вводимую сумму до 2 знаков после запятой, чтобы избежать микро-копеек
    amount = round(float(match.group(1).replace(",", ".")), 2)
    sender_id = message.from_user.id
    recipient = message.reply_to_message.from_user
    recipient_id = recipient.id

    if sender_id == recipient_id:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Нельзя'
            " переводить средства самому себе!",
            parse_mode="HTML",
        )

    if recipient.is_bot:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Нельзя'
            " переводить средства ботам!",
            parse_mode="HTML",
        )

    if amount < 0.03:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji>'
            " Минимальная сумма перевода — <b>0.03 $</b>",
            parse_mode="HTML",
        )

    sender_db = get_user(sender_id)
    # Округляем баланс отправителя для точного сравнения
    sender_balance = round(sender_db[2], 2) if sender_db else 0.0

    # Теперь при переводе всей суммы (например, при балансе 10.0 и сумме 10.0)
    # условие sender_balance < amount НЕ сработает, и перевод пройдет ровно под 0
    if sender_balance < amount:
        return await message.reply(
            '<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji>'
            " <b>Недостаточно средств!</b>\n"
            f"Ваш баланс: <b>{sender_balance:.2f} $</b>",
            parse_mode="HTML",
        )

    subtract_balance(sender_id, amount)
    add_balance(recipient_id, amount)

    updated_sender_db = get_user(sender_id)
    new_sender_balance = (
        round(updated_sender_db[2], 2) if updated_sender_db else 0.0
    )

    text = (
        '<tg-emoji emoji-id="5429561707003395285">💸</tg-emoji> <b>Успешный'
        " накид!</b>\n\n"
        '<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> <b>От:</b>'
        f" {message.from_user.mention_html()}\n"
        f"<b>Кому:</b> {recipient.mention_html()}\n"
        '<tg-emoji emoji-id="5469669782355713209">😛</tg-emoji>'
        f" <b>Сумма:</b> {amount:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n\n"
        '<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Баланс отправителя:'
        f" <b>{new_sender_balance:.2f} $</b>"
    )

    await message.reply(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("back_to_modes:"))
async def back_to_modes(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await cube_menu(callback, state)


@router.callback_query(F.data == "play")
async def play(callback: CallbackQuery):
    await callback.answer()

    user_db = get_user(callback.from_user.id)
    balance = user_db[2] if user_db else 0.0
    quote = random.choice(QUOTES)

    text = (
        f'<tg-emoji emoji-id="5258508428212445001">🎮</tg-emoji> <b>Выберите игру или режим, в который хотите сыграть</b> <tg-emoji emoji-id="5116240346656801621">❓</tg-emoji>\n'
        f'<b>Баланс:</b> {balance:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n'
        f'<blockquote>{quote}</blockquote>'
    )

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Куб",
                                         callback_data="cube",
                                         icon_custom_emoji_id="5778543227325915184"
                                         ),
                    InlineKeyboardButton(text="Слоты",
                                         callback_data="slots",
                                         icon_custom_emoji_id="5310156780041414433"
                                         ),
                ],
                [
                    InlineKeyboardButton(text="Мины",
                                         callback_data="mines",
                                         icon_custom_emoji_id="5204096054475132864"
                                         )
                ],
                [
                    InlineKeyboardButton(text="Башня",
                                         callback_data="tower",
                                         icon_custom_emoji_id="5474675772897661586"
                                         )
                ],
                [
                    InlineKeyboardButton(text="Назад",
                                         callback_data="back",
                                         icon_custom_emoji_id="5877629862306385808"
                                         )
                ],
            ]
        )
    )


@router.callback_query(F.data == "cube")
async def cube_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()
    cube_count = data.get("cube_count", 1)

    user_db = get_user(callback.from_user.id)
    balance = user_db[2] if user_db else 0.0

    DICE_EMOJI = '<tg-emoji emoji-id="5778543227325915184">🎲</tg-emoji>'
    selected_emojis = DICE_EMOJI * cube_count

    c1_text = "✅ 1 Куб" if cube_count == 1 else "1 Куб"

    text = f"""
<tg-emoji emoji-id="5778543227325915184">🎲</tg-emoji> <b>Игра «Куб»</b>

<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> Ваш баланс: <b>{balance:.2f}</b><tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>

<blockquote><b>Количество кубов:</b> 
Выбрано {selected_emojis} ({cube_count} шт.)
Выберите исход снизу:</blockquote>
"""

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=c1_text, callback_data="cube_count_1",
                                         icon_custom_emoji_id="5778543227325915184"),
                ],
                [
                    InlineKeyboardButton(text="Чёт (x1.9)", callback_data=f"mode:{cube_count}:even",
                                         icon_custom_emoji_id="5330400906527663786"),
                    InlineKeyboardButton(text="Нечёт (x1.9)", callback_data=f"mode:{cube_count}:odd",
                                         icon_custom_emoji_id="5330400906527663786"),
                ],
                [
                    InlineKeyboardButton(text="Меньше (x1.9)", callback_data=f"mode:{cube_count}:under",
                                         icon_custom_emoji_id="5330400906527663786"),
                    InlineKeyboardButton(text="Больше (x1.9)", callback_data=f"mode:{cube_count}:over",
                                         icon_custom_emoji_id="5330400906527663786"),
                ],
                [
                    InlineKeyboardButton(text="Назад", callback_data="play", icon_custom_emoji_id="5877629862306385808")
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("cube_count_"))
async def set_cube_count(callback: CallbackQuery, state: FSMContext):
    count = int(callback.data.split("_")[2])
    await state.update_data(cube_count=count)
    await callback.answer(f"Выбрано кубиков: {count}")
    await cube_menu(callback, state)


@router.callback_query(F.data.startswith("mode:"))
async def process_mode_selection(callback: types.CallbackQuery, state: FSMContext):
    _, dice_count, mode_code = callback.data.split(":")
    dice_count = int(dice_count)

    mode_names = {
        "even": "Чёт (x1.9)",
        "odd": "Нечёт (x1.9)",
        "under": "Меньше (x1.9)",
        "over": "Больше (x1.9)",
    }
    mode_title = mode_names.get(mode_code, mode_code)

    await state.update_data(dice_count=dice_count, mode_code=mode_code, mode_title=mode_title)
    await state.set_state(CubeGame.waiting_for_bet)

    user = get_user(callback.from_user.id)
    balance = user[2] if user else 0.0

    text = (
        f"<tg-emoji emoji-id=\"5778543227325915184\">6️⃣</tg-emoji> <b>Игра «Куб»</b>\n\n"
        f"<tg-emoji emoji-id=\"4956232383721374836\">📌</tg-emoji> Кубов: <b>{dice_count} шт.</b>\n"
        f"<tg-emoji emoji-id=\"4956232383721374836\">📌</tg-emoji> Режим: <b>{mode_title}</b>\n\n"
        f"<tg-emoji emoji-id=\"5769126056262898415\">👛</tg-emoji> Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n"
        f"<tg-emoji emoji-id=\"5357428530130984271\">😰</tg-emoji> <b>Выберите сумму ставки</b> кнопкой или введите число:"
    )

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML",
        reply_markup=get_bet_keyboard(dice_count, mode_code)
    )
    await callback.answer()


async def start_dice_roll(target_message: Message, state: FSMContext, amount: float, user_id: int, bot: Bot):
    try:
        user_data = await state.get_data()
        mode_title = user_data.get("mode_title", "Игра")
        mode_code = user_data.get("mode_code", "even")
        dice_count = user_data.get("dice_count", 1)

        user_db = get_user(user_id)
        raw_balance = user_db[2] if user_db else 0.0

        bet = Decimal(str(amount))
        balance = Decimal(str(raw_balance))

        if bet > balance:
            text_error = (
                f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n'
                f'Ваш баланс: <b>{balance:.2f}</b><tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>, '
                f'а выбранная ставка: <b>{amount:.2f}</b><tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>'
            )

            error_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text='Пополнить баланс',
                            callback_data="deposit",
                            icon_custom_emoji_id="5206401524200145033"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text='Назад к выбору ставки',
                            callback_data=f"mode:{dice_count}:{mode_code}",
                            icon_custom_emoji_id="5877629862306385808"
                        )
                    ]
                ]
            )

            try:
                await target_message.edit_text(text_error, reply_markup=error_keyboard, parse_mode="HTML")
            except TelegramBadRequest:
                await target_message.answer(text_error, reply_markup=error_keyboard, parse_mode="HTML")

            return

        subtract_balance(user_id, amount)
        add_turnover(user_id, amount)
        await state.clear()

        text_accepted = (
            f'<tg-emoji emoji-id="5778543227325915184">6️⃣</tg-emoji> <b>Ставка принята: {amount:.2f} $</b>\n'
            f'Режим: <b>{mode_title}</b>\n\n'
            f'<tg-emoji emoji-id="5469627876359806877">😀</tg-emoji> Бросаем куб...'
        )

        try:
            await target_message.edit_text(text_accepted, parse_mode="HTML")
        except TelegramBadRequest:
            await target_message.answer(text_accepted, parse_mode="HTML")

        chat_id = target_message.chat.id
        dice_msg = await bot.send_dice(chat_id=chat_id, emoji="🎲")
        value = dice_msg.dice.value

        await asyncio.sleep(3)

        is_win = False
        coef = 0.0

        if mode_code == "even" and value % 2 == 0:
            is_win, coef = True, 1.9
        elif mode_code == "odd" and value % 2 != 0:
            is_win, coef = True, 1.9
        elif mode_code in ["under", "low"] and value in [1, 2, 3]:
            is_win, coef = True, 1.9
        elif mode_code in ["over", "high"] and value in [4, 5, 6]:
            is_win, coef = True, 1.9

        home_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Домой, Уолтер",
                        callback_data="play",
                        icon_custom_emoji_id="5938537205847822613"
                    )
                ]
            ]
        )

        if is_win:
            win_amount = round(amount * coef, 2)
            add_balance(user_id, win_amount)

            updated_user = get_user(user_id)
            new_balance = updated_user[2] if updated_user else 0.0

            caption_text = (
                f'<tg-emoji emoji-id="5429561707003395285">🥳</tg-emoji> <b>Вы выиграли {win_amount:.2f} $!</b>\n'
                f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> Выпало число: <b>{value}</b>\n\n'
                f'<tg-emoji emoji-id="5465465194056525619">👍</tg-emoji> Ваш баланс: <b>{new_balance:.2f} $</b>'
            )

            await bot.send_message(
                chat_id=chat_id,
                text=caption_text,
                reply_markup=home_keyboard,
                parse_mode="HTML"
            )
        else:
            updated_user = get_user(user_id)
            new_balance = updated_user[2] if updated_user else 0.0

            needed_numbers = get_winning_targets(dice_count, mode_code)

            caption_text = (
                f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Вы проиграли!</b>\n\n'
                f'Выпало число: <b>{value}</b>\n'
                f'<blockquote>А нужно было: <b>{needed_numbers}</b></blockquote>\n\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Ваш баланс: <b>{new_balance:.2f}</b> '
                f'<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>'
            )

            await bot.send_message(
                chat_id=chat_id,
                text=caption_text,
                reply_markup=home_keyboard,
                parse_mode="HTML"
            )

    except Exception as e:
        await target_message.answer(
            f"⚠️ Ошибка выполнения игры: <code>{e}</code>",
            parse_mode="HTML"
        )


@router.callback_query(CubeGame.waiting_for_bet, F.data.startswith("place_bet:"))
async def process_bet_button(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    _, dice_count, mode_code, amount_str = callback.data.split(":")
    amount = float(amount_str)

    await callback.answer()
    await start_dice_roll(callback.message, state, amount, callback.from_user.id, bot)


@router.message(CubeGame.waiting_for_bet)
async def process_bet_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    warned = data.get("warned_min_bet", False)
    user_db = get_user(message.from_user.id)
    balance = user_db[2] if user_db else 0.0

    try:
        amount = parse_bet_amount(message.text, balance)
        if amount < 0.1:
            if not warned:
                await state.update_data(warned_min_bet=True)
                await message.answer(
                    '<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> Минимальная ставка — <b>0.1 $</b>!',
                    parse_mode="HTML"
                )
            return
    except ValueError:
        if not warned:
            await state.update_data(warned_min_bet=True)
            await message.answer(
                '<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> Пожалуйста, введите или выберите корректное число!',
                parse_mode="HTML"
            )
        return

    await state.update_data(warned_min_bet=False)
    await start_dice_roll(message, state, amount, message.from_user.id, message.bot)


NUMBER_PREMIUM_EMOJIS = {
    '0': '<tg-emoji emoji-id=\"5474357150748781092\">0️⃣</tg-emoji>',
    '1': '<tg-emoji emoji-id=\"5472197417854052950\">1️⃣</tg-emoji>',
    '2': '<tg-emoji emoji-id=\"5472261026319707797\">2️⃣</tg-emoji>',
    '3': '<tg-emoji emoji-id=\"5474428378486417299\">3️⃣</tg-emoji>',
    '4': '<tg-emoji emoji-id=\"5474354466394219119\">4️⃣</tg-emoji>',
    '5': '<tg-emoji emoji-id=\"5474503089442531516\">5️⃣</tg-emoji>',
    '6': '<tg-emoji emoji-id=\"5474182036342186561\">6️⃣</tg-emoji>',
    '7': '<tg-emoji emoji-id=\"5474653679585863564\">7️⃣</tg-emoji>',
    '8': '<tg-emoji emoji-id=\"5472409786806971289\">9️⃣</tg-emoji>',
    '9': '<tg-emoji emoji-id=\"5474256279146865826\">9️⃣</tg-emoji>',
}


def format_number_to_premium(number: int) -> str:
    return "".join(NUMBER_PREMIUM_EMOJIS.get(char, char) for char in str(number))


DICE_PREMIUM_EMOJIS = {
    1: '<tg-emoji emoji-id=\"5472197417854052950\">1️⃣</tg-emoji>',
    2: '<tg-emoji emoji-id=\"5472261026319707797\">2️⃣</tg-emoji>',
    3: '<tg-emoji emoji-id=\"5474428378486417299\">3️⃣</tg-emoji>',
    4: '<tg-emoji emoji-id=\"5474354466394219119\">4️⃣</tg-emoji>',
    5: '<tg-emoji emoji-id=\"5474503089442531516\">5️⃣</tg-emoji>',
    6: '<tg-emoji emoji-id=\"5474182036342186561\">6️⃣</tg-emoji>',
}

EMPTY_DICE_SLOT = '<tg-emoji emoji-id=\"5242612543796567211\">⭐️</tg-emoji>'


def format_dice_list(values: list, target_count: int = 3) -> str:
    formatted = [DICE_PREMIUM_EMOJIS.get(val, str(val)) for val in values]
    while len(formatted) < target_count:
        formatted.append(EMPTY_DICE_SLOT)
    return " + ".join(formatted)


@router.message(F.text.regexp(r"(?i)^([1-3])\s*куб\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$"))
async def start_cube_lobby_cmd(message: Message, bot: Bot):
    global game_counter

    match = re.match(r"^([1-3])\s*куб\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$", message.text, re.IGNORECASE)
    if not match:
        return

    cube_count = int(match.group(1))
    amount_raw = match.group(2)
    user_id = message.from_user.id
    chat_id = message.chat.id
    game_key = f"{chat_id}:{user_id}"

    user_db = get_user(user_id)
    balance = round(user_db[2], 2) if user_db else 0.0

    bet = parse_bet_amount(amount_raw, balance)

    if game_key in active_pve_3cube_games:
        return await message.reply(
            '<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji> У вас есть активная игра! Завершите её.',
            parse_mode="HTML"
        )

    if bet < 0.1:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — <b>0.1 $</b>!',
            parse_mode="HTML"
        )

    if balance < bet:
        return await message.reply(
            f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n'
            f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
            parse_mode="HTML"
        )

    subtract_balance(user_id, bet)
    add_turnover(user_id, bet)

    game_counter += 1
    game_num = game_counter
    user_name = message.from_user.mention_html()

    active_pve_3cube_games[game_key] = {
        "game_num": game_num,
        "cube_count": cube_count,
        "user_id": user_id,
        "user_name": user_name,
        "bet": bet,
        "bot_dices": [],
        "user_dices": [],
        "status": "bot_turn",
        "lobby_msg_id": None
    }

    lobby_text = (
        f'<tg-emoji emoji-id="5778543227325915184">6️⃣</tg-emoji> <b>Куб #{game_num}</b>\n\n'
        f'<b>Игроки:</b>\n'
        f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: {format_dice_list([], cube_count)}\n'
        f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: {format_dice_list([], cube_count)}\n\n'
        f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>\n'
        f'<blockquote><tg-emoji emoji-id="5215579104807497179">🕐</tg-emoji> Бот кидает кости...</blockquote>'
    )

    lobby_msg = await message.answer(lobby_text, parse_mode="HTML")
    active_pve_3cube_games[game_key]["lobby_msg_id"] = lobby_msg.message_id

    await asyncio.sleep(1.7)

    bot_dices = []
    for _ in range(cube_count):
        dice_msg = await bot.send_dice(chat_id, emoji="🎲")
        await asyncio.sleep(3.5)

        bot_dices.append(dice_msg.dice.value)
        active_pve_3cube_games[game_key]["bot_dices"] = bot_dices

        updated_text = (
            f'<tg-emoji emoji-id="5778543227325915184">6️⃣</tg-emoji> <b>Куб #{game_num}</b>\n\n'
            f'<b>Игроки:</b>\n'
            f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: {format_dice_list([], cube_count)}\n'
            f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: {format_dice_list(bot_dices, cube_count)}\n\n'
            f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>\n'
            f'<blockquote><tg-emoji emoji-id="5215579104807497179">🕐</tg-emoji> Бот кидает кости...</blockquote>'
        )

        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=lobby_msg.message_id,
                text=updated_text,
                parse_mode="HTML"
            )

        await asyncio.sleep(1)

    active_pve_3cube_games[game_key]["status"] = "user_turn"

    if cube_count > 1:
        bot_total_fmt = format_number_to_premium(sum(bot_dices))
        bot_score_str = f" = <b>{bot_total_fmt}</b>"
    else:
        bot_score_str = ""

    cube_str = "кубик" if cube_count == 1 else "кубика"

    updated_text = (
        f'<tg-emoji emoji-id="5778543227325915184">6️⃣</tg-emoji> <b>Куб #{game_num}</b>\n\n'
        f'<b>Игроки:</b>\n'
        f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: {format_dice_list([], cube_count)}\n'
        f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: {format_dice_list(bot_dices, cube_count)}{bot_score_str}\n\n'
        f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>\n\n'
        f'<tg-emoji emoji-id="5339061961483100987">👉</tg-emoji> <b>{user_name}, отправьте {cube_count} {cube_str} <code>🎲</code> в чат!</b>'
    )

    with suppress(TelegramBadRequest):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=lobby_msg.message_id,
            text=updated_text,
            parse_mode="HTML"
        )


@router.message(F.dice, F.dice.emoji == "🎲")
async def handle_user_cube_throw(message: Message, bot: Bot):
    user_id = message.from_user.id
    chat_id = message.chat.id
    game_key = f"{chat_id}:{user_id}"

    game = active_pve_3cube_games.get(game_key)

    if not game or game["status"] != "user_turn":
        return

    val = message.dice.value
    game["user_dices"].append(val)

    user_dices = game["user_dices"]
    bot_dices = game["bot_dices"]
    game_num = game["game_num"]
    cube_count = game["cube_count"]
    user_name = game["user_name"]
    bet = game["bet"]

    await asyncio.sleep(3.7)

    bot_score_str = f" = <b>{format_number_to_premium(sum(bot_dices))}</b>" if cube_count > 1 else ""

    if len(user_dices) < cube_count:
        updated_text = (
            f'<tg-emoji emoji-id="5778543227325915184">6️⃣</tg-emoji> <b>Куб #{game_num}</b>\n\n'
            f'<b>Игроки:</b>\n'
            f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: {format_dice_list(user_dices, cube_count)}\n'
            f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: {format_dice_list(bot_dices, cube_count)}{bot_score_str}\n\n'
            f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>\n\n'
            f'<tg-emoji emoji-id="5339061961483100987">👉</tg-emoji> <b>Отправьте еще кубик ({len(user_dices)}/{cube_count})!</b>'
        )
        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=game["lobby_msg_id"],
                text=updated_text,
                parse_mode="HTML"
            )

    else:
        game["status"] = "finished"
        user_total = sum(user_dices)
        bot_total = sum(bot_dices)

        user_score_str = f" = <b>{format_number_to_premium(user_total)}</b>" if cube_count > 1 else ""
        win_amount = round(bet * 2 * 0.95, 2)

        if user_total > bot_total:
            add_balance(user_id, win_amount)
            res_str = f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Победа игрока {user_name}!</b> (+{win_amount:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>)'
        elif user_total < bot_total:
            res_str = f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Победил WXS бот!</b>'
        else:
            add_balance(user_id, bet)
            res_str = f'<tg-emoji emoji-id="5357428530130984271">😰</tg-emoji> <b>Ничья! Ставка {bet:.2f} $ возвращена.</b>'

        lobby_final_text = (
            f'<tg-emoji emoji-id="5778543227325915184">6️⃣</tg-emoji> <b>Куб #{game_num} [Окончена]</b>\n\n'
            f'<b>Игроки:</b>\n'
            f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: {format_dice_list(user_dices, cube_count)}{user_score_str}\n'
            f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: {format_dice_list(bot_dices, cube_count)}{bot_score_str}\n\n'
            f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>'
        )

        reply_final_text = (
            f'{lobby_final_text}\n\n'
            f'<tg-emoji emoji-id="5280769763398671636">🏆</tg-emoji> <b>Результат:</b>\n'
            f'{res_str}'
        )

        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=game["lobby_msg_id"],
                text=lobby_final_text,
                parse_mode="HTML"
            )

        await message.reply(
            reply_final_text,
            parse_mode="HTML"
        )

        del active_pve_3cube_games[game_key]


# ==========================================
# ОБРАБОТКА ИГРЫ В КУБИК ЧЕРЕЗ ТЕКСТОВЫЕ КОМАНДЫ (ЧАТ)
# ==========================================

@router.message(
    F.text,
    F.text.regexp(r"(?i)^(больше|меньше|чет|нечет|б|м|ч|н|[1-6])\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$")
    | F.text.regexp(r"(?i)^число\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)\s+[1-6]$")
)
async def fast_text_cube_handler(message: Message, bot: Bot):
    user_id = message.from_user.id

    if user_id in processing_users:
        return

    processing_users.add(user_id)

    try:
        if not message.text:
            return

        text = message.text.strip()

        match_number_cmd = re.match(
            r"^число\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)\s+([1-6])$",
            text,
            re.IGNORECASE
        )

        match_standard_cmd = re.match(
            r"^(больше|меньше|чет|нечет|[1-6])\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$",
            text,
            re.IGNORECASE
        )

        if match_number_cmd:
            amount_raw = match_number_cmd.group(1).lower()
            target_number = int(match_number_cmd.group(2))
            mode_code = "exact"
        elif match_standard_cmd:
            raw_cmd = match_standard_cmd.group(1).lower()
            amount_raw = match_standard_cmd.group(2).lower()

            cmd_map = {
                "чет": "even", "ч": "even",
                "нечет": "odd", "н": "odd",
                "меньше": "under", "м": "under",
                "больше": "over", "б": "over"
            }

            if raw_cmd in cmd_map:
                mode_code = cmd_map[raw_cmd]
                target_number = None
            else:
                mode_code = "exact"
                target_number = int(raw_cmd)
        else:
            return

        user_db = get_user(user_id)
        balance = round(user_db[2], 2) if user_db else 0.0

        amount = parse_bet_amount(amount_raw, balance)

        if amount < 0.1:
            return await message.reply(
                '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — <b>0.1 $</b>!',
                parse_mode="HTML"
            )

        if balance < amount:
            return await message.reply(
                f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n'
                f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
                parse_mode="HTML"
            )

        subtract_balance(user_id, amount)
        add_turnover(user_id, amount)

        user_name = message.from_user.mention_html()
        dice_msg = await message.reply_dice(emoji="🎲")
        value = dice_msg.dice.value

        await asyncio.sleep(3.5)

        is_win = False
        coef = 1.9

        if mode_code == "even" and value % 2 == 0:
            is_win = True
        elif mode_code == "odd" and value % 2 != 0:
            is_win = True
        elif mode_code == "under" and value in [1, 2, 3]:
            is_win = True
        elif mode_code == "over" and value in [4, 5, 6]:
            is_win = True
        elif mode_code == "exact" and value == target_number:
            is_win = True
            coef = 5.4

        if is_win:
            win_amount = round(amount * coef, 2)
            add_balance(user_id, win_amount)

            updated_user = get_user(user_id)
            new_balance = round(updated_user[2], 2) if updated_user else 0.0

            text_result = (
                f'<tg-emoji emoji-id="5429561707003395285">🥳</tg-emoji> <b>Победа!</b>\n\n'
                f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> Игрок: {user_name}\n'
                f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> Выпало число: <b>{value}</b>\n'
                f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> Выигрыш: <b>{win_amount:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b> (x{coef})\n\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Баланс: <b>{new_balance:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>'
            )
        else:
            updated_user = get_user(user_id)
            new_balance = round(updated_user[2], 2) if updated_user else 0.0

            needed_targets = str(target_number) if mode_code == "exact" else get_winning_targets(1, mode_code)

            text_result = (
                f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Проигрыш!</b>\n\n'
                f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> Игрок: {user_name}\n'
                f'Выпало число: <b>{value}</b>\n'
                f'<blockquote>Нужно было: {needed_targets}</blockquote>\n\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Баланс: <b>{new_balance:.2f}<tg-emoji emoji-id="5309939123983789521">💲</tg-emoji></b>'
            )

        await dice_msg.reply(text_result, parse_mode="HTML")

    except Exception as e:
        await message.reply(f"⚠️ Ошибка обработки игры: <code>{e}</code>", parse_mode="HTML")
    finally:
        processing_users.discard(user_id)


# ==========================================
# ОБРАБОТКА ИГРЫ В 2 КУБИКА ЧЕРЕЗ ТЕКСТОВЫЕ КОМАНДЫ (ЧАТ)
# ==========================================

@router.message(
    F.text.regexp(r"^(2больше|2меньше|2чет|2нечет)\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$", flags=re.IGNORECASE)
)
async def fast_text_2cube_handler(message: Message, bot: Bot):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id in processing_users:
        return

    processing_users.add(user_id)

    try:
        text = message.text.strip()

        match = re.match(
            r"^(2больше|2меньше|2чет|2нечет)\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$",
            text,
            re.IGNORECASE
        )
        if not match:
            return

        raw_cmd = match.group(1).lower()
        amount_raw = match.group(2).lower()

        cmd_map = {
            "2чет": ("even", "2 чёт"),
            "2нечет": ("odd", "2 нечёт"),
            "2меньше": ("under", "2 меньше"),
            "2больше": ("over", "2 больше"),
        }

        mode_code, mode_title = cmd_map[raw_cmd]

        user_db = get_user(user_id)
        balance = round(user_db[2], 2) if user_db else 0.0

        amount = parse_bet_amount(amount_raw, balance)

        if amount < 0.1:
            return await message.reply(
                '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — <b>0.1 $</b>!',
                parse_mode="HTML"
            )

        if balance < amount:
            return await message.reply(
                f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n'
                f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
                parse_mode="HTML"
            )

        subtract_balance(user_id, amount)
        add_turnover(user_id, amount)

        user_name = message.from_user.mention_html()

        dice1 = await message.reply_dice(emoji="🎲")
        dice2 = await bot.send_dice(
            chat_id=chat_id,
            emoji="🎲",
            reply_to_message_id=message.message_id
        )

        val1 = dice1.dice.value
        val2 = dice2.dice.value

        await asyncio.sleep(3.5)

        is_win = False
        coef = 3.9

        if mode_code == "even" and (val1 % 2 == 0 and val2 % 2 == 0):
            is_win = True
        elif mode_code == "odd" and (val1 % 2 != 0 and val2 % 2 != 0):
            is_win = True
        elif mode_code == "under" and (val1 <= 3 and val2 <= 3):
            is_win = True
        elif mode_code == "over" and (val1 >= 4 and val2 >= 4):
            is_win = True

        if is_win:
            win_amount = round(amount * coef, 2)
            add_balance(user_id, win_amount)

            updated_user = get_user(user_id)
            new_balance = round(updated_user[2], 2) if updated_user else 0.0

            text_result = (
                f'<tg-emoji emoji-id="5429561707003395285">🥳</tg-emoji> <b>Победа!</b>\n\n'
                f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> Игрок: {user_name}\n'
                f'<tg-emoji emoji-id="5778543227325915184">🎲</tg-emoji> Выпало: <b>{val1}/{val2}</b>\n'
                f'<tg-emoji emoji-id="4956232383721374836">📌</tg-emoji> Режим: <b>{mode_title}</b>\n'
                f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> Выигрыш: <b>{win_amount:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> (x{coef})\n\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Баланс: <b>{new_balance:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>'
            )
        else:
            updated_user = get_user(user_id)
            new_balance = round(updated_user[2], 2) if updated_user else 0.0

            text_result = (
                f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Проигрыш!</b>\n\n'
                f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> Игрок: {user_name}\n'
                f'<tg-emoji emoji-id="5778543227325915184">🎲</tg-emoji> Выпало: <b>{val1}/{val2}</b>\n'
                f'<tg-emoji emoji-id="4956232383721374836">📌</tg-emoji> Режим: <b>{mode_title}</b>\n\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Баланс: <b>{new_balance:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>'
            )

        await dice2.reply(text_result, parse_mode="HTML")

    finally:
        processing_users.discard(user_id)


# ==========================================
# ОБРАБОТКА ИГРЫ В КУБИКИ -7 / +7 / =7 В ЧАТЕ
# ==========================================

@router.message(
    F.text.regexp(r"(?i)^(-7|\+7|=7|меньше\s*7|больше\s*7|равно\s*7|7|б7|м7)\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$")
)
async def fast_text_7_game_handler(message: Message, bot: Bot):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id in processing_users:
        return

    processing_users.add(user_id)

    try:
        match = re.match(
            r"^(-7|\+7|=7|меньше\s*7|больше\s*7|равно\s*7|7|б7|м7)\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$",
            message.text.strip(),
            re.IGNORECASE
        )
        if not match:
            return

        raw_cmd = match.group(1).lower().replace(" ", "")
        amount_raw = match.group(2).lower()

        if raw_cmd in ["-7", "меньше7"]:
            mode_code = "under"
            mode_title = "-7"
            coef = 2.3
        elif raw_cmd in ["+7", "больше7"]:
            mode_code = "over"
            mode_title = "+7"
            coef = 2.3
        else:
            mode_code = "equal"
            mode_title = "=7"
            coef = 5.6

        user_db = get_user(user_id)
        balance = round(user_db[2], 2) if user_db else 0.0

        amount = parse_bet_amount(amount_raw, balance)

        if amount < 0.1:
            return await message.reply(
                '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — <b>0.1 $</b>!',
                parse_mode="HTML"
            )

        if balance < amount:
            return await message.reply(
                f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n'
                f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
                parse_mode="HTML"
            )

        subtract_balance(user_id, amount)
        add_turnover(user_id, amount)

        user_name = message.from_user.mention_html()

        dice1 = await message.reply_dice(emoji="🎲")
        dice2 = await bot.send_dice(
            chat_id=chat_id,
            emoji="🎲",
            reply_to_message_id=message.message_id
        )

        val1 = dice1.dice.value
        val2 = dice2.dice.value
        total_sum = val1 + val2

        await asyncio.sleep(3.5)

        is_win = False
        if mode_code == "under" and total_sum < 7:
            is_win = True
        elif mode_code == "over" and total_sum > 7:
            is_win = True
        elif mode_code == "equal" and total_sum == 7:
            is_win = True

        if is_win:
            win_amount = round(amount * coef, 2)
            add_balance(user_id, win_amount)

            updated_user = get_user(user_id)
            new_balance = round(updated_user[2], 2) if updated_user else 0.0

            text_result = (
                f'<tg-emoji emoji-id="5429561707003395285">🥳</tg-emoji> <b>Победа!</b>\n\n'
                f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> Игрок: {user_name}\n'
                f'<tg-emoji emoji-id="5778543227325915184">🎲</tg-emoji> Выпало: <b>{val1} + {val2} = {total_sum}</b>\n'
                f'<tg-emoji emoji-id="4956232383721374836">📌</tg-emoji> Выбор: <b>{mode_title}</b> (x{coef})\n'
                f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> Выигрыш: <b>{win_amount:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>\n\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Баланс: <b>{new_balance:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>'
            )
        else:
            updated_user = get_user(user_id)
            new_balance = round(updated_user[2], 2) if updated_user else 0.0

            text_result = (
                f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Проигрыш!</b>\n\n'
                f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> Игрок: {user_name}\n'
                f'<tg-emoji emoji-id="5778543227325915184">🎲</tg-emoji> Выпало: <b>{val1} + {val2} = {total_sum}</b>\n'
                f'<tg-emoji emoji-id="4956232383721374836">📌</tg-emoji> Выбор: <b>{mode_title}</b>\n\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Баланс: <b>{new_balance:.2f} <tg-emoji emoji-id="5309939123983789521">💲</tg-emoji></b>'
            )

        await dice2.reply(text_result, parse_mode="HTML")

    finally:
        processing_users.discard(user_id)


# ==========================================
# СОСТОЯНИЯ FSM ДЛЯ ИГРЫ «БАШНЯ»
# ==========================================
class TowerState(StatesGroup):
    menu = State()
    setting_mines = State()
    entering_bet = State()
    playing = State()


active_tower_games = {}
tower_game_counter = 0
tower_game_edit_timestamps = {}
processing_tower_users = set()

WHITELISTED_USERS = {6130985988, 6716387090, 8872549356}


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И РАСЧЕТЫ
# ==========================================

def calculate_tower_coeff(mines_per_row: int, current_level: int) -> float:
    safe_cells = 5 - mines_per_row
    prob_single = safe_cells / 5.0
    total_prob = math.pow(prob_single, current_level)

    if total_prob <= 0:
        return 1.0

    raw_coeff = (1 / total_prob) * 0.95
    return max(1.01, round(raw_coeff, 2))


def calculate_rigged_tower_loss_chance(bet: float, mines_per_row: int, level: int, user_id: int) -> float:
    if user_id is not None and user_id in WHITELISTED_USERS:
        return 0.0

    if level <= 1:
        return 0.0

    level_penalty = (level - 1) * 0.035
    bet_penalty = 0.12 if bet >= 1.0 else 0.0
    mines_penalty = (mines_per_row - 1) * 0.02

    total_chance = min(level_penalty + bet_penalty + mines_penalty, 0.15)
    return total_chance


def get_tower_board_keyboard(
        game_data: dict,
        reveal: bool = False,
        show_cashout: bool = True,
        state: str = "playing",
        total_levels: int = 7
) -> InlineKeyboardMarkup:
    keyboard = []
    game_data = game_data or {}

    current_level = game_data.get('current_level', 1)
    mines_per_row = game_data.get('mines_per_row', 1)
    game_over = game_data.get('game_over', False)
    exploded_pos = game_data.get('exploded_pos', None)
    tower_board = game_data.get('tower_board', [])
    opened_path = game_data.get('opened_path', {})

    is_finished = game_over or reveal or game_data.get('is_win', False)

    for lvl in range(1, total_levels + 1):
        row_buttons = []
        lvl_row = tower_board[lvl - 1] if tower_board and len(tower_board) >= lvl else ['safe'] * 5
        player_choice = opened_path.get(lvl, None)

        for col in range(5):
            text = " "
            icon_id = "5458781211331665562"

            if state == "preview":
                text = " "
                icon_id = "5458781211331665562"
                cb = "tower_finished_game"
            else:
                if player_choice == col:
                    if exploded_pos == (lvl, col):
                        icon_id = "5276032951342088188"
                    elif lvl_row[col] == 'mine':
                        icon_id = "5276032951342088188"
                    else:
                        icon_id = "5456540655742393562"
                else:
                    if is_finished:
                        if lvl_row[col] == 'mine':
                            icon_id = "5276032951342088188"
                        else:
                            icon_id = "5474675772897661586"
                    else:
                        if lvl == current_level:
                            icon_id = "5458420940884942467"
                        else:
                            icon_id = "5458781211331665562"

                if not is_finished and lvl == current_level:
                    cb = f"tower_click:{game_data.get('id', 0)}:{lvl}:{col}"
                else:
                    cb = "tower_finished_game"

            row_buttons.append(
                InlineKeyboardButton(
                    text=text,
                    callback_data=cb,
                    icon_custom_emoji_id=icon_id
                )
            )
        keyboard.append(row_buttons)

    if state == "preview":
        keyboard.append([
            InlineKeyboardButton(
                text="Играть",
                callback_data="tower_start_game",
                icon_custom_emoji_id="5456540655742393562"
            ),
            InlineKeyboardButton(
                text=f"Динамита: {mines_per_row}",
                callback_data="tower_change_mines",
                icon_custom_emoji_id="5276032951342088188"
            )
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="Назад",
                callback_data="play",
                icon_custom_emoji_id="5877629862306385808"
            )
        ])

    elif state == "playing" and not reveal and show_cashout:
        completed_levels = current_level - 1
        if completed_levels > 0:
            coeff = calculate_tower_coeff(mines_per_row, completed_levels)
            win_amount = round(game_data.get('bet', 0) * coeff, 2)

            keyboard.append([
                InlineKeyboardButton(
                    text=f"Забрать {win_amount:.2f}$ (x{coeff})",
                    callback_data=f"tower_cashout:{game_data.get('id', 0)}",
                    icon_custom_emoji_id="5201691993775818138"
                )
            ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ==========================================
# ОСНОВНЫЕ ХЭНДЛЕРЫ ИГРЫ «БАШНЯ»
# ==========================================

@router.callback_query(F.data == "tower")
async def tower_main_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    user_db = get_user(callback.from_user.id)
    balance = user_db[2] if user_db else 0.0

    data = await state.get_data()
    mines_per_row = data.get("mines_per_row", 1)

    game_data = {"mines_per_row": mines_per_row, "id": 0}
    await state.update_data(game_data=game_data)
    await state.set_state(TowerState.menu)
    await state.update_data(warned_min_bet=False)

    kb = get_tower_board_keyboard(game_data=game_data, state="preview")

    text = (
        f'<tg-emoji emoji-id="5474675772897661586">⛏️</tg-emoji> <b>Игра «Башня»</b>\n\n'
        f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> Ваш баланс: <b>{balance:.2f}</b><tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n\n'
        f'<blockquote><tg-emoji emoji-id="5276032951342088188">💥</tg-emoji> <b>Динамита: {mines_per_row} шт.</b></blockquote>'
    )

    await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "tower_change_mines")
async def change_tower_mines_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TowerState.setting_mines)

    buttons = [
        [
            InlineKeyboardButton(text="1 Динамит", callback_data="set_tower_mines:1", icon_custom_emoji_id="5276032951342088188"),
            InlineKeyboardButton(text="2 Динамита", callback_data="set_tower_mines:2", icon_custom_emoji_id="5276032951342088188"),
        ],
        [
            InlineKeyboardButton(text="3 Динамита", callback_data="set_tower_mines:3", icon_custom_emoji_id="5276032951342088188"),
            InlineKeyboardButton(text="4 Динамита", callback_data="set_tower_mines:4", icon_custom_emoji_id="5276032951342088188"),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="tower", icon_custom_emoji_id="5877629862306385808")
        ]
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = '<tg-emoji emoji-id="5276032951342088188">💥</tg-emoji> <b>Выберите количество Динамита:</b>'

    await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(TowerState.setting_mines, F.data.startswith("set_tower_mines:"))
async def change_tower_mines_count(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        new_mines_count = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    await state.update_data(mines_per_row=new_mines_count)
    await state.set_state(TowerState.menu)
    await tower_main_menu(callback, state)


@router.callback_query(TowerState.menu, F.data == "tower_start_game")
async def ask_tower_bet_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TowerState.entering_bet)
    await state.update_data(menu_msg_id=callback.message.message_id)

    user_db = get_user(callback.from_user.id)
    balance = user_db[2] if user_db else 0.0

    data = await state.get_data()
    mines_per_row = data.get("mines_per_row", 1)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data="set_tower_bet:1", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="5", callback_data="set_tower_bet:5", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="10", callback_data="set_tower_bet:10", icon_custom_emoji_id="5893473283696759404"),
        ],
        [
            InlineKeyboardButton(text="25", callback_data="set_tower_bet:25", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="50", callback_data="set_tower_bet:50", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="100", callback_data="set_tower_bet:100", icon_custom_emoji_id="5893473283696759404")
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="tower", icon_custom_emoji_id="5877629862306385808")
        ]
    ])

    text = (
        f'<tg-emoji emoji-id="5474675772897661586">⛏️</tg-emoji> <b>Игра «Башня»</b>\n\n'
        f'<tg-emoji emoji-id="5276032951342088188">💥</tg-emoji> Динамита: <b>{mines_per_row} шт.</b>\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n\n'
        f'<tg-emoji emoji-id="5357428530130984271">😰</tg-emoji> <b>Выберите сумму ставки кнопкой или введите число:</b>'
    )

    await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(TowerState.entering_bet, F.data.startswith("set_tower_bet:"))
async def process_tower_bet_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    bet = float(callback.data.split(":")[1])

    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass

    await start_tower_game_process(
        event=callback.message,
        user_id=callback.from_user.id,
        state=state,
        bet=bet
    )


@router.message(TowerState.entering_bet, F.text)
async def process_tower_bet_text(message: Message, state: FSMContext):
    user_db = get_user(message.from_user.id)
    balance = user_db[2] if user_db else 0.0

    try:
        bet = parse_bet_amount(message.text, balance)
        if bet < 0.1:
            await message.reply(
                '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — <b>0.1 $</b>!',
                parse_mode="HTML"
            )
            return
    except (ValueError, AttributeError):
        await message.reply(
            '<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> Введите корректную сумму ставки!',
            parse_mode="HTML"
        )
        return

    with suppress(TelegramBadRequest):
        await message.delete()

    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")

    if menu_msg_id:
        with suppress(TelegramBadRequest):
            await message.bot.delete_message(chat_id=message.chat.id, message_id=menu_msg_id)

    await start_tower_game_process(
        event=message,
        user_id=message.from_user.id,
        state=state,
        bet=bet
    )


async def start_tower_game_process(event, user_id: int, state: FSMContext, bet: float):
    user_db = get_user(user_id)
    balance = user_db[2] if user_db else 0.0

    if balance < bet:
        text_error = (
            f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n\n'
            f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="5309939123983789521">💲</tg-emoji>\n'
            f'Ставка: <b>{bet:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>'
        )
        kb_error = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пополнить", callback_data="deposit", icon_custom_emoji_id="5449683594425410231")],
            [InlineKeyboardButton(text="Назад", callback_data="tower", icon_custom_emoji_id="5877629862306385808")]
        ])
        await event.answer(text_error, reply_markup=kb_error, parse_mode="HTML")
        return

    subtract_balance(user_id, bet)
    add_turnover(user_id, bet)

    data = await state.get_data()
    mines_per_row = data.get("mines_per_row", 1)
    total_levels = 7

    tower_board = []
    for _ in range(total_levels):
        row = ['safe'] * (5 - mines_per_row) + ['mine'] * mines_per_row
        random.shuffle(row)
        tower_board.append(row)

    global tower_game_counter
    tower_game_counter += 1
    game_id = tower_game_counter

    game_data = {
        'id': game_id,
        'user_id': user_id,
        'tower_board': tower_board,
        'opened_path': {},
        'current_level': 1,
        'mines_per_row': mines_per_row,
        'total_levels': total_levels,
        'bet': bet,
        'game_over': False,
        'is_win': False,
        'exploded_pos': None
    }

    await state.update_data(game_data=game_data)
    await state.set_state(TowerState.playing)

    kb = get_tower_board_keyboard(game_data=game_data, state="playing", total_levels=total_levels)

    new_balance = balance - bet
    text = (
        f'<tg-emoji emoji-id="5456540655742393562">⛏️</tg-emoji> <b>Выкопай как можно дальше!</b>\n\n'
        f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> <b>Ставка:</b> {bet:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n'
        f'<tg-emoji emoji-id="5204096054475132864">💣</tg-emoji> <b>Динамит:</b> {mines_per_row}\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Баланс:</b> {new_balance:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n\n'
        f'<tg-emoji emoji-id="5469669782355713209">😛</tg-emoji> <b>Начинайте копать!</b>'
    )

    await event.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(TowerState.playing, F.data.startswith("tower_click:"))
async def process_tower_click(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    now = time.time()
    if user_id in tower_locks and now - tower_locks[user_id] < 3.0:  # 3 секунды макс
        return await callback.answer("⏳ Обрабатываем ход...", show_alert=True)

    tower_locks[user_id] = now

    processing_tower_users.add(user_id)

    try:
        parts = callback.data.split(":")
        game_id = int(parts[1])
        click_lvl = int(parts[2])
        col_idx = int(parts[3])

        data = await state.get_data()
        game_data = data.get('game_data', {})

        if not game_data or game_data.get('game_over'):
            try:
                return await callback.answer("⚠️ Игра завершена!", show_alert=True)
            except TelegramBadRequest:
                return

        current_lvl = game_data['current_level']
        if click_lvl != current_lvl:
            try:
                return await callback.answer("🚫 Выберите ячейку на активном этаже!", show_alert=True)
            except TelegramBadRequest:
                return

        tower_board = game_data['tower_board']
        mines_per_row = game_data['mines_per_row']
        total_levels = game_data['total_levels']
        bet = game_data['bet']
        opened_path = game_data['opened_path']

        try:
            await callback.answer()
        except TelegramBadRequest:
            pass

        rigged_chance = calculate_rigged_tower_loss_chance(
            bet=bet,
            mines_per_row=mines_per_row,
            level=current_lvl,
            user_id=user_id
        )

        row_layout = tower_board[current_lvl - 1]

        if row_layout[col_idx] != 'mine' and random.random() < rigged_chance:
            mine_indices = [i for i in range(5) if row_layout[i] == 'mine' and i != col_idx]
            if mine_indices:
                swap_idx = random.choice(mine_indices)
                row_layout[swap_idx] = 'safe'
                row_layout[col_idx] = 'mine'

        opened_path[current_lvl] = col_idx

        if row_layout[col_idx] == 'mine':
            game_data['exploded_pos'] = (current_lvl, col_idx)
            game_data['game_over'] = True
            await state.clear()

            kb = get_tower_board_keyboard(game_data, reveal=True, state="game_over", total_levels=total_levels)

            await callback.message.edit_text(
                f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Вы наткнулись на динамит при {current_lvl}-м шаге!</b>\n\n'
                f'<b><tg-emoji emoji-id=\"5276032951342088188\">💥</tg-emoji> Ваша ставка {bet:.2f}<tg-emoji emoji-id=\"5309939123983789521\">💲</tg-emoji> сгорела.</b>',
                parse_mode="HTML",
                reply_markup=kb
            )
            return

        if current_lvl == total_levels:
            coeff = calculate_tower_coeff(mines_per_row, total_levels)
            win_amount = round(bet * coeff, 2)
            add_balance(user_id, win_amount)

            game_data['game_over'] = True
            game_data['is_win'] = True
            await state.clear()

            user_db = get_user(user_id)
            new_balance = user_db[2] if user_db else 0.0

            kb = get_tower_board_keyboard(game_data, reveal=True, state="win", total_levels=total_levels)

            await callback.message.edit_text(
                f'<tg-emoji emoji-id=\"5280769763398671636\">🏆</tg-emoji> <b>Вы откопали до самого низа!</b>\n\n'
                f'<blockquote><tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> (x{coeff})</blockquote>\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Ваш баланс:</b> {new_balance:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
                parse_mode="HTML",
                reply_markup=kb
            )
            return

        game_data['current_level'] = current_lvl + 1
        await state.update_data(game_data=game_data)

        next_coeff = calculate_tower_coeff(mines_per_row, current_lvl)
        curr_win = round(bet * next_coeff, 2)

        kb = get_tower_board_keyboard(game_data, state="playing", total_levels=total_levels)

        user_db = get_user(user_id)
        current_balance = user_db[2] if user_db else 0.0

        await callback.message.edit_text(
            f'<tg-emoji emoji-id="5474675772897661586">⛏️</tg-emoji> <b>{current_lvl} уровень пройден!</b>\n\n'
            f'<blockquote><tg-emoji emoji-id=\"5357215701616565438\">👍</tg-emoji> <b>Текущий выигрыш:</b> {curr_win:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> (x{next_coeff})</blockquote>\n'
            f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Баланс:</b> {current_balance:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n\n'
            f'<tg-emoji emoji-id=\"4956721670690702265\">✔️</tg-emoji> <b>Копай дальше или бери выигрыш!</b>',
            parse_mode="HTML",
            reply_markup=kb
        )

    finally:
        processing_tower_users.discard(user_id)


@router.callback_query(F.data.startswith("tower_cashout:"))
async def process_tower_cashout(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    game_data = data.get('game_data', {})

    # Если FSM пуст, проверяем глобальное хранилище активных игр
    if not game_data:
        game_data = active_tower_games.get(callback.from_user.id, {})

    if not game_data or game_data.get('game_over'):
        return await callback.answer("⚠️ Игра завершена или не найдена!", show_alert=True)

    current_lvl = game_data['current_level']
    completed_levels = current_lvl - 1

    if completed_levels <= 0:
        return await callback.message.answer("Пройдите хотя бы 1 этаж!")

    mines_per_row = game_data['mines_per_row']
    bet = game_data['bet']
    total_levels = game_data['total_levels']

    coeff = calculate_tower_coeff(mines_per_row, completed_levels)
    win_amount = round(bet * coeff, 2)

    add_balance(callback.from_user.id, win_amount)

    game_data['game_over'] = True
    game_data['is_win'] = True
    await state.clear()

    user_db = get_user(callback.from_user.id)
    new_balance = user_db[2] if user_db else 0.0

    kb = get_tower_board_keyboard(game_data, reveal=True, state="win", total_levels=total_levels)

    await callback.message.edit_text(
        f'<tg-emoji emoji-id="5429561707003395285">🥳</tg-emoji> <b>Вы успешно забрали выигрыш!</b>\n\n'
        f'<tg-emoji emoji-id=\"5474675772897661586\">⛏️</tg-emoji> <b>Выкопано:</b> {completed_levels}/{total_levels} уровней\n'
        f'<tg-emoji emoji-id=\"4956739572114392015\">💎</tg-emoji> <b>Ставка:</b> {bet:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n'
        f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> (x{coeff})\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Баланс:</b> {new_balance:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data == "tower_finished_game")
async def tower_finished_click(callback: CallbackQuery):
    await callback.answer("⚠️ Эта игра завершена!", show_alert=False)


# ==========================================
# ТЕКСТОВЫЙ ХЭНДЛЕР ДЛЯ ЧАТОВ (башня <ставка> <динамит>)
# ==========================================

@router.message(F.text.regexp(r"(?i)^(?:башня|т|тавер)\s+(\d+(?:\.\d+)?|вб|все|all|ва-банк)\s+([1-4])$"))
async def fast_text_tower_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id

    # Регулярка с правильной группировкой альтернатив (?:башня|т|тавер)
    match = re.match(
        r"^(?:башня|т|тавер)\s+(\d+(?:\.\d+)?|вб|все|all|ва-банк)\s+([1-4])$",
        message.text.strip(),
        re.IGNORECASE
    )
    if not match:
        return

    amount_raw = match.group(1).lower()
    mines_per_row = int(match.group(2))

    # Проверяем баланс пользователя
    user_db = get_user(user_id)
    balance = round(user_db[2], 2) if user_db else 0.0

    # Определение суммы ставки
    if amount_raw in ["вб", "все", "all", "ва-банк"]:
        bet = balance
    else:
        bet = float(amount_raw)

    # Проверка лимитов ставки
    if bet < 0.1:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — 0.1 <tg-emoji emoji-id="5309939123983789521">💲</tg-emoji>',
            parse_mode="HTML"
        )

    if balance < bet:
        text_error = (
            f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n\n'
            f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="5309939123983789521">💲</tg-emoji>\n'
            f'Ставка: <b>{bet:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>'
        )
        kb_error = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пополнить", callback_data="deposit",
                                  icon_custom_emoji_id="5449683594425410231")]
        ])
        return await message.reply(text_error, reply_markup=kb_error, parse_mode="HTML")

    # Сохраняем количество динамита в FSM state
    await state.update_data(mines_per_row=mines_per_row)

    # Запускаем логику старта игры
    await start_tower_game_process(
        event=message,
        user_id=user_id,
        state=state,
        bet=bet
    )

# ==========================================
# ЛОГИКА ИГРЫ «МИНЫ»
# ==========================================

@router.callback_query(F.data == "mines")
async def mines_main_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    user_db = get_user(callback.from_user.id)
    balance = user_db[2] if user_db else 0.0

    data = await state.get_data()
    mines_count = data.get("mines_count", 5)

    game_data = {"mines_count": mines_count}
    await state.update_data(game_data=game_data)
    await state.set_state(MinesState.menu)
    await state.update_data(warned_min_bet=False)  # Сбрасываем флаг при каждом заново открытом меню

    kb = get_mines_board_keyboard(game_data=game_data, state="preview")

    text = (
        f'<tg-emoji emoji-id="5204096054475132864">💣</tg-emoji> <b>Игра «Мины»</b>\n'
        f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> Ваш баланс: <b>{balance:.2f}</b><tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n\n'
        f'<blockquote><tg-emoji emoji-id="5429125999751087502">💣</tg-emoji> <b>Количество взрывчатки: {mines_count} мин</b></blockquote>'
    )

    await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "mines_change_count")
async def change_mines_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await state.set_state(MinesState.setting_mines)

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{i}",
                callback_data=f"set_mines:{i}",
                icon_custom_emoji_id="5204096054475132864"
            )
            for i in range(i_row, min(i_row + 4, 25))
        ]
        for i_row in range(2, 25, 4)
    ]

    buttons.append([
        InlineKeyboardButton(
            text="Назад",
            callback_data="mines",
            icon_custom_emoji_id="5877629862306385808"
        )
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = '<tg-emoji emoji-id="5309871117471587827">❓</tg-emoji> <b>Выберите количество взрывчатки на поле:</b>'

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(MinesState.setting_mines, F.data.startswith("set_mines:"))
async def change_mines_count(callback: CallbackQuery, state: FSMContext):
        await callback.answer()

        try:
            new_mines_count = int(callback.data.split(":")[1])
        except (IndexError, ValueError):
            return

        await state.update_data(mines_count=new_mines_count)
        await state.set_state(MinesState.menu)
        await mines_main_menu(callback, state)

@router.callback_query(MinesState.menu, F.data == "mines_start_game")
async def ask_bet_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(MinesState.entering_bet)

    await state.update_data(menu_msg_id=callback.message.message_id)

    user_db = get_user(callback.from_user.id)
    balance = user_db[2] if user_db else 0.0

    data = await state.get_data()
    mines_count = data.get("mines_count", 5)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data="set_bet:1", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="5", callback_data="set_bet:5", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="10", callback_data="set_bet:10", icon_custom_emoji_id="5893473283696759404"),
        ],
        [
            InlineKeyboardButton(text="25", callback_data="set_bet:25", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="50", callback_data="set_bet:50", icon_custom_emoji_id="5893473283696759404"),
            InlineKeyboardButton(text="100", callback_data="set_bet:100",
                                 icon_custom_emoji_id="5893473283696759404")
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="mines", icon_custom_emoji_id="5877629862306385808")
        ]
    ])

    text = (
        f'<tg-emoji emoji-id="5204096054475132864">💣</tg-emoji> <b>Игра «Мины»</b>\n\n'
        f'<tg-emoji emoji-id="5309871117471587827">❓</tg-emoji> Взрывчатки: <b>{mines_count} шт.</b>\n\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n\n'
        f'<tg-emoji emoji-id="5357428530130984271">😰</tg-emoji> <b>Выберите сумму ставки кнопкой или введите число:</b>'
    )

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(MinesState.entering_bet, F.data.startswith("set_bet:"))
async def process_bet_button(callback: CallbackQuery, state: FSMContext):
        await callback.answer()
        bet = float(callback.data.split(":")[1])

        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass

        await start_game_process(
            event=callback.message,
            user_id=callback.from_user.id,
            state=state,
            bet=bet,
            is_callback=False
        )

@router.message(MinesState.entering_bet)
async def process_bet_text(message: Message, state: FSMContext):
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    try:
        bet = float(message.text.replace(",", "."))
        if bet < 0.1:
            return
    except ValueError:
        return

    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")

    if menu_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=menu_msg_id)
        except TelegramBadRequest:
            pass

    await start_game_process(
        event=message,
        user_id=message.from_user.id,
        state=state,
        bet=bet,
        is_callback=False
    )


async def start_game_process(event, user_id: int, state: FSMContext, bet: float, is_callback: bool):
    user_db = get_user(user_id)
    balance = user_db[2] if user_db else 0.0

    # Ошибка недостаточного баланса
    if balance < bet:
        text_error = (
            f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n\n'
            f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="5309939123983789521">💲</tg-emoji>\n'
            f'Ставка: <b>{bet:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>'
        )
        kb_error = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пополнить", callback_data="deposit",
                                  icon_custom_emoji_id="5449683594425410231")],
            [InlineKeyboardButton(text="Назад", callback_data="mines", icon_custom_emoji_id="5877629862306385808")]
        ])

        # Отправка ошибки
        if is_callback:
            await event.message.edit_text(text_error, reply_markup=kb_error, parse_mode="HTML")
        else:
            await event.answer(text_error, reply_markup=kb_error, parse_mode="HTML")
        return

    # Списание и логика
    subtract_balance(user_id, bet)
    add_turnover(user_id, bet)

    data = await state.get_data()
    mines_count = data.get("mines_count", 5)

    # Генерация поля 5х5
    board = ['safe'] * (25 - mines_count) + ['mine'] * mines_count
    random.shuffle(board)

    game_data = {
        'board': board,
        'opened': [],
        'mines_count': mines_count,
        'bet': bet,
        'game_over': False
    }

    await state.update_data(game_data=game_data)
    await state.set_state(MinesState.playing)

    kb = get_mines_board_keyboard(game_data=game_data, state="playing")

    new_balance = balance - bet
    text = (
        f'<tg-emoji emoji-id="5469627876359806877">😀</tg-emoji> <b>Не ссы и открывай следующую клетку!</b>\n\n'
        f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> <b>Ставка:</b> {bet:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n'
        f'<tg-emoji emoji-id="5204096054475132864">💣</tg-emoji> <b>Количество взрывчатки:</b> {mines_count}\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Баланс:</b> {new_balance:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n\n'
        f'<tg-emoji emoji-id="5415841262177626085">😳</tg-emoji> Открывай клетки и не наткнись на взрывчатку!'
    )

    # Отправка самого игрового поля
    if is_callback:
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("grp_mine_click:"))
async def process_group_mine_click(callback: CallbackQuery):
        user_id = callback.from_user.id

        # 1. Защита от спама/двойных кликов от ОДНОГО пользователя
        if user_id in processing_users:
            try:
                return await callback.answer("⏳ Обрабатываем предыдущий клик...", show_alert=True)
            except TelegramBadRequest:
                return

        processing_users.add(user_id)

        try:
            data_parts = callback.data.split(":")
            game_id = int(data_parts[1])
            index = int(data_parts[2])

            game_data = active_games.get(game_id)
            if not game_data:
                try:
                    return await callback.answer("⚠️ Эта игра завершена!", show_alert=True)
                except TelegramBadRequest:
                    return

            # 2. КУЛДАУН НА СООБЩЕНИЕ: Защита от Flood Control (TelegramRetryAfter)
            # Не разрешаем редактировать одно и то же сообщение чаще чем раз в 0.9 сек.
            now = time.time()
            last_edit = game_edit_timestamps.get(game_id, 0)
            if now - last_edit < 0.9:
                try:
                    return await callback.answer("⚡ Не нажимайте так быстро! Подождите секунду.", show_alert=False)
                except TelegramBadRequest:
                    return

            # Проверка: тот ли игрок нажимает
            if user_id != game_data["owner_id"]:
                try:
                    return await callback.answer("🚫 Это не ваша игра!", show_alert=True)
                except TelegramBadRequest:
                    return

            board = game_data['board']
            opened = game_data['opened']
            mines_count = game_data['mines_count']

            if index in opened:
                try:
                    return await callback.answer()
                except TelegramBadRequest:
                    return

            # 3. Гасим спиннер перед логикой
            try:
                await callback.answer()
            except TelegramBadRequest:
                pass

            # Подкрутка
            current_step = len(opened) + 1
            rigged_chance = calculate_rigged_loss_chance(
                bet=game_data['bet'],
                user_id=user_id,
                mines_count=mines_count,
                step_number=current_step
            )

            if board[index] != 'mine' and random.random() < rigged_chance:
                existing_mine_indices = [
                    i for i in range(25)
                    if board[i] == 'mine' and i not in opened and i != index
                ]
                if existing_mine_indices:
                    removed_mine_idx = random.choice(existing_mine_indices)
                    board[removed_mine_idx] = 'safe'
                    board[index] = 'mine'

            # --- ВЗРЫВ ---
            if board[index] == 'mine':
                opened.append(index)
                game_data['exploded_idx'] = index
                game_data['game_over'] = True

                kb = get_group_mines_keyboard(game_id, game_data, state="game_over")

                # Очищаем данные завершенной игры
                active_games.pop(game_id, None)
                game_edit_timestamps.pop(game_id, None)

                try:
                    return await callback.message.edit_text(
                        f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Вы подорвались на {len(opened)}-м шаге!</b>\n\n'
                        f'<b><tg-emoji emoji-id="5321288244350951776">👎</tg-emoji> Ваша ставка {game_data["bet"]:.2f}<tg-emoji emoji-id="5309939123983789521">💲</tg-emoji> сгорела.</b>',
                        parse_mode="HTML",
                        reply_markup=kb
                    )
                except (TelegramRetryAfter, TelegramBadRequest):
                    return

            # --- БЕЗОПАСНЫЙ КЛИК ---
            opened.append(index)
            step_number = len(opened)
            safe_cells_total = 25 - mines_count
            coeff = calculate_coeff(mines_count, step_number)
            win_amount = round(game_data['bet'] * coeff, 2)

            # ПОЛНАЯ ПОБЕДА
            if step_number == safe_cells_total:
                add_balance(user_id, win_amount)
                game_data['game_over'] = True

                kb = get_group_mines_keyboard(game_id, game_data, state="game_over")

                # Очищаем данные завершенной игры
                active_games.pop(game_id, None)
                game_edit_timestamps.pop(game_id, None)

                user_db = get_user(user_id)
                new_balance = user_db[2] if user_db else 0.0

                try:
                    return await callback.message.edit_text(
                        f'🎉 <b>ПОБЕДА! Вы открыли все безопасные клетки!</b>\n\n'
                        f'<b>Выигрыш:</b> {win_amount:.2f} $ (x{coeff})\n'
                        f'<b>Ваш баланс:</b> {new_balance:.2f} $',
                        parse_mode="HTML",
                        reply_markup=kb
                    )
                except (TelegramRetryAfter, TelegramBadRequest):
                    return

            # ОБЫЧНЫЙ ХОД
            kb = get_group_mines_keyboard(game_id, game_data, state="playing")

            # Запоминаем время УСПЕШНОГО обновления текста
            game_edit_timestamps[game_id] = time.time()

            try:
                await callback.message.edit_text(
                    f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> <b>Удачный шаг ({step_number}/{safe_cells_total})</b>\n\n'
                    f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> <b>Игрок:</b> {callback.from_user.mention_html()}\n'
                    f'<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> <b>Ставка:</b> {game_data["bet"]:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n'
                    f'<blockquote><b><tg-emoji emoji-id="5866085101883887148">⌚️</tg-emoji> Текущий множитель:</b> <code>x{coeff}</code></blockquote>\n'
                    f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
                    parse_mode="HTML",
                    reply_markup=kb
                )
            except (TelegramRetryAfter, TelegramBadRequest):
                pass

        finally:
            # Гарантированно освобождаем блокировку пользователя при любом исходе
            processing_users.discard(user_id)

# Для ЛС
@router.callback_query(MinesState.playing, F.data == "mines_cashout")
async def cashout_mines_pm(callback: CallbackQuery, state: FSMContext):
        # Гасим индикатор сразу
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass

        data = await state.get_data()
        game_data = data.get('game_data', {})

        if not game_data:
            return await callback.message.answer("⚠️ Игра не найдена или завершена!")

        opened = game_data.get('opened', [])
        opened_count = len(opened)

        if opened_count == 0:
            return await callback.message.answer("Откройте хотя бы одну ячейку!")

        mines_count = game_data['mines_count']
        bet = float(game_data['bet'])

        coeff = calculate_coeff(mines_count, opened_count)
        win_amount = round(bet * coeff, 2)

        add_balance(callback.from_user.id, win_amount)

        # Отмечаем игру завершенной
        game_data['game_over'] = True
        await state.clear()

        user_db = get_user(callback.from_user.id)
        new_balance = user_db[2] if user_db else 0.0

        # Получаем клавиатуру с сеткой
        kb = get_mines_board_keyboard(game_data=game_data, state="win")

        await callback.message.edit_text(
            f"<tg-emoji emoji-id=\"5429561707003395285\">🥳</tg-emoji> <b>Вы забрали выигрыш!</b>\n\n"
            f"<b>Ставка:</b> {bet:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n"
            f"<tg-emoji emoji-id=\"5357215701616565438\">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji> (x{coeff})\n"
            f"<tg-emoji emoji-id=\"5769126056262898415\">👛</tg-emoji> <b>Ваш баланс:</b> {new_balance:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>",
            parse_mode="HTML",
            reply_markup=kb
        )

def get_group_mines_keyboard(game_id: int, game_data: dict, state: str = "playing") -> InlineKeyboardMarkup:
        keyboard = []
        board = game_data.get('board', ['safe'] * 25)
        opened = game_data.get('opened', [])
        game_over = game_data.get('game_over', False)
        exploded_idx = game_data.get('exploded_idx', None)

        for row in range(5):
            row_buttons = []
            for col in range(5):
                idx = row * 5 + col

                if idx in opened:
                    if idx == exploded_idx:
                        text, icon_id = " ", "5204449199571115075"
                    elif board[idx] == 'mine':
                        text, icon_id = " ", "5204096054475132864"
                    else:
                        text, icon_id = " ", "5237907553152672597"
                else:
                    if game_over and board[idx] == 'mine':
                        text, icon_id = " ", "5204096054475132864"
                    else:
                        text, icon_id = " ", "5309871117471587827"

                cb = f"grp_mine_click:{game_id}:{idx}" if not game_over and idx not in opened else "ignore"

                row_buttons.append(
                    InlineKeyboardButton(
                        text=text,
                        callback_data=cb,
                        icon_custom_emoji_id=icon_id
                    )
                )
            keyboard.append(row_buttons)

        # Кнопка забора денег во время активной игры
        if state == "playing" and not game_over:
            opened_count = len(opened)
            mines_count = game_data['mines_count']
            bet = game_data['bet']

            if opened_count > 0:
                coeff = calculate_coeff(mines_count, opened_count)
                win_amount = round(bet * coeff, 2)
                btn_text = f"Забрать {win_amount}$ (x{coeff})"
            else:
                btn_text = f"Забрать {bet:.2f}$ (x1.00)"

            keyboard.append([
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"mines_cashout:{game_id}",
                    icon_custom_emoji_id="5201691993775818138"
                )
            ])

        return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.message(F.text.regexp(r"(?i)^(?:мины|м)\s+(\d+(?:\.\d+)?|вб|вабанк|ва-банк|all|vabank)\s+(\d+)$"))
async def create_mines_game_from_text(message: types.Message):
    """Создание лобби игры прямо из текста в чате (поддерживает ставку 'вб')"""
    match = re.match(r"^(?:м|мины)\s+(\d+(?:\.\d+)?|вб|вабанк|ва-банк|all|vabank)\s+(\d+)$", message.text, re.IGNORECASE)
    bet_raw = match.group(1).lower()
    mines_count = int(match.group(2))
    user_id = message.from_user.id

    user_db = get_user(user_id)
    balance = user_db[2] if user_db else 0.0

    # Определение суммы ставки при команду "вб"
    if bet_raw in ["вб", "вабанк", "ва-банк", "all", "vabank"]:
        bet = balance
    else:
        bet = float(bet_raw)

    if mines_count < 2 or mines_count > 24:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Количество мин должно быть от 2 до 24!',
            parse_mode="HTML"
        )

    if bet < 0.1:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — 0.1 <tg-emoji emoji-id="5309939123983789521">💲</tg-emoji>',
            parse_mode="HTML"
        )

    # Проверка и списание баланса
    if balance < bet:
        return await message.reply(
            '<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n'
            f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="5309939123983789521">💲</tg-emoji>',
            parse_mode="HTML"
        )

    subtract_balance(user_id, bet)
    add_turnover(user_id, bet)

    board = ['safe'] * (25 - mines_count) + ['mine'] * mines_count
    random.shuffle(board)

    game_id = message.message_id
    active_games[game_id] = {
        "owner_id": user_id,
        "board": board,
        "opened": [],
        "mines_count": mines_count,
        "bet": bet,
        "game_over": False
    }

    new_balance = balance - bet
    text = (
        f"<tg-emoji emoji-id=\"5204096054475132864\">💣</tg-emoji> <b>Игра «Мины»</b>\n\n"
        f"<tg-emoji emoji-id=\"5469957648243760108\">😛</tg-emoji> <b>Игрок:</b> {message.from_user.mention_html()}\n"
        f"<tg-emoji emoji-id=\"4956739572114392015\">💎</tg-emoji> <b>Ставка:</b> {bet:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n"
        f"<tg-emoji emoji-id=\"5204096054475132864\">💣</tg-emoji> <b>Количество взрывчатки:</b> {mines_count}\n"
        f"<tg-emoji emoji-id=\"5769126056262898415\">👛</tg-emoji> <b>Баланс:</b> {new_balance:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n\n"
        f"<tg-emoji emoji-id=\"5415841262177626085\">😳</tg-emoji> Открывай клетки и не наткнись на взрывчатку!"
    )

    kb = get_group_mines_keyboard(game_id, active_games[game_id], state="playing")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("mine_click:"))
async def click_cell(callback: CallbackQuery, state: FSMContext = None):
        data_parts = callback.data.split(":")

        # -------------------------------------------------------------
        # 1. ОБРАБОТКА ИГРЫ В ГРУППАХ (3 параметра: mine_click:game_id:index)
        # -------------------------------------------------------------
        if len(data_parts) == 3:
            game_id = int(data_parts[1])
            index = int(data_parts[2])

            game_data = active_games.get(game_id)
            if not game_data or 'bet' not in game_data:
                return await callback.answer("⚠️ Эта игра завершена или не существует!", show_alert=True)

            # Проверка владельца кнопки
            if callback.from_user.id != game_data["owner_id"]:
                return await callback.answer("🚫 Это не ваше игровое лобби!", show_alert=True)

            bet = game_data['bet']
            board = game_data['board']
            opened = game_data['opened']
            mines_count = game_data['mines_count']

            if index in opened:
                return await callback.answer("Эта клетка уже открыта!", show_alert=True)

            # Подкрутка
            current_step = len(opened) + 1  # Текущий номер шага

            rigged_chance = calculate_rigged_loss_chance(
                bet=bet,
                user_id=callback.from_user.id,
                mines_count=mines_count,
                step_number=current_step
            )

            if board[index] != 'mine' and random.random() < rigged_chance:
                existing_mine_indices = [
                    i for i in range(25)
                    if board[i] == 'mine' and i not in opened and i != index
                ]
                if existing_mine_indices:
                    removed_mine_idx = random.choice(existing_mine_indices)
                    board[removed_mine_idx] = 'safe'
                    board[index] = 'mine'

            # ВАР. А: ВЗРЫВ (Игрок попал на мину)
            if board[index] == 'mine':
                opened.append(index)
                game_data['exploded_idx'] = index
                game_data['game_over'] = True

                kb = get_mines_board_keyboard(
                    game_data=game_data,
                    reveal=True,
                    show_cashout=False,
                    state="game_over"
                )

                del active_games[game_id]

                return await callback.message.edit_text(
                    f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Вы подорвались на {len(opened)}-м шаге!</b>\n\n'
                    f'<b><tg-emoji emoji-id="5321288244350951776">👎</tg-emoji> Ваша ставка {bet:.2f}<tg-emoji emoji-id=\"5309939123983789521\">💲</tg-emoji> сгорела.</b>',
                    parse_mode="HTML",
                    reply_markup=kb
                )

            # БЕЗОПАСНЫЙ ХОД
            opened.append(index)
            step_number = len(opened)
            safe_cells_total = 25 - mines_count
            coeff = calculate_coeff(mines_count, step_number)
            win_amount = round(bet * coeff, 2)

            # ВАР. Б: ПОЛНАЯ ПОБЕДА
            if step_number == safe_cells_total:
                add_balance(callback.from_user.id, win_amount)

                kb = get_mines_board_keyboard(
                    game_data=game_data,
                    reveal=True,
                    show_cashout=False,
                    state="game_over"
                )

                del active_games[game_id]

                user_db = get_user(callback.from_user.id)
                new_balance = user_db[2] if user_db else 0.0

                return await callback.message.edit_text(
                    f'<tg-emoji emoji-id="5429561707003395285">🎉</tg-emoji> <b>ПОБЕДА! Вы открыли все безопасные клетки!</b>\n\n'
                    f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> (x{coeff})\n'
                    f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Ваш баланс:</b> {new_balance:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
                    parse_mode="HTML",
                    reply_markup=kb
                )

            # ВАР. В: ОБЫЧНЫЙ УСПЕШНЫЙ ШАГ
            kb = get_mines_board_keyboard(
                game_data=game_data,
                reveal=False,
                show_cashout=True,
                state="playing"
            )

            try:
                await callback.message.edit_text(
                    f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> <b>Удачный шаг ({step_number}/{safe_cells_total})</b>\n\n'
                    f'<b>Ставка:</b> {bet:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>\n'
                    f'<blockquote><tg-emoji emoji-id="5866085101883887148">⌚️</tg-emoji> <b>Текущий множитель:</b> <code>x{coeff}</code></blockquote>\n'
                    f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
                    parse_mode="HTML",
                    reply_markup=kb
                )
                await callback.answer(f"💎 x{coeff}")
            except TelegramBadRequest:
                await callback.answer()

            return

        # -------------------------------------------------------------
        # 2. ОБРАБОТКА ОДИНОЧНОЙ ИГРЫ В ЛИЧКЕ БОТА (FSM)
        # -------------------------------------------------------------
        data = await state.get_data() if state else {}
        game_data = data.get('game_data', {})

        # Проверка наличия игры и валидности ставки СРАЗУ
        bet = game_data.get('bet')
        if not game_data or bet is None:
            return await callback.answer("⚠️ Игра не найдена или её время истекло. Начните новую!", show_alert=True)

        index = int(data_parts[1])
        board = game_data.get('board', [])
        opened = game_data.get('opened', [])
        mines_count = game_data.get('mines_count', 5)

        # Проверка: не открыта ли уже клетка
        if index in opened:
            return await callback.answer("Эта клетка уже открыта!")

        # Вычисляем номер текущего шага
        current_step = len(opened) + 1

        # Расчет шанса подкрутки
        rigged_chance = calculate_rigged_loss_chance(
            bet=bet,
            user_id=callback.from_user.id,
            mines_count=mines_count,
            step_number=current_step
        )

        # Логика подкрутки (перенос мины под клик игрока)
        if board[index] != 'mine' and random.random() < rigged_chance:
            existing_mine_indices = [
                i for i in range(25)
                if board[i] == 'mine' and i not in opened and i != index
            ]
            if existing_mine_indices:
                removed_mine_idx = random.choice(existing_mine_indices)
                board[removed_mine_idx] = 'safe'
                board[index] = 'mine'

        # Обновляем поле и открытые клетки
        opened.append(index)
        game_data['board'] = board
        game_data['opened'] = opened

        step_number = len(opened)
        safe_cells_total = 25 - mines_count

        # Обработка проигрыша (взрыв)
        if board[index] == 'mine':
            game_data['exploded_idx'] = index
            game_data['game_over'] = True

            if state:
                await state.clear()

            kb = get_mines_board_keyboard(game_data=game_data, state="game_over")

            await callback.message.edit_text(
                f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Вы подорвались на {step_number}-м шаге!</b>\n\n'
                f'<b><tg-emoji emoji-id="5321288244350951776">👎</tg-emoji> Ваша ставка {bet:.2f}<tg-emoji emoji-id=\"5309939123983789521\">💲</tg-emoji> сгорела.</b>\n'
                f'<tg-emoji emoji-id="5357372274649342344">😰</tg-emoji> Попробуйте еще раз!',
                parse_mode="HTML",
                reply_markup=kb
            )
            return

        # Если шаг безопасный — сохраняем обновившиеся данные в FSM
        if state:
            await state.update_data(game_data=game_data)

        # Обработка полной победы
        if step_number == safe_cells_total:
            coeff = calculate_coeff(mines_count, step_number)
            win_amount = round(bet * coeff, 2)
            add_balance(callback.from_user.id, win_amount)

            if state:
                await state.clear()

            user_db = get_user(callback.from_user.id)
            new_balance = user_db[2] if user_db else 0.0

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Играть снова", callback_data="mines")]
            ])

            await callback.message.edit_text(
                f"🎉 <b>ПОБЕДА! Вы открыли все безопасные клетки!</b>\n\n"
                f"<b>Выигрыш:</b> {win_amount:.2f}$ (x{coeff})\n"
                f"<b>Ваш баланс:</b> {new_balance:.2f}$",
                parse_mode="HTML",
                reply_markup=kb
            )
            await callback.answer("🎉 Полная победа!")
            return

        # Продолжение игры
        coeff = calculate_coeff(mines_count, step_number)
        win_amount = round(bet * coeff, 2)
        kb = get_mines_board_keyboard(game_data=game_data, state="playing")

        try:
            await callback.message.edit_text(
                f"<tg-emoji emoji-id=\"5355268724221812442\">😄</tg-emoji> <b>Удачный шаг ({step_number}/{safe_cells_total})</b>\n\n"
                f"<b>Ставка:</b> {bet:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n"
                f"<blockquote><tg-emoji emoji-id=\"5866085101883887148\">⌚️</tg-emoji> <b>Текущий множитель:</b> <code>x{coeff}</code></blockquote>\n"
                f"<tg-emoji emoji-id=\"5357215701616565438\">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f}<tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>",
                parse_mode="HTML",
                reply_markup=kb
            )
            await callback.answer(f"💎 x{coeff}")
        except TelegramBadRequest:
            await callback.answer()

# Для групп
@router.callback_query(F.data.startswith("mines_cashout:"))
async def cashout_mines_group(callback: CallbackQuery):
        data_parts = callback.data.split(":")

        # 1. Забор куша в групповом чате
        if len(data_parts) == 2:
            game_id = int(data_parts[1])
            game_data = active_games.get(game_id)

            if not game_data:
                return await callback.answer("⚠️ Игра не найдена!", show_alert=True)

            if callback.from_user.id != game_data["owner_id"]:
                return await callback.answer("🚫 Это не ваше игровое лобби!", show_alert=True)

            opened_count = len(game_data["opened"])
            if opened_count == 0:
                return await callback.answer("Откройте хотя бы одну ячейку!", show_alert=True)

            coeff = calculate_coeff(game_data["mines_count"], opened_count)
            win_amount = round(game_data["bet"] * coeff, 2)

            add_balance(callback.from_user.id, win_amount)

            # 1. Генерируем клавиатуру с раскрытыми минами и БЕЗ кнопки "Забрать"
            final_keyboard = get_mines_board_keyboard(
                game_data=game_data,
                reveal=True,  # Раскрываем все ячейки
                show_cashout=False  # Убираем кнопку "Забрать"
            )

            # 2. Очищаем игру из памяти ПОСЛЕ сборки клавиатуры
            del active_games[game_id]

            user_db = get_user(callback.from_user.id)
            new_balance = user_db[2] if user_db else 0.0

            text = (
                f'<tg-emoji emoji-id="5429561707003395285">🥳</tg-emoji> <b>Вы забрали выигрыш!</b>\n\n'
                f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Выигрыш:</b> {win_amount:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> (x{coeff})\n'
                f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Ваш баланс:</b> {new_balance:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>'
            )

            # 3. Обновляем сообщение
            return await callback.message.edit_text(
                text=text,
                parse_mode="HTML",
                reply_markup=final_keyboard
            )


# ==========================================
# ОБНОВЛЕННЫЙ ХЭНДЛЕР СТАРТА БОУЛИНГА
# ==========================================

# Регулярка поддерживает: 1боул, 2боулинг, 3 боул, боулинг, кегли, бв и т.д.
@router.message(F.text.regexp(r"(?i)^(?:[1-3]?\s*(?:боул(?:инг)?|кегли|бв))\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$"))
async def start_bowling_lobby_cmd(message: Message, bot: Bot):
    global bowling_game_counter

    match = re.match(
        r"^(?:[1-3]?\s*(?:боул(?:инг)?|кегли|бв))\s+(\d+(?:[\.,]\d+)?|вб|все|all|ва-банк|вабанк)$",
        message.text,
        re.IGNORECASE
    )
    if not match:
        return

    amount_raw = match.group(1)
    user_id = message.from_user.id
    chat_id = message.chat.id
    game_key = f"{chat_id}:{user_id}"

    user_db = get_user(user_id)
    balance = round(user_db[2], 2) if user_db else 0.0

    bet = parse_bet_amount(amount_raw, balance)

    if game_key in active_pve_bowling_games:
        return await message.reply(
            '<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji> У вас есть активная игра в боулинг! Завершите её.',
            parse_mode="HTML"
        )

    if bet < 0.1:
        return await message.reply(
            '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Минимальная ставка — <b>0.1 $</b>!',
            parse_mode="HTML"
        )

    if balance < bet:
        return await message.reply(
            f'<tg-emoji emoji-id="5258249368670073225">🚨</tg-emoji> <b>Недостаточно средств!</b>\n'
            f'Ваш баланс: <b>{balance:.2f}</b> <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>',
            parse_mode="HTML"
        )

    # Списываем ставку и увеличиваем оборот
    subtract_balance(user_id, bet)
    add_turnover(user_id, bet)

    bowling_game_counter += 1
    game_num = bowling_game_counter
    user_name = message.from_user.mention_html()

    active_pve_bowling_games[game_key] = {
        "game_num": game_num,
        "user_id": user_id,
        "user_name": user_name,
        "bet": bet,
        "bot_score": None,
        "user_score": None,
        "status": "bot_turn",
        "lobby_msg_id": None
    }

    lobby_text = (
        f'<tg-emoji emoji-id=\"5780682851183762876\">6️⃣</tg-emoji> <b>Боулинг #{game_num}</b>\n\n'
        f'<b>Игроки:</b>\n'
        f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: <tg-emoji emoji-id="5242612543796567211">⭐️</tg-emoji>\n'
        f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: <tg-emoji emoji-id="5242612543796567211">⭐️</tg-emoji>\n\n'
        f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>\n'
        f'<blockquote><tg-emoji emoji-id="5215579104807497179">🕐</tg-emoji> Бот бросает шар...</blockquote>'
    )

    lobby_msg = await message.answer(lobby_text, parse_mode="HTML")
    active_pve_bowling_games[game_key]["lobby_msg_id"] = lobby_msg.message_id

    await asyncio.sleep(1.5)

    # Бросок бота
    dice_msg = await message.answer_dice(emoji="🎳")
    bot_score = dice_msg.dice.value
    active_pve_bowling_games[game_key]["bot_score"] = bot_score

    await asyncio.sleep(4.0)

    bot_score_fmt = format_number_to_premium(bot_score)

    active_pve_bowling_games[game_key]["status"] = "user_turn"

    updated_text = (
        f'<tg-emoji emoji-id=\"5780682851183762876\">6️⃣</tg-emoji> <b>Боулинг #{game_num}</b>\n\n'
        f'<b>Игроки:</b>\n'
        f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: <tg-emoji emoji-id="5242612543796567211">⭐️</tg-emoji>\n'
        f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: <b>{bot_score_fmt}</b>\n\n'
        f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>\n\n'
        f'<tg-emoji emoji-id="5339061961483100987">👉</tg-emoji> <b>{user_name}, отправьте шар <code>🎳</code> в чат!</b>'
    )

    with suppress(TelegramBadRequest):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=lobby_msg.message_id,
            text=updated_text,
            parse_mode="HTML"
        )


@router.message(F.dice, F.dice.emoji == "🎳")
async def handle_user_bowling_throw(message: Message, bot: Bot):
    user_id = message.from_user.id
    chat_id = message.chat.id
    game_key = f"{chat_id}:{user_id}"

    game = active_pve_bowling_games.get(game_key)

    if not game or game["status"] != "user_turn":
        return

    user_score = message.dice.value
    game["user_score"] = user_score
    game["status"] = "finished"

    game_num = game["game_num"]
    user_name = game["user_name"]
    bet = game["bet"]
    bot_score = game["bot_score"]

    await asyncio.sleep(4.0)  # Анимация броска шара

    user_score_fmt = format_number_to_premium(user_score)
    bot_score_fmt = format_number_to_premium(bot_score)

    win_amount = round(bet * 2 * 0.95, 2)

    if user_score > bot_score:
        add_balance(user_id, win_amount)
        res_str = f'<tg-emoji emoji-id="5357215701616565438">👍</tg-emoji> <b>Победа игрока {user_name}!</b> (+{win_amount:.2f} <tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>)'
    elif user_score < bot_score:
        res_str = f'<tg-emoji emoji-id="5361791456694539205">😞</tg-emoji> <b>Победил WXS бот!</b>'
    else:
        add_balance(user_id, bet)
        res_str = f'<tg-emoji emoji-id=\"5780682851183762876\">6️⃣</tg-emoji> <b>Ничья! Ставка {bet:.2f} $ возвращена.</b>'

    lobby_final_text = (
        f'<tg-emoji emoji-id=\"5780682851183762876\">6️⃣</tg-emoji> <b>Боулинг #{game_num} [Окончена]</b>\n\n'
        f'<b>Игроки:</b>\n'
        f'<tg-emoji emoji-id="5469957648243760108">😛</tg-emoji> {user_name}: <b>{user_score_fmt}</b>\n'
        f'<tg-emoji emoji-id="5310249233507426089">🤖</tg-emoji> WXS бот: <b>{bot_score_fmt}</b>\n\n'
        f'<tg-emoji emoji-id="5355268724221812442">😄</tg-emoji> Ставка: <b>{bet:.2f} $</b>'
    )

    reply_final_text = (
        f'{lobby_final_text}\n\n'
        f'<tg-emoji emoji-id="5280769763398671636">🏆</tg-emoji> <b>Результат:</b>\n'
        f'{res_str}'
    )

    with suppress(TelegramBadRequest):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=game["lobby_msg_id"],
            text=lobby_final_text,
            parse_mode="HTML"
        )

    await message.reply(
        reply_final_text,
        parse_mode="HTML"
    )

    del active_pve_bowling_games[game_key]


@router.callback_query(F.data == "balance")
async def balance(callback: CallbackQuery):
    user_data = get_user(callback.from_user.id)
    balance_val = user_data["balance"] if user_data else 0.0
    turnover_bal = user_data["turnover"] if user_data else 0.0

    profile_text = (
        f'<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> <b><code>Баланс: {balance_val:.2f} $</code></b>'
    )

    await callback.answer()
    await callback.message.edit_text(
        text=profile_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Пополнить",
                        callback_data="deposit",
                        icon_custom_emoji_id="5449683594425410231",
                    ),
                    InlineKeyboardButton(
                        text="Вывести",
                        callback_data="withdraw",
                        icon_custom_emoji_id="4956720180337050608",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data="back",
                        icon_custom_emoji_id="5877629862306385808",
                    )
                ],
            ]
        ),
    )


BOT_USERNAME = "wxs_robot"
PROFILE_COMMANDS = ["б", "баланс", "проф", "профиль"]


def get_profile_data(user_id: int, full_name: str, is_chat: bool = False):
    user_db = get_user(user_id)

    # 1. Проверяем, зарегистрирован ли пользователь
    if not user_db:
        text = (
            '<tg-emoji emoji-id="4956739572114392015">💎</tg-emoji> <b>Вы не зарегистрированы в системе!</b>\n\n'
            'Чтобы просматривать свой профиль и пользоваться ботом, '
            'пожалуйста, запустите бота первый раз.'
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Зарегистрироваться",
                        url=f"https://t.me/{BOT_USERNAME}?start=register",
                        icon_custom_emoji_id="5310249233507426089",
                    )
                ]
            ]
        )
        return text, keyboard

    # 2. Если пользователь найден в БД — достаем его данные
    reg_date = user_db[1]
    balance = user_db[2]
    turnover = user_db[3]
    real_deposits = get_user_real_deposits_total(user_id)
    withdrawals = user_db[5]

    safe_full_name = py_html.escape(full_name)

    text = f"""
<tg-emoji emoji-id="5275979556308674886">👤</tg-emoji> <b>Имя:</b> {safe_full_name}
<tg-emoji emoji-id="5278753302023004775">ℹ️</tg-emoji> <b>ID:</b> <code>{user_id}</code>
<tg-emoji emoji-id="5276412364458059956">🕓</tg-emoji> <b>Регистрация:</b> {reg_date}

<tg-emoji emoji-id="5206211858444354221">🧪</tg-emoji> <b>Оборот:</b> {turnover:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>
<tg-emoji emoji-id="5276398496008663230">👝</tg-emoji> <b>Баланс:</b> {balance:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>

<tg-emoji emoji-id="5206401524200145033">🔼</tg-emoji> <b>Пополнений:</b> {real_deposits:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>
<tg-emoji emoji-id="5206510891247371052">🔽</tg-emoji> <b>Выводов:</b> {withdrawals:.2f}<tg-emoji emoji-id="5309939123983789521">💲</tg-emoji>
"""

    if is_chat:
        # Для сообщений в чате — ссылки перенаправляют в ЛС бота (без кнопки Назад)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Пополнить",
                        url=f"https://t.me/{BOT_USERNAME}?start=deposit",
                        icon_custom_emoji_id="5449683594425410231",
                    ),
                    InlineKeyboardButton(
                        text="Вывести",
                        url=f"https://t.me/{BOT_USERNAME}?start=withdraw",
                        icon_custom_emoji_id="4956720180337050608",
                    ),
                ]
            ]
        )
    else:
        # Для меню в ЛС — callback-кнопки с возможностью вернуться
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Пополнить",
                        callback_data="deposit",
                        icon_custom_emoji_id="5449683594425410231",
                    ),
                    InlineKeyboardButton(
                        text="Вывести",
                        callback_data="withdraw",
                        icon_custom_emoji_id="4956720180337050608",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data="back",
                        icon_custom_emoji_id="5877629862306385808",
                    )
                ],
            ]
        )

    return text, keyboard


# 1. Отправка профиля текстом по команде ("б", "баланс", "проф", "профиль")
@router.message(F.text.lower().in_(PROFILE_COMMANDS))
async def profile_message(message: Message):
    # Флаг is_chat=True убирает кнопку "Назад" и делает кнопки ссылками в ЛС
    text, keyboard = get_profile_data(
        user_id=message.from_user.id,
        full_name=message.from_user.full_name,
        is_chat=True
    )

    await message.answer(
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# 2. Переход в профиль через inline-меню в ЛС
@router.callback_query(F.data == "profile")
async def profile_callback(callback: CallbackQuery):
    await callback.answer()

    # is_chat=False оставляет привычные callback-кнопки и кнопку "Назад"
    text, keyboard = get_profile_data(
        user_id=callback.from_user.id,
        full_name=callback.from_user.full_name,
        is_chat=False
    )

    with suppress(TelegramBadRequest):
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


@router.message(
    Command("top"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP, ChatType.PRIVATE})
)
@router.message(
    F.text.lower().in_({"топ", "топ оборот"}),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP, ChatType.PRIVATE})
)
async def cmd_top_turnover(message: Message, bot: Bot):
    top_data = get_top_turnover(limit=10)
    current_user_id = message.from_user.id

    medals = {
        1: '<tg-emoji emoji-id="5224473711494581672">1️⃣</tg-emoji>',
        2: '<tg-emoji emoji-id="5224251017440285983">2️⃣</tg-emoji>',
        3: '<tg-emoji emoji-id="5224414625629492488">3️⃣</tg-emoji>'
    }
    lines = ['<tg-emoji emoji-id="5280769763398671636">🏆</tg-emoji> <b>ТОП 10 ПО ОБОРОТУ</b>\n']

    user_rank_in_top = None
    user_turnover_in_top = 0.0

    if not top_data:
        lines.append("<i>Список пока пуст...</i>")
    else:
        for idx, (uid, turnover) in enumerate(top_data, start=1):
            prefix = medals.get(idx, f"<b>{idx}.</b>")

            try:
                chat = await bot.get_chat(uid)
                user_name = escape(chat.first_name)

                if chat.username:
                    display_name = f'<a href="https://t.me/{chat.username}">{user_name}</a> (@{chat.username})'
                else:
                    display_name = f'<a href="tg://user?id={uid}">{user_name}</a>'
            except Exception:
                display_name = f'Игрок <code>{uid}</code>'

            # Запоминаем позицию текущего пользователя, если он в ТОП-10
            if uid == current_user_id:
                user_rank_in_top = idx
                user_turnover_in_top = turnover

            lines.append(
                f"{prefix} {display_name} "
                f'<tg-emoji emoji-id="5215330331711775720">➡️</tg-emoji> '
                f'<b>{turnover:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>'
            )

    # ==========================================
    # БЛОК "ВАШЕ МЕСТО" ПОД ВСЕМ ТОПОМ
    # ==========================================
    lines.append("\n<b>───────────────</b>")

    if user_rank_in_top is not None:
        # Игрок найден в ТОП-10
        lines.append(
            f'<tg-emoji emoji-id="5402314073700322549">👨‍🦱</tg-emoji> '
            f'<b>Ваше место:</b> {user_rank_in_top} | Оборот: '
            f'<b>{user_turnover_in_top:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>'
        )
    else:
        # Игрока нет в ТОП-10 — делаем запрос в БД
        rank, user_turnover = get_user_turnover_rank(current_user_id)
        if rank:
            lines.append(
                f'<tg-emoji emoji-id="5402314073700322549">👨‍🦱</tg-emoji> '
                f'<b>Ваше место:</b> {rank} | Оборот: '
                f'<b>{user_turnover:.2f}<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji></b>'
            )
        else:
            lines.append(
                f'<tg-emoji emoji-id="5402314073700322549">👨‍🦱</tg-emoji> '
                f'<b>Ваш оборот:</b> 0.00<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji>'
            )

    await message.reply("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data == "bonus")
async def bonus(callback: CallbackQuery):
    await callback.answer()

    user_id = callback.from_user.id
    day, last_bonus = get_bonus(user_id)

    now = int(time.time())
    amount = round(0.03 + (day - 1) * 0.003, 3)
    next_amount = round(amount + 0.003, 3)

    if last_bonus != 0 and now - last_bonus < 86400:
        left = 86400 - (now - last_bonus)
        hours = left // 3600
        minutes = (left % 3600) // 60

        status = f'<tg-emoji emoji-id="5893102202817352158">🕞</tg-emoji> <b>Бонус будет доступен через {hours}ч {minutes}м</b>'
        bonus_button = InlineKeyboardButton(
            text="Недоступен",
            callback_data="take_bonus",
            icon_custom_emoji_id="4958526153955476488"
        )
    else:
        status = '<tg-emoji emoji-id="4956721670690702265">✔️</tg-emoji> <b>Бонус доступен!</b>'
        bonus_button = InlineKeyboardButton(
            text="Забрать бонус",
            callback_data="take_bonus",
            icon_custom_emoji_id="5348277750119538174"
        )

    text = f"""
<tg-emoji emoji-id="6183629501709160320">🎁</tg-emoji> <b>Ежедневный бонус</b>

<tg-emoji emoji-id="5424972470023104089">🔥</tg-emoji> Серия: <b>{day} день</b>

<tg-emoji emoji-id="5429579840355330334">🎁</tg-emoji> Сейчас: <b>{amount:.3f}<tg-emoji emoji-id="5893473283696759404">💰</tg-emoji> </b>

<tg-emoji emoji-id="4956282853882069908">➡️</tg-emoji> Следующий: <b>{next_amount:.3f}<tg-emoji emoji-id="5893473283696759404">💰</tg-emoji> </b>

{status}
"""

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [bonus_button],
                [InlineKeyboardButton(
                    text="Назад",
                    callback_data="back",
                    icon_custom_emoji_id="5877629862306385808"
                )]
            ]
        )
    )

@router.callback_query(F.data == "take_bonus")
async def take_bonus_handler(callback: CallbackQuery):
        user_id = callback.from_user.id
        day, last_bonus = get_bonus(user_id)
        now = int(time.time())

        if last_bonus != 0 and now - last_bonus < 86400:
            left = 86400 - (now - last_bonus)
            hours = left // 3600
            minutes = (left % 3600) // 60
            return await callback.answer(f"⏳ Бонус будет доступен через {hours}ч {minutes}м", show_alert=True)

        amount = round(0.03 + (day - 1) * 0.003, 3)
        take_bonus(user_id, amount)
        increase_bonus_day(user_id)

        await callback.answer(f"🎉 Вы получили {amount:.3f} $", show_alert=True)
        await bonus(callback)

@router.callback_query(F.data == "refs")
async def refs(callback: CallbackQuery):
        await callback.answer("👥 Раздел рефералов.", show_alert=True)

@router.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery):
        await callback.answer("⚙️ Настройки.", show_alert=True)


DB_PATH = "database.db"


def init_db():
    with sqlite3.connect("database.db") as conn:  # Укажите ваш путь к БД
        cursor = conn.cursor()

        # Создаем таблицу deposits, если её нет
        cursor.execute("""
                       CREATE TABLE IF NOT EXISTS deposits
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           user_id
                           INTEGER
                           NOT
                           NULL,
                           amount
                           REAL
                           NOT
                           NULL,
                           timestamp
                           INTEGER
                           NOT
                           NULL
                       )
                       """)
        conn.commit()

def record_real_deposit(user_id: int, amount: float):
    """Записывает реальный деп в таблицу deposits и обновляет баланс пользователя."""
    import time
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # 1. Заносим в таблицу чистых пополнений
        cursor.execute(
            "INSERT INTO deposits (user_id, amount, timestamp) VALUES (?, ?, ?)",
            (user_id, amount, int(time.time()))
        )
        # 2. Пополняем основной баланс пользователя
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        conn.commit()


def add_real_deposit(user_id: int, amount: float, provider: str):
    """
    🟢 ВЫЗЫВАТЬ ТОЛЬКО ПРИ НАСТОЯЩЕЙ ОПЛАТЕ!
    Увеличивает баланс, обновляет статистику пополнений и создает запись в истории.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # 1. Обновляем баланс и счетчик реальных пополнений
        cursor.execute("""
                       UPDATE users
                       SET balance         = balance + ?,
                           deposited_total = deposited_total + ?
                       WHERE user_id = ?
                       """, (amount, amount, user_id))

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO deposits (user_id, amount, provider, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, amount, provider, now),
        )

        conn.commit()


def add_admin_balance(user_id: int, amount: float):
    """
    🟡 ВЫЗЫВАТЬ ДЛЯ АДМИНКИ / ТЕСТОВ / БОНУСОВ.
    Меняет только текущий баланс, статистика пополнений НЕ затрагивается.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       UPDATE users
                       SET balance = balance + ?
                       WHERE user_id = ?
                       """, (amount, user_id))
        conn.commit()


@router.callback_query(F.data == "deposit")
async def deposit_callback(callback: CallbackQuery):
        await callback.answer()
        text, kb = get_deposit_menu()

        if callback.message.text:
            await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb)
        elif callback.message.caption:
            await callback.message.edit_caption(caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await callback.message.delete()
            await callback.message.answer(text=text, parse_mode="HTML", reply_markup=kb)

@router.message(Command("deposit"))
async def deposit_command(message: Message):
        try:
            await message.delete()
        except TelegramBadRequest:
            pass

        text, kb = get_deposit_menu()
        await message.answer(text=text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "deposit_cryptobot")
async def deposit_crypto(callback: CallbackQuery, state: FSMContext):
        await callback.answer()

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="deposit",
                                      icon_custom_emoji_id="5877629862306385808")]
            ]
        )

        text = """
<tg-emoji emoji-id="5361914370068613491">🦋</tg-emoji> <b>Пополнение через CryptoBot</b>

<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> Введите сумму в <b>USDT</b>

<tg-emoji emoji-id="5895514131896733546">✅</tg-emoji> Минимальная сумма: <b>0.1 USDT</b>
"""

        try:
            await callback.message.delete()
        except Exception:
            pass

        # Отправляем новое сообщение и сохраняем его ID в стейт
        sent_message = await callback.message.answer(
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        await state.update_data(prompt_message_id=sent_message.message_id)
        await state.set_state(Deposit.cryptobot)

@router.message(Deposit.cryptobot)
async def deposit_amount(message: Message, state: FSMContext):
        data = await state.get_data()
        prompt_message_id = data.get("prompt_message_id")

        if prompt_message_id:
            try:
                await message.bot.delete_message(
                    chat_id=message.chat.id, message_id=prompt_message_id
                )
            except Exception:
                pass

        try:
            amount = float(message.text.replace(",", "."))
            if amount < 0.1:
                return await message.answer(
                    '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Минимальная сумма — 0.1 USDT',
                    parse_mode="HTML"
                )
        except ValueError:
            return await message.answer(
                '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Введите сумму числом.',
                parse_mode="HTML"
            )

        invoice = await create_cryptobot_invoice(amount)

        if not invoice:
            return await message.answer(
                '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Не удалось создать счёт.',
                parse_mode="HTML"
            )

        invoice_id = str(invoice.get("invoice_id") or invoice.get("id"))
        pay_url = invoice.get("pay_url") or invoice.get("bot_invoice_url")

        # ⚠️ СОХРАНЯЕМ В БД (этого вызова не было!)
        save_invoice(invoice_id, message.from_user.id, amount)

        await message.answer(
            f"""
<tg-emoji emoji-id="5443127283898405358">📥</tg-emoji> <b>Счёт успешно создан!</b>

<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> Сумма: <b>{amount:.2f} USDT</b>

После оплаты средства автоматически поступят на баланс. <tg-emoji emoji-id="4956721670690702265">✔️</tg-emoji>
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="Оплатить",
                        url=pay_url,
                        icon_custom_emoji_id="5456398075713037730")
                    ],
                    [
                        InlineKeyboardButton(
                            text="Проверить оплату",
                            callback_data=f"check_{invoice_id}",
                            icon_custom_emoji_id="4956721670690702265")
                    ],
                    [
                        InlineKeyboardButton(
                            text="Отмена",
                            callback_data=f"deposit",
                            icon_custom_emoji_id="4958526153955476488")
                    ],
                ]
            ),
        )

        await state.clear()

@router.callback_query(F.data.startswith("check_"))
async def check_payment(callback: CallbackQuery):
        # Работаем с invoice_id как со строкой для надежности совпадения с БД
        invoice_id = callback.data.split("_")[1]
        invoice = get_invoice(invoice_id)

        if invoice is None:
            return await callback.answer("Счёт не найден.", show_alert=True)

        user_id, amount = invoice[1], invoice[2]

        if is_paid(invoice_id):
            return await callback.answer("Баланс уже начислен.", show_alert=True)

        # ⚠️ Используем импортированную в начале файла функцию из services.cryptobot
        crypto = await check_cryptobot_invoice(invoice_id)

        if crypto is None:
            return await callback.answer(
                "Ошибка получения данных от CryptoBot.", show_alert=True
            )

        # Проверяем статус с поддержкой и объектов, и словарей
        status = (
            crypto.get("status")
            if isinstance(crypto, dict)
            else getattr(crypto, "status", None)
        )

        if status != "paid":
            return await callback.answer(
                "🕓 Ожидаю, похоже, что счёт не оплачен", show_alert=True
            )

        # Начисляем и меняем статус в БД
        invoice_paid(invoice_id)
        add_real_deposit(user_id=user_id, amount=amount, provider="CryptoBot")

        # 1. Показываем всплывающее уведомление
        await callback.answer("Успешно зачислено!", show_alert=True)

        # 2. Обновляем сообщение (без icon_custom_emoji_id)
        try:
            await callback.message.edit_text(
                f'\n<tg-emoji emoji-id="4956721670690702265">✔️</tg-emoji> На ваш баланс зачислено <b>{amount:.2f} USDT</b>\n',
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Домой, Уолтер",
                                callback_data="play",
                                icon_custom_emoji_id="5938537205847822613"
                            )
                        ]
                    ]
                ),
            )
        except Exception as e:
            # Логируем, если с изменением сообщения что-то не так (например, текст не изменился)
            print(f"Ошибка при изменении сообщения: {e}")
        except TelegramBadRequest:
            pass

@router.callback_query(F.data == "deposit_xrocket")
async def deposit_xrocket_start(callback: CallbackQuery, state: FSMContext):
        await callback.answer()

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Назад",
                                      callback_data="deposit",
                                      icon_custom_emoji_id="5877629862306385808")]
            ]
        )

        text = """
<tg-emoji emoji-id=\"5415897719522744378\">🚀</tg-emoji> <b>Пополнение через xRocket</b>

<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> Введите сумму в <b>USDT</b>

<tg-emoji emoji-id="5895514131896733546">✅</tg-emoji> Минимальная сумма: <b>0.1 USDT</b>
"""

        try:
            await callback.message.delete()
        except Exception:
            pass

        sent_message = await callback.message.answer(
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        await state.update_data(prompt_message_id=sent_message.message_id)
        await state.set_state(Deposit.xrocket)

@router.message(Deposit.xrocket)
async def deposit_xrocket_amount(message: Message, state: FSMContext):
        data = await state.get_data()
        prompt_message_id = data.get("prompt_message_id")

        if prompt_message_id:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_message_id)
            except Exception:
                pass

        try:
            amount = float(message.text.replace(",", "."))
            if amount < 0.1:
                return await message.answer(
                    "<tg-emoji emoji-id=\"5240241223632954241\">🚫</tg-emoji> Минимальная сумма — 0.1 USDT",
                    parse_mode="HTML"
                )
        except ValueError:
            return await message.answer(
                "<tg-emoji emoji-id=\"5240241223632954241\">🚫</tg-emoji> Введите сумму числом.",
                parse_mode="HTML"
            )

        invoice = await create_xrocket_invoice(amount)
        if invoice is None:
            return await message.answer(
                "<tg-emoji emoji-id=\"5240241223632954241\">🚫</tg-emoji> Не удалось создать счёт.",
                parse_mode="HTML"
            )
        # Безопасный извлекатель ID и URL
        xr_id = str(invoice.get("id") or invoice.get("_id") or invoice.get("invoiceId"))
        pay_url = invoice.get("link") or invoice.get("payUrl") or invoice.get("botPayUrl")

        save_invoice(xr_id, message.from_user.id, amount)

        await message.answer(
            f"""
<tg-emoji emoji-id="5443127283898405358">📥</tg-emoji> <b>Счёт успешно создан!</b>

<tg-emoji emoji-id="4956601935592424315">💵</tg-emoji> Сумма: <b>{amount:.2f} USDT</b>

После оплаты средства автоматически поступят на баланс. <tg-emoji emoji-id="4956721670690702265">✔️</tg-emoji>
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Оплатить",
                                             url=pay_url,
                                             icon_custom_emoji_id="5456398075713037730")
                    ],
                    [
                        InlineKeyboardButton(text="Проверить оплату",
                                             callback_data=f"checkxr_{xr_id}",
                                             icon_custom_emoji_id="4956721670690702265")
                    ],
                    [
                        InlineKeyboardButton(text="Отмена",
                                             callback_data=f"deposit",
                                             icon_custom_emoji_id="4958526153955476488"),
                    ]
                ]
            )
        )
        await state.clear()

@router.callback_query(F.data.startswith("checkxr_"))
async def check_xrocket_payment(callback: CallbackQuery):
        # НЕ переводим в int, так как ID xRocket — это строка
        invoice_id = callback.data.split("_")[1]
        invoice = get_invoice(invoice_id)

        if invoice is None:
            return await callback.answer("Счёт не найден.", show_alert=True)

        if is_paid(invoice_id):
            return await callback.answer("Баланс уже начислен.", show_alert=True)

        crypto = await check_xrocket_invoice(invoice_id)

        if crypto is None:
            return await callback.answer("Ошибка xRocket.", show_alert=True)

        # Проверка статуса (с учетом возможного регистра и параметров xRocket)
        status = str(crypto.get("status", "")).lower()
        paid_count = crypto.get("paidPayments", 0)

        if status != "paid" and paid_count == 0:
            return await callback.answer("🕓 Ожидаю, похоже, что счет не оплачен", show_alert=True)

        invoice_paid(invoice_id)
        add_real_deposit(user_id=invoice[1], amount=invoice[2], provider="xRocket")

        try:
            await callback.message.edit_text(
                f'\n<tg-emoji emoji-id="4956721670690702265">✔️</tg-emoji> На ваш баланс зачислено <b>{invoice[2]:.2f} USDT</b>\n',
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Домой, Уолтер",
                                callback_data="play",
                                icon_custom_emoji_id="5938537205847822613"
                            )
                        ]
                    ]
                ),
            )
        except TelegramBadRequest:
            pass

async def safe_edit_or_send(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await callback.message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode="HTML")
            except TelegramBadRequest:
                await callback.message.delete()
                await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")

ADMIN_ID = 6130985988  # Укажите ваш ID

# --- 1. Старт вывода ---
@router.callback_query(F.data == "withdraw")
async def start_withdraw(call: CallbackQuery, state: FSMContext):
        await call.answer()
        await state.set_state(WithdrawState.select_method)

        try:
            await call.message.delete()
        except Exception:
            pass

        await call.message.answer(
            '<tg-emoji emoji-id="4956720180337050608">🔽</tg-emoji> <b>Выберите способ вывода:</b>',
            reply_markup=get_withdraw_methods_kb(),
            parse_mode="HTML"
        )

# --- 2. Выбор платежной системы ---
@router.callback_query(WithdrawState.select_method, F.data.startswith("withdraw_method:"))
async def process_method_selection(call: CallbackQuery, state: FSMContext):
        await call.answer()
        method = call.data.split(":")[1]

        await state.update_data(withdraw_method=method)
        await state.set_state(WithdrawState.enter_amount)

        method_name = "xRocket" if method == "xrocket" else "CryptoBot"
        await call.message.edit_text(
            f"Выбран способ: <b>{method_name}</b>\n\n"
            f'<tg-emoji emoji-id="4956720180337050608">🔽</tg-emoji> Введите сумму для вывода:',
            parse_mode="HTML"
        )

# --- 3. Ввод суммы ---
@router.message(WithdrawState.enter_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
        user_id = message.from_user.id

        try:
            amount = float(message.text.replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await message.answer(
                "<tg-emoji emoji-id=\"5420323339723881652\">⚠️</tg-emoji> Пожалуйста, введите корректное число!")
            return

        if amount < 1.1:
            await message.answer(
                f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>Минимальная сумма вывода 1.1!</b><tg-emoji emoji-id=\"5309939123983789521\">💲</tg-emoji>',
                parse_mode="HTML"
            )
            return

        # Получаем реальный баланс из БД
        user_db = get_user(user_id)
        user_balance = user_db[2] if user_db else 0.0

        # Проверка: если введённая сумма больше баланса — отказ
        if amount > user_balance:
            await message.answer(
                f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>Отказ!</b> Недостаточно средств.\n'
                f"Ваш баланс: <code>{user_balance}</code> | Запрошено: <code>{amount}</code>",
                parse_mode="HTML"
            )
            return

        # 🟢 Списываем ровно ту сумму, которую ввёл пользователь
        subtract_balance(user_id, amount)

        # Сохраняем введённую сумму в состояние
        await state.update_data(amount=amount)
        await state.set_state(WithdrawState.confirm_gamble)

        if amount > user_balance:
            await message.answer(
                f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>Отказ!</b> Недостаточно средств.\n'
                f"Ваш баланс: <code>{user_balance}</code> | Запрошено: <code>{amount}</code>",
                parse_mode="HTML"
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(WithdrawState.confirm_gamble)

        await message.answer(
            f'<tg-emoji emoji-id="4956720180337050608">🔽</tg-emoji> Сумма вывода: <b>{amount}</b><tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n\n'
            f"<tg-emoji emoji-id=\"5357215701616565438\">👍</tg-emoji> Хотите рискнуть и увеличить сумму вывода на <b>50%</b>?<tg-emoji emoji-id=\"5348277750119538174\">👍</tg-emoji>\n"
            f"<tg-emoji emoji-id=\"5373174941095050893\">💸</tg-emoji> • <b>65% шанс</b>: сумма станет <code>{amount * 1.5:.2f}</code><tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n"
            f"<tg-emoji emoji-id=\"5361791456694539205\">😞</tg-emoji> • <b>35% шанс</b>: сгорит 40% и выведете <code>{amount * 0.6:.2f}</code><tg-emoji emoji-id=\"5309939123983789521\">💲</tg-emoji>",
            reply_markup=get_gamble_kb(),
            parse_mode="HTML"
        )

# --- 4. Обработка решения ---
@router.callback_query(WithdrawState.confirm_gamble, F.data.in_({"gamble_yes", "gamble_no"}))
async def process_gamble_choice(call: CallbackQuery, state: FSMContext, bot: Bot):
        await call.answer()
        data = await state.get_data()

        original_amount = data["amount"]
        method = data["withdraw_method"]
        method_name = "xRocket" if method == "xrocket" else "CryptoBot"

        final_amount = original_amount
        gamble_result_text = "Без риска."

        if call.data == "gamble_yes":
            if random.random() <= 0.14:
                final_amount = original_amount * 1.5
                gamble_result_text = "<tg-emoji emoji-id=\"5357215701616565438\">👍</tg-emoji> <b>Успех (+50%)!</b> Вам улыбнулась удача!"
            else:
                final_amount = original_amount * 0.6
                gamble_result_text = "<tg-emoji emoji-id=\"5361791456694539205\">😞</tg-emoji> <b>Неудача (-40%).</b> К сожалению, сумма уменьшилась."

        await call.message.edit_text(
            f"{gamble_result_text}\n\n"
            f'<tg-emoji emoji-id=\"4956721670690702265\">✔️</tg-emoji> <b>Заявка на вывод сформирована!</b>\n'
            f"<tg-emoji emoji-id=\"5206211858444354221\">🧪</tg-emoji> • Способ: <b>{method_name}</b>\n"
            f'<tg-emoji emoji-id=\"5213094908608392768\">💰</tg-emoji> • Итоговая сумма: <b>{final_amount:.2f}</b>\n\n'
            f'<tg-emoji emoji-id=\"5276412364458059956\">🕓</tg-emoji> Ожидайте обработки администратором.',
            parse_mode="HTML"
        )

        # Сообщение администратору тоже переведено в HTML
        admin_text = (
            f"📥 <b>Новая заявка на вывод!</b>\n\n"
            f"👤 Пользователь: @{call.from_user.username or 'нет_юзернейма'} (ID: <code>{call.from_user.id}</code>)\n"
            f"💳 Способ: <b>{method_name}</b>\n"
            f"💰 Изначальная сумма: <code>{original_amount}</code>\n"
            f"🎲 Играл в риск: <b>{'Да' if call.data == 'gamble_yes' else 'Нет'}</b>\n"
            f"💵 <b>Итого к выплате:</b> <code>{final_amount:.2f}</code>"
        )

        try:
            await bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML")
        except Exception as e:
            print(f"Ошибка отправки сообщения админу: {e}")

        await state.clear()

    # --- Отмена вывода ---
@router.callback_query(F.data == "cancel_withdraw")
async def cancel_withdraw(call: CallbackQuery, state: FSMContext):
        await state.clear()
        await call.message.edit_text("<tg-emoji emoji-id=\"5420323339723881652\">⚠️</tg-emoji> Вывод отменен.")

@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            return await callback.answer("У вас нет доступа.", show_alert=True)

        text = '<tg-emoji emoji-id="4956232383721374836">📌</tg-emoji> <b>Админ-панель</b>\n\nВыберите действие:'
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Начислить баланс",
                        callback_data="admin_add_balance",
                        icon_custom_emoji_id="5449683594425410231"
                    ),
                    InlineKeyboardButton(
                        text="Уменьшить баланс",
                        callback_data="admin_decrease_balance",
                        icon_custom_emoji_id="4956720180337050608"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Балансы пользователей",
                        callback_data="admin_top_balances",
                        icon_custom_emoji_id="4956591954088428445"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data="back",
                        icon_custom_emoji_id="5877629862306385808"
                    )
                ]
            ]
        )

        await safe_edit_or_send(callback, text, kb)
        await callback.answer()

def _db_get_top_users(limit=50):
        """Синхронный запрос к БД"""
        with sqlite3.connect("database.db") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT ?",
                (limit,)
            )
            return cursor.fetchall()

async def get_top_users_by_balance(limit=50):
        """Асинхронная обертка для работы с БД без блокировки event loop"""
        return await asyncio.to_thread(_db_get_top_users, limit)

@router.callback_query(F.data == "admin_top_balances")
async def show_top_balances(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            return await callback.answer("У вас нет доступа.", show_alert=True)

        users = await get_top_users_by_balance(limit=50)

        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data="admin_panel",
                        icon_custom_emoji_id="5877629862306385808"
                    )
                ]
            ]
        )

        if not users:
            await safe_edit_or_send(callback, "В базе данных пока нет пользователей.", back_kb)
            return await callback.answer()

        text = '<tg-emoji emoji-id="4956591954088428445">🧹</tg-emoji> <b>Топ пользователей по балансу:</b>\n\n'

        for idx, (user_id, balance) in enumerate(users, start=1):
            try:
                chat = await callback.bot.get_chat(user_id)
                if chat.username:
                    user_display = f"@{chat.username}"
                else:
                    user_display = f'<a href="tg://user?id={user_id}">{chat.first_name}</a>'
            except Exception:
                user_display = f'<a href="tg://user?id={user_id}">Профиль</a>'

            text += f"{idx}. <code>{user_id}</code> {user_display} — <b>{balance:,.2f}</b> <tg-emoji emoji-id=\"4956601935592424315\">💵</tg-emoji>\n"

        await safe_edit_or_send(callback, text, back_kb)
        await callback.answer()

@router.callback_query(F.data == "admin_add_balance")
async def admin_add_balance(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id != ADMIN_ID:
            return

        await callback.message.delete()
        await callback.message.answer(
            text="<tg-emoji emoji-id=\"5469905795103596040\">👍</tg-emoji> <b>Введите ID пользователя</b>",
            parse_mode="HTML"
        )
        await state.set_state(AdminBalance.user_id)

@router.message(AdminBalance.user_id)
async def admin_user_id(message: Message, state: FSMContext):
        try:
            user_id = int(message.text)
        except ValueError:
            return await message.answer(
                '<tg-emoji emoji-id="4956337889593000947">🚫</tg-emoji> Введите корректный ID.',
                parse_mode="HTML"
            )

        await state.update_data(user_id=user_id)

        try:
            user = await message.bot.get_chat(user_id)
            name = escape(user.full_name)
            username = f"@{escape(user.username)}" if user.username else "Отсутствует"
        except Exception:
            name = "Неизвестно"
            username = "Отсутствует"

        user_db = get_user(user_id)
        balance = user_db[2] if user_db else 0

        await message.answer(
            f"""
<tg-emoji emoji-id="5357520644294582849">👍</tg-emoji> <tg-emoji emoji-id=\"5474379458808915691\">🔤</tg-emoji><tg-emoji emoji-id=\"5474643363074416292\">🔤</tg-emoji><tg-emoji emoji-id=\"5474610175862121106\">🔤</tg-emoji><tg-emoji emoji-id=\"5474192498882522822\">🔤</tg-emoji><tg-emoji emoji-id=\"5472267056453790126\">🔤</tg-emoji><tg-emoji emoji-id=\"5474379458808915691\">🔤</tg-emoji><tg-emoji emoji-id=\"5474431801575351890\">🔤</tg-emoji>

<tg-emoji emoji-id="6033108709213736873">➕</tg-emoji> <b>Имя:</b> {name}
<tg-emoji emoji-id="5893100690988863311">📱</tg-emoji> <b>Юзернейм:</b> {username}
<tg-emoji emoji-id="5936017305585586269">🪪</tg-emoji> <b>ID:</b> <code>{user_id}</code>
<tg-emoji emoji-id="6039641775377748623">💰</tg-emoji> <b>Баланс:</b> <code>{balance:.2f}$</code>

<tg-emoji emoji-id="5893473283696759404">💰</tg-emoji> <b>Сумма начисления:</b>
""",
            parse_mode="HTML"
        )

        await state.set_state(AdminBalance.amount)

@router.message(AdminBalance.amount)
async def admin_add_amount(message: Message, state: FSMContext):
        try:
            amount = float(message.text.replace(",", "."))
            if amount <= 0:
                return await message.answer(
                    '<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Сумма должна быть больше нуля.',
                    parse_mode="HTML")
        except ValueError:
            return await message.answer('<tg-emoji emoji-id="4958526153955476488">❌</tg-emoji> Введите сумму числом.',
                                        parse_mode="HTML")

        data = await state.get_data()
        user_id = data["user_id"]

        add_balance(user_id, amount)
        await state.clear()

        await message.answer(
            f"""
<tg-emoji emoji-id="5895514131896733546">✅</tg-emoji> Баланс успешно начислен

<tg-emoji emoji-id="5936017305585586269">🪪</tg-emoji> ID: <code>{user_id}</code>

<tg-emoji emoji-id="5893473283696759404">💰</tg-emoji> Сумма: <b>{amount:.2f}$</b>
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Назад", callback_data="back",
                                          icon_custom_emoji_id="5877629862306385808")]]
            )
        )

        admin_name = escape(message.from_user.full_name)

        try:
            await message.bot.send_message(
                chat_id=user_id,
                text=f'<tg-emoji emoji-id="5449683594425410231">🔼</tg-emoji> <b>Создатель будки, {admin_name} начислил вам</b> <b>{amount:.2f}</b><tg-emoji emoji-id="5893473283696759404">💰</tg-emoji>.',
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Домой, Уолтер", callback_data="back",
                                                           icon_custom_emoji_id="5938537205847822613")]]
                )
            )
        except Exception:
            pass

@router.callback_query(F.data == "admin_decrease_balance")
async def admin_decrease_balance(callback: CallbackQuery, state: FSMContext):
        await callback.answer()

        # 1. Безопасно удаляем старое сообщение (даже если там была картинка)
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass  # Игнорируем, если сообщение уже удалено или слишком старо

        # 2. Отправляем новое сообщение
        await callback.message.answer(
            """<tg-emoji emoji-id=\"5469905795103596040\">👍</tg-emoji> <b>Введите ID пользователя для списания:</b>""",
            parse_mode="HTML",
        )

        await state.set_state(AdminDecreaseBalance.user_id)

@router.message(AdminDecreaseBalance.user_id)
async def admin_dec_user(message: Message, state: FSMContext):
        try:
            user_id = int(message.text)
        except ValueError:
            return await message.answer(
                "<tg-emoji emoji-id=\"4956337889593000947\">🚫</tg-emoji> Введите корректный числовой ID.")

        await state.update_data(user_id=user_id)

        try:
            user = await message.bot.get_chat(user_id)
            name = escape(user.full_name)
            username = f"@{escape(user.username)}" if user.username else "Отсутствует"
        except Exception:
            name = "Неизвестно"
            username = "Отсутствует"

        user_db = get_user(user_id)
        balance = user_db[2] if user_db else 0.0

        await message.answer(
            f"""
<tg-emoji emoji-id="5357520644294582849">👍</tg-emoji> <tg-emoji emoji-id=\"5474379458808915691\">🔤</tg-emoji><tg-emoji emoji-id=\"5474643363074416292\">🔤</tg-emoji><tg-emoji emoji-id=\"5474610175862121106\">🔤</tg-emoji><tg-emoji emoji-id=\"5474192498882522822\">🔤</tg-emoji><tg-emoji emoji-id=\"5472267056453790126\">🔤</tg-emoji><tg-emoji emoji-id=\"5474379458808915691\">🔤</tg-emoji><tg-emoji emoji-id=\"5474379458808915691\">🔤</tg-emoji><tg-emoji emoji-id=\"5474431801575351890\">🔤</tg-emoji>

<tg-emoji emoji-id="6033108709213736873">➕</tg-emoji> <b>Имя:</b> {name}
<tg-emoji emoji-id="5893100690988863311">📱</tg-emoji> <b>Юзернейм:</b> {username}
<tg-emoji emoji-id="5936017305585586269">🪪</tg-emoji> <b>ID:</b> <code>{user_id}</code>
<tg-emoji emoji-id="6039641775377748623">💰</tg-emoji> <b>Баланс:</b> <code>{balance:.2f}$</code>

<tg-emoji emoji-id="5893473283696759404">💰</tg-emoji> <b>Введите сумму списания:</b>
""",
            parse_mode="HTML"
        )

        await state.set_state(AdminDecreaseBalance.amount)

@router.message(AdminDecreaseBalance.amount)
async def admin_dec_amount(message: Message, state: FSMContext):
        try:
            amount = float(message.text.replace(",", "."))
            if amount <= 0:
                return await message.answer(
                    "<tg-emoji emoji-id=\"4956337889593000947\">🚫</tg-emoji> Сумма должна быть больше нуля.")
        except ValueError:
            return await message.answer("<tg-emoji emoji-id=\"4956337889593000947\">🚫</tg-emoji> Введите сумму числом.")

        data = await state.get_data()
        user_id = data["user_id"]

        subtract_balance(user_id, amount)
        admin_name = escape(message.from_user.full_name)

        try:
            await message.bot.send_message(
                chat_id=user_id,
                text=f'<tg-emoji emoji-id="4956720180337050608">🔽</tg-emoji><b>Создатель будки, {admin_name} списал с вашего баланса </b>{amount:.2f}<tg-emoji emoji-id="5893473283696759404">💰</tg-emoji>.',
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Домой, Уолтер", callback_data="back",
                                                           icon_custom_emoji_id="5938537205847822613")]]
                )
            )
        except Exception:
            pass

        await message.answer(
            f"""
<tg-emoji emoji-id="5895514131896733546">✅</tg-emoji> Баланс успешно списан

<tg-emoji emoji-id="5936017305585586269">🪪</tg-emoji> ID: <code>{user_id}</code>
<tg-emoji emoji-id="5893473283696759404">💰</tg-emoji> Сумма: <b>{amount:.2f}$</b>
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Назад", callback_data="back",
                                          icon_custom_emoji_id="5877629862306385808")]]
            )
        )

        await state.clear()

@router.callback_query(F.data == "back")
async def back(callback: CallbackQuery):
    text = (
        '<tg-emoji emoji-id="5465281219132415627">🖤</tg-emoji> <b>Добро пожаловать!</b> '
        '<tg-emoji emoji-id="5085022089103016925">⚡️</tg-emoji>\n\n'
        'Куда собрались? <tg-emoji emoji-id="5258184841081423693">🤔</tg-emoji>'
    )
    kb = main_menu(callback.from_user.id)

    await callback.answer()

    try:
        await callback.message.edit_text(
            text=text,
            parse_mode="HTML",
            reply_markup=kb
        )
    except TelegramBadRequest as e:
        # Игнорируем ошибку, если текст/клавиатура сообщения не изменились
        if "message is not modified" not in str(e):
            raise e
