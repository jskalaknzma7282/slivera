import socket
import ssl
import struct
import msgpack
import uuid
import random
import time
from typing import Optional, Dict, Any, List, Tuple

class MaxClient:
    def __init__(self, proxy_list: List[Tuple[str, int]] = None):
        self.sock = None
        self.seq = 0
        self.device_id = None
        self.user_agent = None
        self.mt_instance_id = None
        self.client_session_id = None
        self.response_offset = 2
        self.proxy_host = None
        self.proxy_port = None
        
        if proxy_list:
            self._select_fastest_proxy(proxy_list)
        
        self._load_device_preset()
    
    def _select_fastest_proxy(self, proxy_list: List[Tuple[str, int]]):
        """Тестирует прокси и выбирает самый быстрый"""
        print(f"🔍 Тестируем {len(proxy_list)} прокси...")
        fastest_time = None
        fastest_proxy = None
        
        for ip, port in proxy_list:
            try:
                start = time.time()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect((ip, port))
                sock.close()
                elapsed = time.time() - start
                
                print(f"   ✅ {ip}:{port} - {elapsed:.2f} сек")
                
                if fastest_time is None or elapsed < fastest_time:
                    fastest_time = elapsed
                    fastest_proxy = (ip, port)
            except:
                print(f"   ❌ {ip}:{port} - не отвечает")
        
        if fastest_proxy:
            self.proxy_host, self.proxy_port = fastest_proxy
            print(f"\n🏆 Выбран прокси: {self.proxy_host}:{self.proxy_port} (время {fastest_time:.2f} сек)")
        else:
            print("❌ Не найден рабочий прокси!")
            raise Exception("Нет рабочих прокси")
    
    def _load_device_preset(self):
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
    
    def _connect_via_http_proxy(self, target_host: str, target_port: int):
        """Подключается через HTTP CONNECT прокси"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect((self.proxy_host, self.proxy_port))
        
        # Отправляем CONNECT запрос
        connect_cmd = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}:{target_port}\r\n\r\n"
        sock.send(connect_cmd.encode())
        
        # Читаем ответ
        response = sock.recv(1024)
        if b"200" not in response:
            raise Exception(f"Прокси отказал: {response}")
        
        return sock
    
    def connect(self, retries=3):
        for attempt in range(retries):
            try:
                print(f"🔌 Попытка {attempt+1}/{retries}...")
                
                # Подключаемся через прокси или напрямую
                if self.proxy_host and self.proxy_port:
                    raw_sock = self._connect_via_http_proxy('api.oneme.ru', 443)
                else:
                    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    raw_sock.settimeout(15)
                    raw_sock.connect(('api.oneme.ru', 443))
                
                # TLS обёртка
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self.sock = context.wrap_socket(raw_sock, server_hostname='api.oneme.ru')
                print("✅ TLS подключён")
                
                # Handshake
                handshake_payload = {
                    "mt_instanceid": self.mt_instance_id,
                    "clientSessionId": self.client_session_id,
                    "deviceId": self.device_id,
                    "userAgent": self.user_agent
                }
                
                self.seq = (self.seq + 1) % 256
                packet = self._pack_packet(10, 0, self.seq, 6, handshake_payload)
                self.sock.send(packet)
                print("📤 Handshake отправлен")
                
                # Ждём ответ
                response = self._recv_packet()
                if response:
                    print(f"📥 Ответ: {response}")
                    if response.get('opcode') == 6 and response.get('cmd') == 0x100:
                        print("✅ Handshake успешен")
                        return
                    else:
                        raise Exception(f"Неверный ответ: {response}")
                else:
                    raise Exception("Нет ответа")
                    
            except Exception as e:
                print(f"❌ Ошибка: {e}")
                if self.sock:
                    self.sock.close()
                if attempt == retries - 1:
                    raise
                print("⏳ Повтор через 2 секунды...")
                time.sleep(2)
    
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
        
        try:
            payload = msgpack.unpackb(payload_bytes[self.response_offset:], raw=False)
        except:
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
    
    def _recv_packet(self) -> Optional[Dict]:
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
        self.sock.settimeout(10)
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def request_code(self, phone: str) -> str:
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
