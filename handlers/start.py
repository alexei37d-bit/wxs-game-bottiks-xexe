from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.enums import ParseMode
from database.db import add_user
from aiogram.types import FSInputFile


router = Router()


from config import ADMIN_ID

def main_menu(user_id):
    keyboard = [
        [
            InlineKeyboardButton(
                text="Играть",
                callback_data="play",
                icon_custom_emoji_id="5467583879948803288"
            ),
            InlineKeyboardButton(
                text="Баланс",
                callback_data="balance",
                icon_custom_emoji_id="6039641775377748623"
            )
        ],
        [
            InlineKeyboardButton(
                text="Профиль",
                callback_data="profile",
                icon_custom_emoji_id="5904630315946611415"
            )
        ],
        [
            InlineKeyboardButton(
                text="Бонус",
                callback_data="bonus",
                icon_custom_emoji_id="6183629501709160320"
            ),
            InlineKeyboardButton(
                text="Рефералы",
                callback_data="refs",
                icon_custom_emoji_id="5400016846017548469"
            )
        ],
        [
            InlineKeyboardButton(
                text="Настройки",
                callback_data="settings",
                icon_custom_emoji_id="5902432207519093015"
            )
        ]
    ]

    if user_id == ADMIN_ID:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Админ панель",
                    callback_data="admin_panel",
                    icon_custom_emoji_id="5213214428958306222"
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=keyboard
    )


@router.message(CommandStart())
async def start(message: Message):
    add_user(message.from_user.id)

    await message.answer(
        text="""
<tg-emoji emoji-id="5465281219132415627">🖤</tg-emoji> <b>Добро пожаловать!</b> <tg-emoji emoji-id="5085022089103016925">⚡️</tg-emoji>

Куда собрались? <tg-emoji emoji-id="5258184841081423693">🤔</tg-emoji>
""",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(message.from_user.id)
    )
