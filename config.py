# config.py
import os

BOT_TOKEN = "8923421542:AAEyCogYhJdc2vYwliOQ00gMiNmbrEPUt7c"

CRYPTOBOT_TOKEN = "613002:AAXR6WKD7yZwTJcBo6kmhjcBnC3wx3mHB4Y"

XROCKET_API_KEY = "0b53d75d7a3ce3c486b174893"

ADMIN_ID = 6130985988

# === ПУТЬ К БАЗЕ ДАННЫХ ===
# Единая точка правды для всего проекта. Раньше "database.db" было
# захардкожено по отдельности в 6+ файлах — из-за этого легко было
# случайно завести "вторую" пустую базу и потерять данные.
#
# Если хостинг даёт отдельную папку для ПОСТОЯННОГО хранения файлов
# (persistent storage / volume / диск), которая НЕ пересоздаётся при
# обновлении/рестарте — укажите её через переменную окружения DB_PATH,
# например: DB_PATH=/data/database.db
#
# Без переменной окружения база лежит рядом с кодом — это ок для
# локального запуска, но на хостинге, где при каждом обновлении
# репозиторий пересобирается с нуля, файл рядом с кодом будет теряться.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "database.db"))

# Создаём папку под базу, если её ещё нет (актуально для DB_PATH вида /data/database.db)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)