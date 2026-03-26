import asyncio
import logging
from pymax import MaxClient
from pymax.payloads import UserAgentPayload

logging.basicConfig(level=logging.INFO)

async def test_qr():
    print("=" * 60)
    print("Тест pymax: ищем URL QR-кода")
    print("=" * 60)
    
    ua = UserAgentPayload(device_type="WEB", app_version="25.12.13")
    
    client = MaxClient(
        phone="+79123456789",  # любой валидный номер
        work_dir="cache_qr_test",
        headers=ua
    )
    
    print("\n1. Создан клиент")
    
    try:
        print("\n2. Запускаем client.start()...")
        await client.start()
        
        print("\n3. Проверяем атрибуты client:")
        attrs = [a for a in dir(client) if not a.startswith('_')]
        print(f"   Методы: {', '.join(attrs[:20])}")
        
        print("\n4. Ищем URL QR-кода во всех атрибутах:")
        found = False
        
        # Проверяем все атрибуты, включая приватные
        for key in dir(client):
            if 'qr' in key.lower() or 'url' in key.lower() or 'auth' in key.lower():
                try:
                    value = getattr(client, key)
                    if value and isinstance(value, str) and ('http' in value or 'link' in value):
                        print(f"   ✅ {key}: {value}")
                        found = True
                except:
                    pass
        
        # Смотрим __dict__
        print("\n5. Содержимое client.__dict__:")
        for key, value in client.__dict__.items():
            if 'qr' in key.lower() or 'url' in key.lower() or 'auth' in key.lower():
                print(f"   {key}: {value}")
                if isinstance(value, str) and ('http' in value or 'https' in value):
                    found = True
        
        # Проверяем наличие внутреннего websocket объекта
        if hasattr(client, '_websocket'):
            print("\n6. Проверяем client._websocket:")
            ws = client._websocket
            for key in dir(ws):
                if 'url' in key.lower() or 'qr' in key.lower():
                    try:
                        value = getattr(ws, key)
                        print(f"   {key}: {value}")
                    except:
                        pass
        
        if not found:
            print("\n❌ URL QR-кода не найден в явном виде.")
            print("   Возможно, библиотека генерирует QR только в консоль.")
        
        print("\n" + "=" * 60)
        print("Тест завершён")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_qr())
