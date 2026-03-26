import asyncio
import os
import traceback
import socket
import ssl
import struct
import msgpack
import uuid
import random
from typing import Dict, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class RegStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()

temp_sessions: Dict[int, dict] = {}

class MaxClient:
    def __init__(self):
        self.sock = None
        self.seq = 0
        self.device_id = None
        self.user_agent = None
        self.mt_instance_id = None
        self.client_session_id = None
        self.response_offset = 2
        self._load_device_preset()
        self.auth_token = None
        
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
    
    def _pack_packet(self, ver: int, cmd: int, seq: int, opcode: int, payload: Dict) -> bytes:
        payload_bytes = msgpack.packb(payload)
        
        header = bytearray(10)
        header[0] = ver
        header[1] = (cmd >> 8) & 0xFF
        header[2] = cmd & 0xFF
        header[3] = seq & 0xFF
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
    
    def connect(self):
        print("🔌 Подключение к api.oneme.ru:443...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(('api.oneme.ru', 443))
        
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.sock = context.wrap_socket(sock, server_hostname='api.oneme.ru')
        print("✅ TLS подключён")
        
        # Handshake (opcode 6)
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
        print(f"📥 Ответ handshake: {response}")
        
        if response and response.get('opcode') == 6 and response.get('cmd') == 0x100:
            print("✅ Handshake успешен")
        else:
            raise Exception(f"Handshake failed: {response}")
    
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
        print(f"📥 Ответ на номер: {response}")
        
        if response and response.get('payload'):
            # Проверяем на ошибку
            if 'error' in response['payload']:
                raise Exception(f"Сервер вернул ошибку: {response['payload']['error']}")
            
            # Ищем токен
            token = response['payload'].get('token')
            if token:
                print(f"✅ Получен токен: {token}")
                return token
            
            # Если токена нет, может быть в другом месте
            if 'hash' in response['payload']:
                return response['payload']['hash']
        
        raise Exception(f"Не удалось получить токен: {response}")
    
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
        print(f"📥 Ответ на код: {response}")
        
        if response and response.get('payload'):
            if 'error' in response['payload']:
                raise Exception(f"Ошибка подтверждения: {response['payload']['error']}")
            return response['payload']
        
        raise Exception("Не удалось подтвердить код")
    
    def close(self):
        if self.sock:
            self.sock.close()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "👋 Привет! Я помогу зарегистрироваться в Max.\n\n"
        "Введите номер телефона в формате:\n`+79123456789`",
        parse_mode="Markdown"
    )
    await state.set_state(RegStates.waiting_phone)

@dp.message(RegStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    
    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer("❌ Неверный формат. Пример: `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    try:
        client = MaxClient()
        client.connect()
        token = client.request_code(phone)
        
        temp_sessions[user_id] = {
            "client": client,
            "token": token,
            "phone": phone
        }
        
        await message.answer("✅ Код отправлен! Введите код из SMS:")
        await state.set_state(RegStates.waiting_code)
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

@dp.message(RegStates.waiting_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    if user_id not in temp_sessions:
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    client = session["client"]
    token = session["token"]
    phone = session["phone"]
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        auth_data = client.verify_code(token, code)
        
        # Ищем токен авторизации
        login_token = auth_data.get('tokenAttrs', {}).get('LOGIN', {}).get('token')
        if login_token:
            await message.answer(
                f"✅ **Регистрация успешна!**\n\n"
                f"📱 Номер: `{phone}`\n"
                f"🔑 Токен: `{login_token[:30]}...`\n\n"
                f"⚠️ Сохраните токен для входа.",
                parse_mode="Markdown"
            )
            print(f"✅ Токен: {login_token}")
        else:
            await message.answer("❌ Не удалось получить токен")
        
        client.close()
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")
        client.close()
        del temp_sessions[user_id]
        await state.clear()

async def main():
    print("=" * 50)
    print("🚀 Бот запущен")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
