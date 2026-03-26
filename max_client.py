import socket
import ssl
import struct
import msgpack
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
        self.response_offset = 2  # Смещение для распаковки ответов (из handshake)
        self._load_device_preset()
        
    def _load_device_preset(self):
        # Точный формат из Dart-кода Komet (успешный вариант)
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
        payload_bytes = msgpack.packb(payload)
        
        header = bytearray(10)
        header[0] = ver
        header[1] = (cmd >> 8) & 0xFF
        header[2] = cmd & 0xFF
        header[3] = seq
        header[4] = (opcode >> 8) & 0xFF
        header[5] = opcode & 0xFF
        struct.pack_into('>I', header, 6, len(payload_bytes))
        
        return bytes(header) + payload_bytes
    
    def _unpack_packet(self, data: bytes) -> Optional[Dict]:
        """Разбирает пакет, пропуская первые response_offset байт"""
        if len(data) < 10:
            return None
        
        ver = data[0]
        cmd = (data[1] << 8) | data[2]
        seq = data[3]
        opcode = (data[4] << 8) | data[5]
        payload_len = (data[6] << 24) | (data[7] << 16) | (data[8] << 8) | data[9]
        
        if len(data) < 10 + payload_len:
            return None
        
        payload_bytes = data[10:10 + payload_len]
        
        # Пропускаем response_offset байт при распаковке
        try:
            payload = msgpack.unpackb(payload_bytes[self.response_offset:], raw=False)
        except:
            # Если не получилось, пробуем без смещения
            try:
                payload = msgpack.unpackb(payload_bytes, raw=False)
            except:
                return None
        
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
        
        # Отправляем handshake (opcode 6)
        handshake_payload = {
            "mt_instanceid": self.mt_instance_id,
            "clientSessionId": self.client_session_id,
            "deviceId": self.device_id,
            "userAgent": self.user_agent
        }
        
        print(f"📤 Отправка handshake (opcode=6)")
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(10, 0, self.seq, 6, handshake_payload)
        self.sock.send(packet)
        
        # Ждём ответ
        print("📥 Ожидание ответа...")
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
        
        payload_len = (header[6] << 24) | (header[7] << 16) | (header[8] << 8) | header[9]
        
        if payload_len > 0:
            payload_data = self._recv_exact(payload_len)
            if not payload_data:
                return None
            full = header + payload_data
        else:
            full = header
        
        return self._unpack_packet(full)
    
    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Читает ровно n байт"""
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def request_code(self, phone: str) -> str:
        """Запрашивает SMS-код (opcode 17)"""
        print(f"\n📱 Запрос кода для {phone}")
        payload = {
            "phone": phone,
            "type": "START_AUTH",
            "language": "ru"
        }
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(10, 0, self.seq, 17, payload)
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
        """Подтверждает код (opcode 18)"""
        print(f"\n🔐 Подтверждение кода: {code}")
        payload = {
            "token": token,
            "verifyCode": code,
            "authTokenType": "CHECK_CODE"
        }
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(10, 0, self.seq, 18, payload)
        self.sock.send(packet)
        
        response = self._recv_packet()
        print(f"📥 Ответ: {response}")
        
        if response and response.get('payload'):
            return response['payload']
        raise Exception("Не удалось подтвердить код")
    
    def register(self, reg_token: str, first_name: str = "User", last_name: str = "Komet") -> str:
        """Завершает регистрацию (opcode 23)"""
        print(f"\n📝 Завершение регистрации")
        payload = {
            "lastName": last_name,
            "token": reg_token,
            "firstName": first_name,
            "tokenType": "REGISTER"
        }
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(10, 0, self.seq, 23, payload)
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
