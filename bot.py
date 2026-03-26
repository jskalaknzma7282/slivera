import asyncio
import logging
from pymax import MaxClient
from pymax.payloads import UserAgentPayload

# Включаем логирование, чтобы видеть, что происходит
logging.basicConfig(level=logging.INFO)

async def test_qr():
    print("=" * 60)
    print("Тест pymax: пытаемся получить QR-код")
    print("=" * 60)
    
    # Создаём клиент для WEB (QR-код)
    ua = UserAgentPayload(
        device_type="WEB",
        app_version="25.12.13"
    )
    
    client = MaxClient(
        phone=None,  # для QR номер не нужен
        work_dir="cache_qr_test",
        headers=ua
    )
    
    print("\n1. Создан клиент")
    print(f"   work_dir: cache_qr_test")
    print(f"   device_type: WEB")
    
    try:
        print("\n2. Запускаем client.start()...")
        # Запускаем клиент (он должен начать процесс авторизации)
        await client.start()
        
        print("\n3. Проверяем атрибуты клиента:")
        # Смотрим, что есть в клиенте
        attrs = [a for a in dir(client) if not a.startswith('_')]
        print(f"   Методы и атрибуты: {', '.join(attrs[:15])}...")
        
        # Проверяем, есть ли qr_url или что-то похожее
        qr_attrs = ['qr', 'qr_url', 'qr_link', 'qrcode', 'qr_code', 'auth_url']
        found = False
        for attr in qr_attrs:
            if hasattr(client, attr):
                value = getattr(client, attr)
                print(f"   ✅ Найден атрибут {attr}: {value}")
                found = True
        
        if not found:
            print("   ❌ Не найдено атрибутов с QR")
            
            # Проверяем внутренние переменные
            if hasattr(client, '_qr_url'):
                print(f"   🔍 Найден _qr_url: {client._qr_url}")
                found = True
            if hasattr(client, '_auth_url'):
                print(f"   🔍 Найден _auth_url: {client._auth_url}")
                found = True
        
        # Пробуем вызвать метод get_qr, если есть
        if hasattr(client, 'get_qr'):
            print("\n4. Вызываем client.get_qr()...")
            result = await client.get_qr()
            print(f"   Результат: {result}")
        
        # Смотрим, что внутри client.__dict__
        print("\n5. Содержимое client.__dict__:")
        for key, value in client.__dict__.items():
            if 'qr' in key.lower() or 'auth' in key.lower() or 'url' in key.lower():
                print(f"   {key}: {value}")
        
        print("\n" + "=" * 60)
        print("Тест завершён")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_qr())
