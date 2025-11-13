#!/usr/bin/env python3
"""
Скрипт для симуляции регистрации нового пользователя и начисления реферального бонуса.
"""
import asyncio
import sys
import os

# Добавляем путь к директории src в sys.path для импорта модулей
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from shop_bot.data_manager.database import register_user_if_not_exists, get_user
from shop_bot.config import REFERRAL_BONUS_DAYS


async def grant_referral_bonus(referrer_id: int):
    """
    Имитация функции начисления реферального бонуса.
    В реальной системе эта функция продлевает срок действия ключей реферера,
    но для симуляции мы просто выводим сообщение.
    """
    print(f"Attempting to grant referral bonus to referrer_id: {referrer_id}")
    print(f"Referral bonus would add {REFERRAL_BONUS_DAYS} days to active keys")
    print("Referral bonus granted successfully (simulated)")


async def main():
    print("=== Симуляция регистрации нового пользователя ===")
    
    # Параметры для регистрации
    telegram_id = 999
    username = 'test_referral'
    referrer_id = 1
    
    print(f"Регистрация пользователя с параметрами:")
    print(f"  - telegram_id: {telegram_id}")
    print(f"  - username: {username}")
    print(f"  - referrer_id: {referrer_id}")
    
    # Регистрируем пользователя
    register_user_if_not_exists(telegram_id, username, referrer_id)
    print("Пользователь зарегистрирован (или обновлен)")
    
    # Получаем данные пользователя для проверки
    user_data = get_user(telegram_id)
    print(f"Данные пользователя после регистрации: {user_data}")
    
    # Начисляем реферальный бонус рефереру
    print(f"\nНачисление реферального бонуса рефереру с ID {referrer_id}...")
    await grant_referral_bonus(referrer_id)
    
    print("\n=== Симуляция завершена успешно ===")
    print("Проверьте, что данные корректно записались в БД и бонус начислен.")


if __name__ == "__main__":
    asyncio.run(main())