import sqlite3
from datetime import datetime

DB_PATH = "database.db"


def get_connection():
    """Создает подключение и настраивает доступ к полям по именам"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Теперь строки работают как словари!
    return conn


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()

        # Таблица пользователей
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                reg_date TEXT,
                balance REAL DEFAULT 0,
                turnover REAL DEFAULT 0,
                deposits REAL DEFAULT 0,
                withdrawals REAL DEFAULT 0,
                bonus_day INTEGER DEFAULT 1,
                last_bonus INTEGER DEFAULT 0,
                total_bonus REAL DEFAULT 0,
                bonus_notify INTEGER DEFAULT 1
            )
        """)

        # Таблица инвойсов (TEXT PRIMARY KEY подходит и для CryptoBot, и для xRocket)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                invoice_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                paid INTEGER DEFAULT 0
            )
        """)

        conn.commit()


# Инициализируем структуры при запуске
init_db()


# === ФУНКЦИИ ДЛЯ РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ ===

def add_user(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users(user_id, reg_date) VALUES(?, ?)",
                (user_id, datetime.now().strftime("%d.%m.%Y %H:%M"))
            )
            conn.commit()


def get_user(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return cursor.fetchone()  # Можно обращаться: user["balance"] или dict(user)


def add_balance(user_id, amount):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (amount, user_id)
        )
        conn.commit()


def subtract_balance(user_id, amount):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=?",
            (amount, user_id)
        )
        conn.commit()


def add_turnover(user_id: int, amount: float):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET turnover = turnover + ? WHERE user_id = ?",
            (amount, int(user_id)),
        )
        conn.commit()


# === БОНУСНАЯ СИСТЕМА ===

def get_bonus(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT bonus_day, last_bonus FROM users WHERE user_id=?",
            (user_id,)
        )
        return cursor.fetchone()


def take_bonus(user_id, amount):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users
            SET balance     = balance + ?,
                total_bonus = total_bonus + ?,
                last_bonus  = strftime('%s', 'now')
            WHERE user_id = ?
            """,
            (amount, amount, user_id)
        )
        conn.commit()


def increase_bonus_day(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET bonus_day = bonus_day + 1 WHERE user_id=?",
            (user_id,)
        )
        conn.commit()


# === ИНВОЙСЫ ===

def save_invoice(invoice_id, user_id, amount):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO invoices(invoice_id, user_id, amount) VALUES(?,?,?)",
            (str(invoice_id), user_id, amount)
        )
        conn.commit()


def get_invoice(invoice_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM invoices WHERE invoice_id=?",
            (str(invoice_id),)
        )
        return cursor.fetchone()


def invoice_paid(invoice_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE invoices SET paid=1 WHERE invoice_id=?",
            (str(invoice_id),)
        )
        conn.commit()


def is_paid(invoice_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT paid FROM invoices WHERE invoice_id=?",
            (str(invoice_id),)
        )
        row = cursor.fetchone()
        return row["paid"] if row else 0


def get_top_users_by_balance(limit=50):
    """
    Получает пользователей, отсортированных по балансу от большего к меньшему.
    limit: ограничение на количество пользователей, чтобы сообщение не вышло слишком длинным.
    """
    conn = sqlite3.connect("database.db")  # Укажи имя своего файла БД
    cursor = conn.cursor()

    # Сортируем по balance по убыванию (DESC)
    cursor.execute("""
                   SELECT first_name, username, balance
                   FROM users
                   ORDER BY balance DESC LIMIT ?
                   """, (limit,))

    users = cursor.fetchall()
    conn.close()
    return users

def add_balance(user_id, amount):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = balance + ?, deposits = deposits + ? WHERE user_id=?",
            (amount, amount, user_id)
        )
        conn.commit()


def get_top_turnover(limit: int = 10):
    """Возвращает ТОП игроков по обороту (turnover)"""
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT telegram_id, turnover
                       FROM users
                       WHERE turnover > 0
                       ORDER BY turnover DESC LIMIT ?
                       """, (limit,))
        return cursor.fetchall()


def get_top_turnover(limit: int = 10):
    """Возвращает ТОП игроков по обороту"""
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        # Заменили telegram_id на user_id (или id)
        cursor.execute("""
                       SELECT user_id, turnover
                       FROM users
                       WHERE turnover > 0
                       ORDER BY turnover DESC LIMIT ?
                       """, (limit,))
        return cursor.fetchall()


def get_top_turnover(limit: int = 10):
    """
    Возвращает ТОП игроков по обороту.
    Предполагается, что в таблице users есть колонки: user_id, first_name, username, turnover.
    Замените названия колонок, если у вас они отличаются.
    """
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT user_id, first_name, username, turnover
                       FROM users
                       WHERE turnover > 0
                       ORDER BY turnover DESC LIMIT ?
                       """, (limit,))
        return cursor.fetchall()


def get_top_turnover(limit: int = 10):
    """Возвращает ТОП пользователей по обороту"""
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT user_id, full_name, username, turnover
                       FROM users
                       WHERE turnover > 0
                       ORDER BY turnover DESC LIMIT ?
                       """, (limit,))
        return cursor.fetchall()


def get_top_turnover(limit: int = 10):
    """Возвращает ТОП по обороту (user_id, username, turnover)"""
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT user_id, username, turnover
                       FROM users
                       WHERE turnover > 0
                       ORDER BY turnover DESC LIMIT ?
                       """, (limit,))
        return cursor.fetchall()


def get_top_turnover(limit: int = 10):
    """Возвращает ТОП-10 пользователей (user_id, turnover)"""
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT user_id, turnover
                       FROM users
                       WHERE turnover > 0
                       ORDER BY turnover DESC LIMIT ?
                       """, (limit,))
        return cursor.fetchall()


def get_user_turnover_rank(user_id: int):
    """Возвращает место и оборот текущего пользователя"""
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT turnover FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()

        if not res or res[0] is None or res[0] <= 0:
            return None, 0.0

        user_turnover = res[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE turnover > ?", (user_turnover,))
        rank = cursor.fetchone()[0] + 1

        return rank, user_turnover
