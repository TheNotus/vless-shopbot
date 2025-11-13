import asyncio
import aiohttp
import json


async def test_payment_webhook():
    """
    Тестовый скрипт для проверки процесса оплаты и выдачи ключа
    путем имитации веб-хука от платежной системы.
    """
    # 1. Определить тестовые данные
    user_id = 123456789
    chat_id = 123456789
    plan_id = 1
    host_name = "test_host"
    tariff_name = "Premium Plan"
    tariff_price = 990
    tariff_days = 30
    
    # 2. Создать metadata — словарь с данными о платеже
    metadata = {
        "user_id": user_id,
        "chat_id": chat_id,
        "plan_id": plan_id,
        "host_name": host_name,
        "tariff_name": tariff_name,
        "tariff_price": tariff_price,
        "tariff_days": tariff_days
    }
    
    # 3. Определить полезную нагрузку, имитирующую успешную оплату
    webhook_payload = {
        "event": "payment.succeeded",
        "object": {
            "id": "pay_1234567890abcdef",
            "status": "succeeded",
            "paid": True,
            "amount": {
                "value": str(tariff_price),
                "currency": "RUB"
            },
            "created_at": "2023-01-01T00:00:00.000Z",
            "description": f"Оплата за тариф {tariff_name}",
            "metadata": metadata,
            "payment_method": {
                "type": "bank_card",
                "id": "pm_1234567890abcdef"
            }
        }
    }
    
    # 5. В качестве эндпоинта используем локальный адрес
    webhook_url = "http://localhost:1488/yookassa-webhook"
    
    # 4. Использовать aiohttp для отправки запроса
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                webhook_url,
                json=webhook_payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                # Проверяем статус ответа
                print(f"Веб-хук отправлен. Статус: {response.status}")
                
                # Читаем тело ответа
                response_text = await response.text()
                print(f"Ответ сервера: {response_text}")
                
                # 6. Выводим сообщение о том, что веб-хук был успешно отправлен
                # и что следует ожидать сообщения от бота
                print("\n" + "="*50)
                print("✅ Веб-хук успешно отправлен!")
                print(f"ℹ️  Пользователь: {user_id}")
                print(f"ℹ️  Чат: {chat_id}")
                print(f"ℹ️  Тариф: {tariff_name} ({tariff_price} руб. на {tariff_days} дней)")
                print(f"ℹ️  Хост: {host_name}")
                print("\n📢 Ожидается, что бот отправит сообщение пользователю с ключом доступа.")
                print("📋 Проверьте, что логика process_successful_payment была выполнена.")
                print("="*50)
                
        except aiohttp.ClientError as e:
            print(f"❌ Ошибка при отправке веб-хука: {e}")
        except Exception as e:
            print(f"❌ Непредвиденная ошибка: {e}")


if __name__ == "__main__":
    # Запускаем асинхронную функцию тестирования
    asyncio.run(test_payment_webhook())