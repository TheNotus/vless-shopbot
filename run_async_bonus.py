import asyncio
import sys
import os

# Добавляем путь к директории src в sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Импортируем необходимые функции
from shop_bot.data_manager.database import get_user_keys, get_host, add_new_key
from shop_bot.modules.xui_api import create_or_update_key_on_host
from shop_bot.config import REFERRAL_BONUS_DAYS

# Создаем псевдоним для xui_api для совместимости с grant_referral_bonus
import shop_bot.modules.xui_api as xui_api

async def grant_referral_bonus(referrer_id: int):
    """
    Grants a referral bonus to the referrer.
    Finds all active keys for the referrer and extends their expiration date.
    """
    print(f"Attempting to grant referral bonus to referrer_id: {referrer_id}")
    
    # Get all keys for the referrer from the database
    user_keys_db = get_user_keys(referrer_id)
    
    if not user_keys_db:
        print(f"No active keys found for referrer_id: {referrer_id}. No bonus granted.")
        return

    # For each key, update the expiry on the XUI panel
    updated_count = 0
    for key in user_keys_db:
        host_name = key['host_name']
        key_email = key['key_email']
        
        # Get the current key details from the XUI panel
        host_data = get_host(host_name)
        if not host_data:
            print(f"Host {host_name} not found for key {key_email}")
            continue
            
        # Update or create the client with additional days to extend the expiry
        # This will effectively add REFERRAL_BONUS_DAYS to the current expiry
        result = await xui_api.create_or_update_key_on_host(host_name, key_email, REFERRAL_BONUS_DAYS)
        if result:
            updated_count += 1
            print(f"Successfully updated expiry for key {key_email}")
        else:
            print(f"Failed to update expiry for key {key_email}")
    
    print(f"Successfully granted bonus to {updated_count} keys for referrer_id: {referrer_id}")

async def main():
    await grant_referral_bonus(1)

if __name__ == "__main__":
    asyncio.run(main())