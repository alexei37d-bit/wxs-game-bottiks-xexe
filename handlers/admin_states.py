from aiogram.fsm.state import StatesGroup, State


class AdminBalance(StatesGroup):
    user_id = State()
    amount = State()


class AdminDecreaseBalance(StatesGroup):
    user_id = State()
    amount = State()