import socket
import ssl
import struct
import msgpack
import lz4.block
import os
import json
import random
import uuid
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
        # Используем пресет Android из Komet
        self.user_agent = {
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
        self.device_id = str(uuid.uuid4())
        self.mt_instance_id = str(uuid.uuid4())
        self.client_session_id = random.randint(1, 100)
        
    def _pack_packet(self, ver: int, cmd: int, seq: int, opcode: int, payload: Dict) -> bytes:
        # Сериализуем payload в MessagePack
        payload_bytes = msgpack.packb(payload)
        
        # Сжатие LZ4 для больших пакетов
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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(('api.oneme.ru', 443))
        
        # TLS
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.sock = context.wrap_socket(sock, server_hostname='api.oneme.ru')
        
        # Отправляем handshake (opcode 6)
        handshake_payload = {
            "mt_instanceid": self.mt_instance_id,
            "clientSessionId": self.client_session_id,
            "deviceId": self.device_id,
            "userAgent": self.user_agent
        }
        
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(10, 0, self.seq, 6, handshake_payload)
        self.sock.send(packet)
        
        # Ждём ответ
        response = self._recv_packet()
        if response and response.get('opcode') == 6 and response.get('cmd') == 0x100:
            print("Handshake успешен")
        else:
            raise Exception("Handshake failed")
    
    def _recv_packet(self) -> Optional[Dict]:
        """Получает пакет от сервера"""
        # Сначала читаем заголовок 10 байт
        header = self._recv_exact(10)
        if not header:
            return None
        
        # Получаем длину payload
        packed_len = (header[6] << 24) | (header[7] << 16) | (header[8] << 8) | header[9]
        payload_len = packed_len & 0x00FFFFFF
        
        # Читаем payload
        payload_data = self._recv_exact(payload_len) if payload_len > 0 else b''
        if payload_len > 0 and not payload_data:
            return None
        
        full_packet = header + (payload_data or b'')
        return self._unpack_packet(full_packet)
    
    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Читает ровно n байт"""
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def send_message(self, opcode: int, payload: Dict) -> Optional[Dict]:
        """Отправляет сообщение и ждёт ответ"""
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(10, 0, self.seq, opcode, payload)
        self.sock.send(packet)
        
        # Ждём ответ
        response = self._recv_packet()
        while response and response.get('opcode') != opcode:
            response = self._recv_packet()
        return response
    
    def request_code(self, phone: str) -> str:
        """Запрашивает SMS-код"""
        payload = {
            "phone": phone,
            "type": "START_AUTH",
            "language": "ru"
        }
        response = self.send_message(17, payload)
        if response and response.get('payload'):
            return response['payload'].get('token')
        raise Exception("Не удалось получить токен для кода")
    
    def verify_code(self, token: str, code: str) -> Dict:
        """Подтверждает код"""
        payload = {
            "token": token,
            "verifyCode": code,
            "authTokenType": "CHECK_CODE"
        }
        response = self.send_message(18, payload)
        if response and response.get('payload'):
            return response['payload']
        raise Exception("Не удалось подтвердить код")
    
    def register(self, reg_token: str, first_name: str = "User", last_name: str = "Komet") -> str:
        """Завершает регистрацию"""
        payload = {
            "lastName": last_name,
            "token": reg_token,
            "firstName": first_name,
            "tokenType": "REGISTER"
        }
        response = self.send_message(23, payload)
        if response and response.get('payload'):
            token_attrs = response['payload'].get('tokenAttrs', {})
            login_attrs = token_attrs.get('LOGIN', {})
            return login_attrs.get('token')
        raise Exception("Не удалось завершить регистрацию")
    
    def close(self):
        if self.sock:
            self.sock.close()
