import socket
import ssl
import struct
import msgpack
import uuid
import random

def try_unpack(data, offset):
    """Пробует распаковать MessagePack с указанным смещением"""
    try:
        result = msgpack.unpackb(data[offset:], raw=False)
        print(f"✅ Смещение {offset}: успешно!")
        print(f"   Распаковано: {result}")
        return result
    except Exception as e:
        return None

def test_handshake():
    print("🔌 Подключение к api.oneme.ru:443...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(('api.oneme.ru', 443))
    
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    ssl_sock = context.wrap_socket(sock, server_hostname='api.oneme.ru')
    print("✅ TLS подключён")
    
    # Формируем handshake
    device_id = str(uuid.uuid4())
    mt_instance_id = str(uuid.uuid4())
    client_session_id = random.randint(1, 100)
    
    user_agent = {
        "deviceType": "ANDROID",
        "locale": "ru",
        "deviceLocale": "ru",
        "osVersion": "Android 14",
        "deviceName": "Samsung Galaxy S23",
        "appVersion": "25.21.3",
        "screen": "xxhdpi 480dpi 1080x2340",
        "timezone": "Europe/Moscow",
        "pushDeviceType": "GCM",
        "arch": "arm64-v8a",
        "buildNumber": 6498
    }
    
    handshake_payload = {
        "mt_instanceid": mt_instance_id,
        "clientSessionId": client_session_id,
        "deviceId": device_id,
        "userAgent": user_agent
    }
    
    # Упаковываем
    payload_bytes = msgpack.packb(handshake_payload)
    header = bytearray(10)
    header[0] = 10
    header[1] = 0
    header[2] = 0
    header[3] = 1
    header[4] = 0
    header[5] = 6
    struct.pack_into('>I', header, 6, len(payload_bytes))
    packet = bytes(header) + payload_bytes
    
    print(f"📤 Отправка handshake, размер={len(packet)}")
    ssl_sock.send(packet)
    
    # Получаем ответ
    header = ssl_sock.recv(10)
    if not header:
        print("❌ Нет заголовка")
        return
    
    payload_len = (header[6] << 24) | (header[7] << 16) | (header[8] << 8) | header[9]
    print(f"📥 Заголовок: {header.hex()}, payload_len={payload_len}")
    
    if payload_len > 0:
        payload = ssl_sock.recv(payload_len)
        print(f"📥 Получено {len(payload)} байт")
        print(f"📥 Hex (первые 100): {payload.hex()[:100]}")
        
        # Перебираем смещения от 0 до 20
        print("\n🔍 Перебор смещений для MessagePack...")
        found = False
        for offset in range(0, min(20, len(payload))):
            result = try_unpack(payload, offset)
            if result:
                found = True
                print(f"\n✅ Найдено рабочее смещение: {offset}")
                break
        
        if not found:
            print("\n❌ Не найдено смещение, которое даёт валидный MessagePack")
    
    ssl_sock.close()

if __name__ == "__main__":
    test_handshake()
