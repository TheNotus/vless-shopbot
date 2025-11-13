#!/usr/bin/env python3
"""
Скрипт для проверки результатов симуляции регистрации и работы реферальной системы.
"""
import sys
import os
from datetime import datetime, timedelta

# Добавляем путь к директории src в sys.path для импорта модулей
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from shop_bot.data_manager.database import get_user, get_key_by_id


def main():
    print("=== Проверка результатов симуляции реферальной системы ===\n")
    
    # Проверка 1: Получаем данные нового пользователя с user_id=999 (так как в simulate_registration.py используется telegram_id=999)
    print("Проверка 1: Получение данных нового пользователя (telegram_id=999)...")
    user_data = get_user(999)
    
    if user_data:
        referred_by = user_data.get('referred_by')
        expected_referrer = 1
        print(f"  - Поле 'referred_by': {referred_by}")
        print(f"  - Ожидаемое значение: {expected_referrer}")
        
        if referred_by == expected_referrer:
            print("  - РЕЗУЛЬТАТ: УСПЕХ - Значение соответствует ожидаемому\n")
            check1_result = "УСПЕХ"
        else:
            print("  - РЕЗУЛЬТАТ: НЕУДАЧА - Значение не соответствует ожидаемому\n")
            check1_result = "НЕУДАЧА"
    else:
        print("  - РЕЗУЛЬТАТ: НЕУДАЧА - Пользователь с telegram_id=99 не найден\n")
        check1_result = "НЕУДАЧА"
    
    # Проверка 2: Получаем данные ключа пользователя с ID=1 (ID ключа 20), сравниваем дату истечения
    print("Проверка 2: Получение данных ключа с ID=20 и проверка даты истечения...")
    key_data = get_key_by_id(20)
    
    if key_data:
        current_expiry = key_data.get('expiry_date')
        print(f"  - Текущая дата истечения: {current_expiry}")
        
        # Предполагаем, что исходная дата была 2025-12-11, проверим, увеличилась ли она на 3 дня
        try:
            original_date = datetime(2025, 12, 11)
            expected_new_date = original_date + timedelta(days=3)  # Добавляем 3 дня как ожидалось
            
            # Преобразуем строку даты из БД в объект datetime для сравнения
            if current_expiry:
                db_expiry = datetime.fromisoformat(current_expiry.replace('Z', '+00:00'))
                
                print(f" - Ожидаемая новая дата (исходная + 3 дня): {expected_new_date}")
                
                if db_expiry >= expected_new_date:
                    print("  - РЕЗУЛЬТАТ: УСПЕХ - Дата истечения увеличилась как ожидалось\n")
                    check2_result = "УСПЕХ"
                else:
                    print("  - РЕЗУЛЬТАТ: НЕУДАЧА - Дата истечения не увеличилась должным образом\n")
                    check2_result = "НЕУДАЧА"
            else:
                print("  - РЕЗУЛЬТАТ: НЕУДАЧА - Дата истечения отсутствует\n")
                check2_result = "НЕУДАЧА"
        except ValueError as e:
            print(f"  - ОШИБКА при обработке даты: {e}\n")
            check2_result = "НЕУДАЧА"
    else:
        print("  - РЕЗУЛЬТАТ: НЕУДАЧА - Ключ с ID=20 не найден\n")
        check2_result = "НЕУДАЧА"
    
    # Выводим итоговые результаты
    print("=== ИТОГИ ПРОВЕРКИ ===")
    print(f"Проверка 1 (данные пользователя): {check1_result}")
    print(f"Проверка 2 (дата истечения ключа): {check2_result}")
    
    if check1_result == "УСПЕХ" and check2_result == "УСПЕХ":
        print("\nВсе проверки пройдены успешно! Реферальная система работает корректно.")
    else:
        print("\nОбнаружены проблемы с реферальной системой. Требуется дополнительное исследование.")


if __name__ == "__main__":
    main()