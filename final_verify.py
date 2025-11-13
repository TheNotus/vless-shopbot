import asyncio
from datetime import datetime, timedelta
from src.shop_bot.data_manager.database import get_key_by_id

async def main():
    key_id_to_check = 20
    print(f"Проверяем ключ с key_id = {key_id_to_check}")

    # Получаем исходную информацию о ключе
    key_before = get_key_by_id(key_id_to_check)
    if not key_before:
        print(f"ОШИБКА: Ключ с key_id = {key_id_to_check} не найден.")
        return

    original_expiry_str = key_before['expiry_date']
    # Конвертируем строку в объект datetime, если она не None
    # Формат даты в БД: 'YYYY-MM-DD HH:MM:SS.ssssss'
    original_expiry_date = datetime.strptime(original_expiry_str, '%Y-%m-%d %H:%M:%S.%f') if original_expiry_str else datetime.now()
    
    # Получаем ключ после предполагаемого обновления
    # В нашем сценарии, бонус уже был применен скриптом run_async_bonus.py
    key_after = get_key_by_id(key_id_to_check)

    # --- Проверка 1: key_name ---
    expected_key_name = 'BONUS_APPLIED_TEST'
    actual_key_name = key_after.get('key_name')
    print(f"Проверка 1: `key_name`")
    if actual_key_name == expected_key_name:
        print(f"  [УСПЕХ] `key_name` успешно обновлен на '{actual_key_name}'.")
    else:
        print(f"  [ПРОВАЛ] Ожидалось `key_name` = '{expected_key_name}', но получено: '{actual_key_name}'.")

    # --- Проверка 2: expiry_date ---
    # Мы не можем знать точное время "до", поэтому просто проверим, что дата изменилась
    # и стала больше, чем была.
    new_expiry_str = key_after['expiry_date']
    new_expiry_date = datetime.strptime(new_expiry_str, '%Y-%m-%d %H:%M:%S.%f') if new_expiry_str else None
    
    print(f"Проверка 2: `expiry_date`")
    if new_expiry_date and new_expiry_date > original_expiry_date:
        print(f"  [УСПЕХ] `expiry_date` изменилась. Старое значение: {original_expiry_str}, Новое значение: {new_expiry_str}.")
    else:
        print(f"  [ПРОВАЛ] `expiry_date` не изменилась или стала меньше. Старое: {original_expiry_str}, Новое: {new_expiry_str}.")

if __name__ == "__main__":
    asyncio.run(main())