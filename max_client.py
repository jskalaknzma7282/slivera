import socket
import ssl
import struct
import msgpack
import lz4.block
import uuid
import random
from typing import Optional, Dict, Any

class MaxClient:
    def __init__(self):
        self.sock = None
        self.seq = 0
        self.device_id = None
        self.user_agent = None
        self.mt_instance_id = None
        self.client_session_id = None
        self._load_device_preset()
        
    def _load_device_preset(self):
        # Точный формат из Dart-кода Komet
        self.user_agent = {
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
        self.device_id = str(uuid.uuid4())
        self.mt_instance_id = str(uuid.uuid4())
        self.client_session_id = random.randint(1, 100)
        
    def _pack_packet(self, ver: int, cmd: int, seq: int, opcode: int, payload: Dict) -> bytes:
        """Формирует пакет с поддержкой сжатия"""
        payload_bytes = msgpack.packb(payload)
        
        is_compressed = False
        if len(payload_bytes) >= 32:
            compressed = lz4.block.compress(payload_bytes)
            # Формат: 4 байта исходный размер (big-endian) + сжатые данные
            uncompressed_size = struct.pack('>I', len(payload_bytes))
            payload_bytes = uncompressed_size + compressed
            is_compressed = True
        
        # Заголовок 10 байт
        header = bytearray(10)
        header[0] = ver
        header[1] = (cmd >> 8) & 0xFF
        header[2] = cmd & 0xFF
        header[3] = seq & 0xFF
        header[4] = (opcode >> 8) & 0xFF
        header[5] = opcode & 0xFF
        
        # Длина с флагом сжатия в старшем байте
        packed_len = len(payload_bytes)
        if is_compressed:
            packed_len |= (1 << 24)
        struct.pack_into('>I', header, 6, packed_len)
        
        return bytes(header) + payload_bytes
    
    def _unpack_packet(self, data: bytes) -> Optional[Dict]:
        """Разбирает пакет по формату из Komet"""
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
            # Распаковываем: первые 4 байта — исходный размер
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
    
    def connect(self):
        """Подключается к серверу Max"""
        print("🔌 Подключение к api.oneme.ru:443...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(('api.oneme.ru', 443))
        
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.sock = context.wrap_socket(sock, server_hostname='api.oneme.ru')
        print("✅ TLS подключён")
        
        # 1. Отправляем ping (opcode 1)
        ping_payload = {"interactive": True}
        self.seq = (self.seq + 1) % 256
        ping_packet = self._pack_packet(11, 0, self.seq, 1, ping_payload)
        self.sock.send(ping_packet)
        print("📤 Отправлен ping (opcode=1)")
        
        # Ждём ответ на ping
        ping_response = self._recv_packet()
        print(f"📥 Ответ на ping: {ping_response}")
        
        # 2. Отправляем handshake (opcode 6)
        handshake_payload = {
            "mt_instanceid": self.mt_instance_id,
            "clientSessionId": self.client_session_id,
            "deviceId": self.device_id,
            "userAgent": self.user_agent
        }
        
        print(f"📤 Handshake payload: {handshake_payload}")
        
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(11, 0, self.seq, 6, handshake_payload)
        print(f"📤 Пакет handshake: {len(packet)} байт")
        self.sock.send(packet)
        
        # Ждём ответ на handshake
        print("📥 Ожидание ответа на handshake...")
        response = self._recv_packet()
        print(f"📥 Ответ: {response}")
        
        if response and response.get('opcode') == 6 and response.get('cmd') == 0x100:
            print("✅ Handshake успешен")
        else:
            raise Exception(f"Handshake failed: {response}")
    
    def _recv_packet(self) -> Optional[Dict]:
        """Получает пакет от сервера"""
        # Заголовок 10 байт
        header = self._recv_exact(10)
        if not header:
            return None
        
        packed_len = (header[6] << 24) | (header[7] << 16) | (header[8] << 8) | header[9]
        payload_len = packed_len & 0x00FFFFFF
        
        if payload_len > 0:
            payload_data = self._recv_exact(payload_len)
            if not payload_data:
                return None
            full = header + payload_data
        else:
            full = header
        
        return self._unpack_packet(full)
    
    def _recv_exact(self, n: int, timeout: float = 10) -> Optional[bytes]:
        """Читает ровно n байт с таймаутом"""
        self.sock.settimeout(timeout)
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def request_code(self, phone: str) -> str:
        """Запрашивает SMS-код"""
        print(f"\n📱 Запрос кода для {phone}")
        payload = {
            "phone": phone,
            "type": "START_AUTH",
            "language": "ru"
        }
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(11, 0, self.seq, 17, payload)
        self.sock.send(packet)
        
        response = self._recv_packet()
        print(f"📥 Ответ: {response}")
        
        if response and response.get('payload'):
            token = response['payload'].get('token')
            if token:
                print(f"✅ Получен токен: {token}")
                return token
        raise Exception("Не удалось получить токен для кода")
    
    def verify_code(self, token: str, code: str) -> Dict:
        """Подтверждает код"""
        print(f"\n🔐 Подтверждение кода: {code}")
        payload = {
            "token": token,
            "verifyCode": code,
            "authTokenType": "CHECK_CODE"
        }
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(11, 0, self.seq, 18, payload)
        self.sock.send(packet)
        
        response = self._recv_packet()
        print(f"📥 Ответ: {response}")
        
        if response and response.get('payload'):
            return response['payload']
        raise Exception("Не удалось подтвердить код")
    
    def register(self, reg_token: str, first_name: str = "User", last_name: str = "Komet") -> str:
        """Завершает регистрацию"""
        print(f"\n📝 Завершение регистрации")
        payload = {
            "lastName": last_name,
            "token": reg_token,
            "firstName": first_name,
            "tokenType": "REGISTER"
        }
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(11, 0, self.seq, 23, payload)
        self.sock.send(packet)
        
        response = self._recv_packet()
        print(f"📥 Ответ: {response}")
        
        if response and response.get('payload'):
            token_attrs = response['payload'].get('tokenAttrs', {})
            login_attrs = token_attrs.get('LOGIN', {})
            token = login_attrs.get('token')
            if token:
                print(f"✅ Получен финальный токен: {token[:30]}...")
                return token
        raise Exception("Не удалось завершить регистрацию")
    
    def close(self):
        if self.sock:
            self.sock.close()
