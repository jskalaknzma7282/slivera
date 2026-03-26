import socket
import ssl
import struct
import msgpack
import uuid
import random
import lz4.block
from typing import Dict, Optional

class MaxClient:
    def __init__(self, user_agent_variant: int = 0):
        self.sock = None
        self.seq = 0
        self.device_id = str(uuid.uuid4())
        self.mt_instance_id = str(uuid.uuid4())
        self.client_session_id = random.randint(1, 100)
        self.user_agent_variant = user_agent_variant
        self._load_user_agent()
        
    def _load_user_agent(self):
        # Базовые поля
        base_ua = {
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
        
        # Разные порядки полей
        orders = [
            list(base_ua.keys()),  # оригинальный порядок
            ["deviceType", "locale", "deviceLocale", "osVersion", "deviceName", 
             "appVersion", "screen", "timezone", "pushDeviceType", "arch", "buildNumber"],
            ["deviceType", "osVersion", "deviceName", "appVersion", "locale", 
             "deviceLocale", "screen", "timezone", "pushDeviceType", "arch", "buildNumber"],
            ["deviceType", "deviceName", "osVersion", "appVersion", "screen", 
             "timezone", "locale", "deviceLocale", "pushDeviceType", "arch", "buildNumber"],
        ]
        
        # Добавляем/убираем поля
        variants = []
        
        # Вариант 0: все поля
        variants.append(base_ua.copy())
        
        # Вариант 1: без pushDeviceType
        ua1 = base_ua.copy()
        del ua1["pushDeviceType"]
        variants.append(ua1)
        
        # Вариант 2: без arch
        ua2 = base_ua.copy()
        del ua2["arch"]
        variants.append(ua2)
        
        # Вариант 3: без buildNumber
        ua3 = base_ua.copy()
        del ua3["buildNumber"]
        variants.append(ua3)
        
        # Вариант 4: все поля + clientVersion
        ua4 = base_ua.copy()
        ua4["clientVersion"] = "25.21.3"
        variants.append(ua4)
        
        # Выбираем по индексу
        if self.user_agent_variant < len(variants):
            self.user_agent = variants[self.user_agent_variant]
        else:
            self.user_agent = base_ua.copy()
            
        # Применяем порядок
        if self.user_agent_variant < len(orders):
            ordered = {}
            for key in orders[self.user_agent_variant]:
                if key in self.user_agent:
                    ordered[key] = self.user_agent[key]
            self.user_agent = ordered
            
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
    
    def connect(self, ver: int = 10, compress: bool = False):
        print(f"\n🔌 Тест {self.user_agent_variant}: ver={ver}, compress={compress}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect(('api.oneme.ru', 443))
        except Exception as e:
            print(f"❌ TCP ошибка: {e}")
            return False
        
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            self.sock = context.wrap_socket(sock, server_hostname='api.oneme.ru')
        except Exception as e:
            print(f"❌ TLS ошибка: {e}")
            return False
        
        handshake_payload = {
            "mt_instanceid": self.mt_instance_id,
            "clientSessionId": self.client_session_id,
            "deviceId": self.device_id,
            "userAgent": self.user_agent
        }
        
        self.seq = (self.seq + 1) % 256
        packet = self._pack_packet(ver, 0, self.seq, 6, handshake_payload)
        
        try:
            self.sock.send(packet)
        except Exception as e:
            print(f"❌ Ошибка отправки: {e}")
            self.sock.close()
            return False
        
        try:
            header = self.sock.recv(10)
            if not header:
                print("❌ Нет заголовка")
                self.sock.close()
                return False
            
            payload_len = (header[6] << 24) | (header[7] << 16) | (header[8] << 8) | header[9]
            print(f"📥 Заголовок: {header.hex()}, payload_len={payload_len}")
            
            if payload_len > 0:
                payload = self.sock.recv(payload_len)
                print(f"📥 Payload (hex первые 200): {payload.hex()[:200]}...")
                
                # Пробуем распаковать LZ4
                try:
                    # Способ 1: прямой LZ4
                    try:
                        decompressed = lz4.block.decompress(payload)
                        print(f"📥 LZ4 распаковано (прямое), размер: {len(decompressed)}")
                        data = msgpack.unpackb(decompressed, raw=False)
                        print(f"📥 Распаковано: {data}")
                    except:
                        # Способ 2: с 4-байтовым заголовком (как в нашем pack)
                        try:
                            uncompressed_size = struct.unpack('>I', payload[:4])[0]
                            compressed = payload[4:]
                            decompressed = lz4.block.decompress(compressed, uncompressed_size=uncompressed_size)
                            print(f"📥 LZ4 (с заголовком) распаковано, размер: {len(decompressed)}")
                            data = msgpack.unpackb(decompressed, raw=False)
                            print(f"📥 Распаковано: {data}")
                        except Exception as e:
                            print(f"❌ LZ4 распаковка не удалась: {e}")
                except Exception as e:
                    print(f"❌ Ошибка LZ4: {e}")
            
            self.sock.close()
            return True
        except Exception as e:
            print(f"❌ Ошибка приёма: {e}")
            self.sock.close()
            return False

def test_all_variants():
    # Пробуем разные варианты
    for variant in range(5):  # 5 вариантов user_agent
        for ver in [10, 11, 12]:
            client = MaxClient(user_agent_variant=variant)
            success = client.connect(ver=ver, compress=False)
            if success:
                print(f"\n✅ НАЙДЕНО! Вариант: variant={variant}, ver={ver}")
                return
        print(f"Вариант {variant} не сработал")

if __name__ == "__main__":
    test_all_variants()
