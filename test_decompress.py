import socket
import ssl
import struct
import msgpack
import uuid
import random
import lz4.block

def brute_force_decompress(payload):
    """Перебирает смещения и пытается распаковать LZ4"""
    for offset in range(0, min(20, len(payload))):
        try:
            decompressed = lz4.block.decompress(payload[offset:])
            print(f"✅ Смещение {offset}: распаковано {len(decompressed)} байт")
            try:
                data = msgpack.unpackb(decompressed, raw=False)
                print(f"📦 Распаковано: {data}")
                return data
            except:
                print(f"   Не удалось распаковать MessagePack")
        except Exception as e:
            print(f"❌ Смещение {offset}: {e}")
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
    header[0] = 10  # ver
    header[1] = 0   # cmd high
    header[2] = 0   # cmd low
    header[3] = 1   # seq
    header[4] = 0   # opcode high
    header[5] = 6   # opcode low
    struct.pack_into('>I', header, 6, len(payload_bytes))
    packet = bytes(header) + payload_bytes
    
    print(f"📤 Отправка handshake (opcode=6), размер={len(packet)}")
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
        
        # Перебираем смещения
        result = brute_force_decompress(payload)
        if result:
            print("\n✅ Успешно распаковано!")
        else:
            print("\n❌ Не удалось распаковать ни с одним смещением")
    
    ssl_sock.close()

if __name__ == "__main__":
    test_handshake()
