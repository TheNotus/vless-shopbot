#!/usr/bin/env python3
"""
Скрипт для проверки принадлежности ключа с key_id=20 пользователю user_id=1
"""
import sqlite3
from pathlib import Path

# Путь к базе данных
BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "users.db"

def initialize_db():
    """Инициализирует базу данных, создавая необходимые таблицы"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY, username TEXT, total_spent REAL DEFAULT 0,
                    total_months INTEGER DEFAULT 0, trial_used BOOLEAN DEFAULT 0,
                    agreed_to_terms BOOLEAN DEFAULT 0,
                    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_banned BOOLEAN DEFAULT 0,
                    referred_by INTEGER,
                    referral_balance REAL DEFAULT 0,
                    referral_balance_all REAL DEFAULT 0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vpn_keys (
                    key_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    host_name TEXT NOT NULL,
                    xui_client_uuid TEXT NOT NULL,
                    key_email TEXT NOT NULL UNIQUE,
                    expiry_date TIMESTAMP,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    key_name TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    username TEXT,
                    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    amount_rub REAL NOT NULL,
                    amount_currency REAL,
                    currency_name TEXT,
                    payment_method TEXT,
                    metadata TEXT,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS support_threads (
                    user_id INTEGER PRIMARY KEY,
                    thread_id INTEGER NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS xui_hosts(
                    host_name TEXT NOT NULL,
                    host_url TEXT NOT NULL,
                    host_username TEXT NOT NULL,
                    host_pass TEXT NOT NULL,
                    host_inbound_id INTEGER NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_name TEXT NOT NULL,
                    plan_name TEXT NOT NULL,
                    months INTEGER NOT NULL,
                    price REAL NOT NULL,
                    FOREIGN KEY (host_name) REFERENCES xui_hosts (host_name)
                )
            ''')
            conn.commit()
            print("База данных инициализирована успешно.")
    except sqlite3.Error as e:
        print(f"Ошибка при инициализации базы данных: {e}")

def check_key_ownership():
    """Проверяет, принадлежит ли ключ с key_id=20 пользователю user_id=1"""
    try:
        # Инициализируем базу данных, если она еще не создана
        initialize_db()
        
        # Подключение к базе данных
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            
            # Проверим, есть ли вообще какие-либо ключи в базе данных
            cursor.execute("SELECT COUNT(*) FROM vpn_keys;")
            total_keys = cursor.fetchone()[0]
            print(f"Всего ключей в базе данных: {total_keys}")
            
            # Выполнение основного SQL-запроса
            query = "SELECT user_id, key_id, key_email FROM vpn_keys WHERE key_id = 20;"
            cursor.execute(query)
            
            # Получение результата
            result = cursor.fetchone()
            
            if result:
                user_id, key_id, key_email = result
                print(f"Результат запроса: user_id={user_id}, key_id={key_id}, key_email={key_email}")
                
                if user_id == 1:
                    print(f"✓ Ключ с key_id={key_id} действительно принадлежит пользователю user_id={user_id}")
                else:
                    print(f"✗ Ключ с key_id={key_id} принадлежит пользователю user_id={user_id}, а не user_id=1")
            else:
                print("Ключ с key_id=20 не найден в базе данных")
                
                # Выведем все доступные ключи, если они есть
                if total_keys > 0:
                    print("\nВсе доступные ключи в базе данных:")
                    cursor.execute("SELECT user_id, key_id, key_email FROM vpn_keys;")
                    all_keys = cursor.fetchall()
                    for user_id, key_id, key_email in all_keys:
                        print(f"  - user_id={user_id}, key_id={key_id}, key_email={key_email}")
                
    except sqlite3.Error as e:
        print(f"Ошибка при работе с базой данных: {e}")

if __name__ == "__main__":
    check_key_ownership()