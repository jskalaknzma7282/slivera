import asyncio
import logging
from pymax import MaxClient
from pymax.payloads import UserAgentPayload

logging.basicConfig(level=logging.INFO)

async def test_qr():
    print("=" * 60)
    print("Тест pymax: пытаемся получить QR-код")
    print("=" * 60)
    
    ua = UserAgentPayload(device_type="WEB", app_version="25.12.13")
    
    # Для QR нужно передать пустую строку или заглушку
    # Некоторые версии требуют номер, но для WEB он не используется
    client = MaxClient(
        phone="",  # пустая строка вместо None
        work_dir="cache_qr_test",
        headers=ua
    )
    
    print("\n1. Создан клиент")
    
    try:
        print("\n2. Запускаем client.start()...")
        await client.start()
        
        print("\n3. Проверяем атрибуты:")
        attrs = [a for a in dir(client) if not a.startswith('_')]
        print(f"   Методы: {', '.join(attrs[:20])}")
        
        # Ищем QR
        qr_attrs = ['qr', 'qr_url', 'qr_link', 'qrcode', 'auth_url', '_qr_url', '_auth_url']
        for attr in qr_attrs:
            if hasattr(client, attr):
                value = getattr(client, attr)
                print(f"   ✅ {attr}: {value}")
        
        # Смотрим __dict__
        print("\n4. Содержимое __dict__:")
        for key, value in client.__dict__.items():
            if 'qr' in key.lower() or 'auth' in key.lower() or 'url' in key.lower():
                print(f"   {key}: {value}")
        
        print("\n" + "=" * 60)
        print("Тест завершён")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_qr())
