# basketball_game.py (ПОЛНОСТЬЮ ПЕРЕПИСАННАЯ ВЕРСИЯ)

import random
import asyncio
import logging
import time
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


logger = logging.getLogger(__name__)


class BasketballGame:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.last_click_time = {}

    def check_cooldown(self, user_id: int, cooldown_seconds: float = Config.CLICK_COOLDOWN) -> bool:
        """Проверка кулдауна между кликами"""
        current_time = time.time()
        if user_id in self.last_click_time:
            time_diff = current_time - self.last_click_time[user_id]
            if time_diff < cooldown_seconds:
                return False
        self.last_click_time[user_id] = current_time
        return True

    def safe_float(self, value, default=0.0):
        """Безопасное преобразование в float"""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    async def safe_delete_message(self, message: types.Message):
        """Безопасное удаление сообщения"""
        try:
            if message:
                await message.delete()
        except (MessageToDeleteNotFound, MessageCantBeDeleted):
            logger.debug(f"Сообщение уже удалено или не может быть удалено")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

    async def start_game_from_message(self, message: types.Message, bet: float, bet_type: str):
        """Запустить игру из текстового сообщения"""
        try:
            user_id = message.from_user.id

            if bet <= 0:
                await message.reply("❌ Ставка должна быть положительной!")
                return

            user = self.db.get_user(user_id)
            player_name = message.from_user.full_name or message.from_user.username or f"ID: {user_id}"

            if not user or self.safe_float(user[3], 0) < bet:
                await message.reply("❌ Недостаточно средств на балансе!")
                return

            if bet < Config.MIN_BET:
                await message.reply(f"❌ Минимальная ставка: {Config.MIN_BET} USDT")
                return
            if bet > Config.MAX_BET:
                await message.reply(f"❌ Максимальная ставка: {Config.MAX_BET} USDT")
                return

            # Списываем ставку
            self.db.update_balance(user_id, -bet)
            self.db.add_transaction(user_id, -bet, "bet", f"Ставка в Баскетбол: {bet_type}")

            # Бросаем мяч
            basketball_msg = await message.reply_dice(emoji="🏀")
            basketball_value = basketball_msg.dice.value

            await asyncio.sleep(4)

            # Определяем результат
            result = self._calculate_result(basketball_value, bet_type)

            if result['win']:
                win_amount = bet * result['multiplier']
                self.db.update_balance(user_id, win_amount)
                self.db.update_turnover(user_id, bet)
                self.db.add_game_result(user_id, "basketball", bet, win_amount, f"{bet_type}: {basketball_value}", multiplier=result['multiplier'])
                self.db.add_transaction(user_id, win_amount, 'win', f'Выигрыш в Баскетбол')
                result_text = f"🎉 Победа! Выигрыш: {win_amount:.2f} USDT"
            else:
                win_amount = 0
                self.db.update_turnover(user_id, bet)
                self.db.add_game_result(user_id, "basketball", bet, 0, f"{bet_type}: {basketball_value}", multiplier=0)
                self.db.add_transaction(user_id, 0, 'loss', f'Проигрыш в Баскетбол')
                result_text = f"❌ Проигрыш: {bet:.2f} USDT"

                # Кешбек
                if bet >= Config.MIN_CASHBACK_BET:
                    rank = user[8] or 1
                    cashback_percent = Config.RANKS[rank]['cashback']
                    cashback_amount = bet * cashback_percent
                    self.db.update_balance(user_id, cashback_amount)
                    self.db.add_transaction(user_id, cashback_amount, "cashback", f"Кешбек за ставку в Баскетбол")

            await self.bot.send_message(
                message.chat.id,
                f"🏀 <b>Результат игры в Баскетбол</b>\n\n"
                f"👤 <b>Игрок:</b> {player_name}\n"
                f"💰 Ставка: {bet:.2f} USDT\n"
                f"🏀 Тип: {self.get_bet_type_name(bet_type)}\n"
                f"🏀 Выпало: {basketball_value}\n"
                f"{result_text}\n"
                f"📊 Коэффициент: {result['multiplier']}x",
                parse_mode=ParseMode.HTML
            )

        except Exception as e:
            logger.error(f"Ошибка в start_game_from_message Basketball: {e}")
            await message.reply("❌ Ошибка при запуске игры")

    async def start_game(self, callback_query: types.CallbackQuery, bet: float, bet_type: str):
        """Запустить игру из callback (для ЛС)"""
        try:
            user_id = callback_query.from_user.id

            if not self.check_cooldown(user_id, Config.CLICK_COOLDOWN):
                await callback_query.answer(
                    f"⏳ Пожалуйста, не так быстро! Подождите {Config.CLICK_COOLDOWN:.1f} секунду...",
                    show_alert=False
                )
                return

            if bet <= 0:
                await callback_query.answer("❌ Ставка должна быть положительной!", show_alert=True)
                return

            message = callback_query.message

            user = self.db.get_user(user_id)
            if not user or self.safe_float(user[3], 0) < bet:
                await callback_query.answer("❌ Недостаточно средств на балансе!", show_alert=True)
                return

            if bet < Config.MIN_BET:
                await callback_query.answer(f"❌ Минимальная ставка: {Config.MIN_BET} USDT", show_alert=True)
                return
            if bet > Config.MAX_BET:
                await callback_query.answer(f"❌ Максимальная ставка: {Config.MAX_BET} USDT", show_alert=True)
                return

            # Списываем ставку
            self.db.update_balance(user_id, -bet)
            self.db.add_transaction(user_id, -bet, "bet", f"Ставка в Баскетбол: {bet_type}")

            # Безопасно удаляем сообщение
            await self.safe_delete_message(message)

            # Бросаем мяч
            basketball_msg = await message.answer_dice(emoji="🏀")
            basketball_value = basketball_msg.dice.value

            await asyncio.sleep(4)

            # Определяем результат
            result = self._calculate_result(basketball_value, bet_type)

            if result['win']:
                win_amount = bet * result['multiplier']
                self.db.update_balance(user_id, win_amount)
                self.db.update_turnover(user_id, bet)
                self.db.add_game_result(user_id, "basketball", bet, win_amount, f"{bet_type}: {basketball_value}", multiplier=result['multiplier'])
                self.db.add_transaction(user_id, win_amount, 'win', f'Выигрыш в Баскетбол')
                result_text = f"🎉 Победа! Выигрыш: {win_amount:.2f} USDT"
            else:
                win_amount = 0
                self.db.update_turnover(user_id, bet)
                self.db.add_game_result(user_id, "basketball", bet, 0, f"{bet_type}: {basketball_value}", multiplier=0)
                self.db.add_transaction(user_id, 0, 'loss', f'Проигрыш в Баскетбол')
                result_text = f"❌ Проигрыш: {bet:.2f} USDT"

                # Кешбек
                if bet >= Config.MIN_CASHBACK_BET:
                    rank = user[8] or 1
                    cashback_percent = Config.RANKS[rank]['cashback']
                    cashback_amount = bet * cashback_percent
                    self.db.update_balance(user_id, cashback_amount)
                    self.db.add_transaction(user_id, cashback_amount, "cashback", f"Кешбек за ставку в Баскетбол")

            keyboard = None
            if message.chat.type == 'private':
                keyboard = self.create_result_keyboard(bet)

            await self.bot.send_message(
                message.chat.id,
                f"🏀 <b>Результат игры в Баскетбол</b>\n\n"
                f"💰 Ставка: {bet:.2f} USDT\n"
                f"🏀 Тип: {self.get_bet_type_name(bet_type)}\n"
                f"🏀 Выпало: {basketball_value}\n"
                f"{result_text}\n"
                f"📊 Коэффициент: {result['multiplier']}x",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )

            await callback_query.answer()

        except Exception as e:
            logger.error(f"Ошибка в start_game Basketball: {e}")
            await callback_query.answer("❌ Ошибка при запуске игры", show_alert=True)

    async def show_bet_type_menu(self, message: types.Message, bet: float):
        """Показать меню выбора исхода для баскетбола"""
        try:
            if bet <= 0:
                await message.reply("❌ Ставка должна быть положительной!")
                return

            keyboard = self.get_bet_type_keyboard(bet)
            await message.reply(
                f"🏀 <b>Игра в Баскетбол</b>\n\n"
                f"💰 <b>Ставка:</b> {bet:.2f} USDT\n\n"
                f"<b>Выберите исход:</b>\n"
                f"• Гол - x{Config.BASKETBALL_COEFFS.get('гол', 1.9)}\n"
                f"• Мимо - x{Config.BASKETBALL_COEFFS.get('мимо', 1.4)}\n"
                f"• Застрял - x{Config.BASKETBALL_COEFFS.get('застрял', 2.75)}",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка в show_bet_type_menu Basketball: {e}")

    async def show_bet_type_menu_callback(self, callback_query: types.CallbackQuery, bet: float):
        """Показать меню выбора типа ставки через callback"""
        try:
            if not self.check_cooldown(callback_query.from_user.id, Config.CLICK_COOLDOWN):
                await callback_query.answer(
                    f"⏳ Пожалуйста, не так быстро! Подождите {Config.CLICK_COOLDOWN:.1f} секунду...",
                    show_alert=False
                )
                return

            if bet <= 0:
                await callback_query.answer("❌ Ставка должна быть положительной!", show_alert=True)
                return

            keyboard = self.get_bet_type_keyboard(bet)
            text = (
                f"🏀 <b>Игра в Баскетбол</b>\n\n"
                f"💰 <b>Ставка:</b> {bet:.2f} USDT\n\n"
                f"<b>Выберите исход:</b>\n"
                f"• Гол - x{Config.BASKETBALL_COEFFS.get('гол', 1.9)}\n"
                f"• Мимо - x{Config.BASKETBALL_COEFFS.get('мимо', 1.4)}\n"
                f"• Застрял - x{Config.BASKETBALL_COEFFS.get('застрял', 2.75)}"
            )

            try:
                await callback_query.message.edit_text(
                    text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.debug(f"Не удалось отредактировать сообщение Basketball, отправляем новое: {e}")
                await callback_query.message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

            await callback_query.answer()
        except Exception as e:
            logger.error(f"Ошибка в show_bet_type_menu_callback Basketball: {e}")

    def get_bet_type_keyboard(self, bet: float):
        """Клавиатура для выбора типа ставки"""
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton(f"🏀 Гол (x{Config.BASKETBALL_COEFFS.get('гол', 1.9)})",
                                 callback_data=f"basketball_bet_гол_{bet}"),
            InlineKeyboardButton(f"❌ Мимо (x{Config.BASKETBALL_COEFFS.get('мимо', 1.4)})",
                                 callback_data=f"basketball_bet_мимо_{bet}"),
            InlineKeyboardButton(f"⚠️ Застрял (x{Config.BASKETBALL_COEFFS.get('застрял', 2.75)})",
                                 callback_data=f"basketball_bet_застрял_{bet}"),
            InlineKeyboardButton("⬅️ Назад к играм", callback_data="play_games")
        )
        return keyboard

    def create_result_keyboard(self, bet: float):
        """Создать клавиатуру для результата (только для ЛС)"""
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🏀 Сыграть ещё", callback_data=f"basketball_repeat_{bet}"),
            InlineKeyboardButton("⬅️ Назад к играм", callback_data="play_games")
        )
        return keyboard

    def get_bet_type_name(self, bet_type: str) -> str:
        """Получить читаемое название типа ставки"""
        names = {
            'гол': 'Гол',
            'мимо': 'Мимо',
            'застрял': 'Застрял'
        }
        return names.get(bet_type, bet_type)

    def _calculate_result(self, basketball_value: int, bet_type: str):
        """Определить результат броска баскетбола"""
        result = {
            'win': False,
            'multiplier': 0,
            'description': ''
        }

        if bet_type == 'гол':
            result['win'] = basketball_value in [4, 5]
            result['multiplier'] = Config.BASKETBALL_COEFFS.get('гол', 1.9)
        elif bet_type == 'мимо':
            result['win'] = basketball_value in [1, 2]
            result['multiplier'] = Config.BASKETBALL_COEFFS.get('мимо', 1.4)
        elif bet_type == 'застрял':
            result['win'] = basketball_value == 3
            result['multiplier'] = Config.BASKETBALL_COEFFS.get('застрял', 2.75)

        return result

    async def handle_repeat_game(self, callback_query: types.CallbackQuery):
        """Обработать повторную игру"""
        try:
            if not self.check_cooldown(callback_query.from_user.id, Config.CLICK_COOLDOWN):
                await callback_query.answer(
                    f"⏳ Пожалуйста, не так быстро! Подождите {Config.CLICK_COOLDOWN:.1f} секунду...",
                    show_alert=False
                )
                return

            bet = float(callback_query.data.split('_')[2])
            await self.show_bet_type_menu_callback(callback_query, bet)
        except Exception as e:
            logger.error(f"Ошибка в handle_repeat_game Basketball: {e}")
            await callback_query.answer("❌ Ошибка при запуске игры", show_alert=True)