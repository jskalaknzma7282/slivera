# max_websocket_client.py
import asyncio
import json
import logging
import uuid
from typing import Optional, Any, Dict
import websockets
from websockets.asyncio.client import ClientConnection

WS_HOST = "wss://ws-api.oneme.ru/websocket"
RPC_VERSION = 11
APP_VERSION = "26.2.2"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"

_logger = logging.getLogger(__name__)

class MaxClient:
    def __init__(self):
        self._connection: Optional[ClientConnection] = None
        self._seq = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._device_id: Optional[str] = None
        self._is_logged_in = False
        
    async def connect(self):
        """Подключается к WebSocket Max"""
        if self._connection:
            return
            
        _logger.info(f'Connecting to {WS_HOST}...')
        self._connection = await websockets.connect(
            WS_HOST,
            origin=websockets.Origin('https://web.max.ru'),
            user_agent_header=USER_AGENT
        )
        _logger.info('✅ WebSocket connected')
        
        # Запускаем приём сообщений
        asyncio.create_task(self._recv_loop())
        
    async def disconnect(self):
        if self._connection:
            await self._connection.close()
            self._connection = None
            
    async def _send_message(self, opcode: int, payload: Dict) -> int:
        """Отправляет сообщение и возвращает seq"""
        self._seq += 1
        seq = self._seq
        
        request = {
            "ver": RPC_VERSION,
            "cmd": 0,
            "seq": seq,
            "opcode": opcode,
            "payload": payload
        }
        
        # Создаём Future для ожидания ответа
        future = asyncio.get_event_loop().create_future()
        self._pending[seq] = future
        
        await self._connection.send(json.dumps(request))
        
        # Ждём ответ
        response = await future
        return response
    
    async def _recv_loop(self):
        """Цикл приёма сообщений"""
        try:
            async for message in self._connection:
                packet = json.loads(message)
                seq = packet.get("seq")
                
                # Если есть ожидающий Future, разрешаем его
                if seq and seq in self._pending:
                    future = self._pending.pop(seq)
                    future.set_result(packet)
                    
        except websockets.exceptions.ConnectionClosed:
            _logger.warning("Connection closed")
        except Exception as e:
            _logger.error(f"Recv error: {e}")
            
    async def _send_hello(self):
        """Отправляет handshake"""
        self._device_id = str(uuid.uuid4())
        
        payload = {
            "userAgent": {
                "deviceType": "WEB",
                "locale": "ru",
                "deviceLocale": "ru",
                "osVersion": "Linux",
                "deviceName": "Chrome",
                "headerUserAgent": USER_AGENT,
                "appVersion": APP_VERSION,
                "screen": "1080x1920 1.0x",
                "timezone": "Europe/Moscow"
            },
            "deviceId": self._device_id,
        }
        
        return await self._send_message(6, payload)
    
    async def send_code(self, phone: str) -> str:
        """Отправляет номер, возвращает токен для кода"""
        # Сначала отправляем handshake
        await self._send_hello()
        
        # Отправляем номер
        response = await self._send_message(17, {
            "phone": phone,
            "type": "START_AUTH",
            "language": "ru"
        })
        
        # Извлекаем токен
        payload = response.get("payload", {})
        token = payload.get("token")
        
        if not token:
            raise Exception(f"Не удалось получить токен: {response}")
            
        return token
    
    async def verify_code(self, token: str, code: str) -> Dict:
        """Подтверждает код, возвращает данные авторизации"""
        response = await self._send_message(18, {
            "token": token,
            "verifyCode": code,
            "authTokenType": "CHECK_CODE"
        })
        
        payload = response.get("payload", {})
        
        # Проверяем ошибку
        if "error" in payload:
            raise Exception(payload["error"])
            
        return payload
    
    async def login_by_token(self, token: str) -> Dict:
        """Вход по сохранённому токену"""
        await self._send_hello()
        
        response = await self._send_message(19, {
            "interactive": True,
            "token": token,
            "chatsCount": 40,
            "chatsSync": 0,
            "contactsSync": 0,
            "presenceSync": -1,
            "draftsSync": 0
        })
        
        payload = response.get("payload", {})
        
        if "error" in payload:
            raise Exception(payload["error"])
            
        self._is_logged_in = True
        return payload
    
    @property
    def device_id(self) -> Optional[str]:
        return self._device_id
