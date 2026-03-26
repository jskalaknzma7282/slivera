import socket
import ssl
import struct
import msgpack
import lz4.block
import uuid
import random

def pack_packet(ver, cmd, seq, opcode, payload):
    payload_bytes = msgpack.packb(payload)
    
    is_compressed = False
    if len(payload_bytes) >= 32:
        compressed = lz4.block.compress(payload_bytes)
        uncompressed_size = struct.pack('>I', len(payload_bytes))
        payload_bytes = uncompressed_size + compressed
        is_compressed = True
    
    header = bytearray(10)
    header[0] = ver
    header[1] = (cmd >> 8) & 0xFF
    header[2] = cmd & 0xFF
    header[3] = seq & 0xFF
    header[4] = (opcode >> 8) & 0xFF
    header[5] = opcode & 0xFF
    
    packed_len = len(payload_bytes)
    if is_compressed:
        packed_len |= (1 << 24)
    struct.pack_into('>I', header, 6, packed_len)
    
    return bytes(header) + payload_bytes

def recv_exact(sock, n):
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data

def unpack_packet(data):
    if len(data) < 10:
        return None
    ver = data[0]
    cmd = (data[1] << 8) | data[2]
    seq = data[3]
    opcode = (data[4] << 8) | data[5]
    packed_len = (data[6] << 24) | (data[7] << 16) | (data[8] << 8) | data[9]
    
    is_compressed = (packed_len >> 24) != 0
    payload_len = packed_len & 0x00FFFFFF
    
    if len(data) < 10 + payload_len:
        return None
    
    payload_bytes = data[10:10 + payload_len]
    
    if is_compressed:
        uncompressed_size = struct.unpack('>I', payload_bytes[:4])[0]
        compressed_data = payload_bytes[4:]
        payload_bytes = lz4.block.decompress(compressed_data, uncompressed_size=uncompressed_size)
    
    payload = msgpack.unpackb(payload_bytes, raw=False)
    return {
        "ver": ver,
        "cmd": cmd,
        "seq": seq,
        "opcode": opcode,
        "payload": payload
    }

print("1. Подключение...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(30)
sock.connect(('api.oneme.ru', 443))

context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
ssl_sock = context.wrap_socket(sock, server_hostname='api.oneme.ru')
print("✅ TLS подключён")

# Генерируем данные
device_id = str(uuid.uuid4())
mt_instance_id = str(uuid.uuid4())
client_session_id = random.randint(1, 100)

user_agent = {
    "deviceType": "ANDROID",
    "locale": "ru-RU",
    "deviceLocale": "ru-RU",
    "osVersion": "Android 14",
    "deviceName": "Samsung Galaxy S24 Ultra",
    "headerUserAgent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "appVersion": "25.21.3",
    "screen": "1440x3120 3.0x",
    "timezone": "Europe/Moscow"
}

handshake_payload = {
    "mt_instanceid": mt_instance_id,
    "clientSessionId": client_session_id,
    "deviceId": device_id,
    "userAgent": user_agent
}

print(f"\n📤 Handshake payload: {handshake_payload}")

seq = 1
packet = pack_packet(10, 0, seq, 6, handshake_payload)
print(f"📤 Пакет: {len(packet)} байт")
ssl_sock.send(packet)

print("\n📥 Ожидание ответа...")
header = recv_exact(ssl_sock, 10)
if header:
    print(f"📥 Заголовок: {header.hex()}")
    packed_len = (header[6] << 24) | (header[7] << 16) | (header[8] << 8) | header[9]
    payload_len = packed_len & 0x00FFFFFF
    if payload_len > 0:
        payload_data = recv_exact(ssl_sock, payload_len)
        if payload_data:
            full = header + payload_data
            response = unpack_packet(full)
            print(f"📥 Ответ: {response}")
        else:
            print("❌ Не удалось прочитать payload")
    else:
        print("📥 Пустой payload")
else:
    print("❌ Не удалось прочитать заголовок")

ssl_sock.close()
