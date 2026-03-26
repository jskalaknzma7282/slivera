# max_websocket_client.py
import asyncio
import websockets
import json
import uuid
import random
import qrcode
import io
from typing import Optional, Dict

class MaxWebSocketClient:
    def __init__(self):
        self.websocket = None
        self.device_id = None
        self.mt_instance_id = None
        self.client_session_id = None
        self.user_agent = None
        self.seq = 0
        self._load_device_preset()
    
    def _load_device_preset(self):
        self.user_agent = {
            "deviceType": "WEB",
            "locale": "ru",
            "deviceLocale": "ru",
            "osVersion": "Windows 11",
            "deviceName": "Chrome",
            "appVersion": "25.12.13",
            "screen": "1920x1080",
            "timezone": "Europe/Moscow"
        }
        self.device_id = str(uuid.uuid4())
        self.mt_instance_id = str(uuid.uuid4())
        self.client_session_id = random.randint(1, 100)
    
    async def connect(self):
        """Подключается к WebSocket Max"""
        uri = "wss://ws-api.oneme.ru/websocket"
        print(f"🔌 Подключение к {uri}...")
        self.websocket = await websockets.connect(uri)
        print("✅ WebSocket подключён")
        
        # Отправляем handshake (opcode 6)
        handshake_payload = {
            "mt_instanceid": self.mt_instance_id,
            "clientSessionId": self.client_session_id,
            "deviceId": self.device_id,
            "userAgent": self.user_agent
        }
        
        await self._send_message(6, handshake_payload)
        print("📤 Handshake отправлен")
        
        # Ждём ответ
        response = await self._recv_message()
        print(f"📥 Ответ: {response}")
        
        if response and response.get('opcode') == 6 and response.get('cmd') == 0x100:
            print("✅ Handshake успешен")
        else:
            raise Exception("Handshake failed")
    
    async def get_qr(self) -> str:
        """Запрашивает QR-код и возвращает URL"""
        print("📱 Запрос QR-кода...")
        
        # Отправляем запрос QR (opcode 7 или другой — нужно уточнить)
        # В pymax это было через _request_qr_login
        payload = {}
        await self._send_message(7, payload)  # предположительно opcode 7 для QR
        
        response = await self._recv_message()
        print(f"📥 Ответ QR: {response}")
        
        if response and response.get('payload'):
            qr_link = response['payload'].get('qrLink')
            if qr_link:
                return qr_link
        
        raise Exception("Не удалось получить QR-код")
    
    async def wait_for_login(self, track_id: str) -> bool:
        """Ожидает сканирования QR-кода"""
        print("⏳ Ожидание сканирования QR...")
        
        while True:
            payload = {"trackId": track_id}
            await self._send_message(8, payload)  # опрос статуса
            response = await self._recv_message()
            
            if response and response.get('payload'):
                status = response['payload'].get('status')
                if status and status.get('loginAvailable'):
                    print("✅ QR отсканирован!")
                    return True
                elif status and status.get('expired'):
                    print("❌ QR истёк")
                    return False
            
            await asyncio.sleep(2)
    
    async def _send_message(self, opcode: int, payload: Dict):
        self.seq += 1
        message = {
            "ver": 10,
            "cmd": 0,
            "seq": self.seq,
            "opcode": opcode,
            "payload": payload
        }
        await self.websocket.send(json.dumps(message))
    
    async def _recv_message(self) -> Optional[Dict]:
        response = await self.websocket.recv()
        return json.loads(response)
    
    async def close(self):
        if self.websocket:
            await self.websocket.close()

def generate_qr_image(url: str) -> bytes:
    """Генерирует PNG из URL"""
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
