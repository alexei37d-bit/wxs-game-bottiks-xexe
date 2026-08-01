from aiogram.fsm.state import StatesGroup, State

class CubeGame(StatesGroup):
    bet = State()      # Ввод суммы ставки
    number = State()   # Выбор точного числа (если выбран этот режим)
