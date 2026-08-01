from datetime import datetime
import sqlite3

# --- НАСТРОЙКИ ---
DB_PATH = "database.db"  # Укажите точный путь к базе (.db файл)
TARGET_USER_ID = 7542007802  # Telegram ID пользователя

# Выберите действие:
# "add"    — прибавить к сумме пополнений
# "reduce" — уменьшить сумму пополнений
MODE = "reduce"

AMOUNT = 9.07  # Сумма, которую нужно добавить или отнять
# ------------------

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

try:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Определяем знак суммы в зависимости от режима
    if MODE == "add":
        final_amount = abs(AMOUNT)  # Положительное число (прибавит)
        provider_tag = "Admin Deposit"
        message_action = "увеличена"
    elif MODE == "reduce":
        final_amount = -abs(AMOUNT)  # Отрицательное число (уменьшит)
        provider_tag = "Admin Reduction"
        message_action = "уменьшена"
    else:
        raise ValueError("Неверный MODE! Используйте 'add' или 'reduce'.")

    # Вставляем запись в таблицу deposits
    cursor.execute(
        """
        INSERT INTO deposits (user_id, amount, provider, timestamp)
        VALUES (?, ?, ?, ?)
    """,
        (TARGET_USER_ID, final_amount, provider_tag, now),
    )

    conn.commit()
    print(
        f"Успешно! Добавлена запись на {final_amount}. "
        f"Общая сумма пополнений пользователя {TARGET_USER_ID} {message_action} на {abs(AMOUNT)}."
    )

except Exception as e:
    conn.rollback()
    print(f"Ошибка при выполнении операции: {e}")

finally:
    conn.close()