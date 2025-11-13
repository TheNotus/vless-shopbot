#!/usr/bin/env python3
"""
Скрипт для проверки и создания VPN-ключа для пользователя с ID 1
"""

import sys
import os
from datetime import datetime, timedelta

# Добавляем путь к модулям бота
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from shop_bot.data_manager.database import get_user_keys, add_new_key

def main():
    user_id = 1
    
    print(f"Проверяем наличие VPN-ключей у пользователя с ID {user_id}...")
    
    # Проверяем наличие ключей у пользователя
    user_keys = get_user_keys(user_id)
    
    if user_keys:
        print(f"У пользователя {user_id} уже есть {len(user_keys)} ключ(ей):")
        for key in user_keys:
            print(f"  - ID: {key['key_id']}, Email: {key['key_email']}, Хост: {key['host_name']}, Создан: {key['created_date']}, Истекает: {key['expiry_date']}")
    else:
        print(f"У пользователя {user_id} нет активных ключей. Создаем новый ключ...")
        
        # Создаем новый ключ с валидными данными
        host_name = 'try test'
        email = 'ref_user@test.com'
        # Генерируем случайный UUID для xui_client_uuid
        import uuid
        xui_client_uuid = str(uuid.uuid4())
        days = 30
        key_name = 'Test Key for Referral Testing'
        
        new_key_id = add_new_key(
            user_id=user_id,
            email=email,
            xui_client_uuid=xui_client_uuid,
            host_name=host_name,
            days=days,
            key_email=email,
            key_name=key_name
        )
        
        if new_key_id:
            print(f"Новый ключ успешно создан с ID: {new_key_id}")
        else:
            print("Ошибка при создании нового ключа!")
            return
        
        # Повторно проверяем ключи после создания
        user_keys = get_user_keys(user_id)
        print(f"Теперь у пользователя {user_id} есть {len(user_keys)} ключ(ей):")
        for key in user_keys:
            print(f"  - ID: {key['key_id']}, Email: {key['key_email']}, Хост: {key['host_name']}, Создан: {key['created_date']}, Истекает: {key['expiry_date']}")


if __name__ == "__main__":
    main()