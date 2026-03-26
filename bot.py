import asyncio
import os
import json
import traceback
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

import websockets
import ssl

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ==========
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден в .env файле")

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ========== СОСТОЯНИЯ ==========
class RegStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()

# ========== ВРЕМЕННОЕ ХРАНИЛИЩЕ ==========
temp_sessions: Dict[int, dict] = {}

# ========== КЛАСС ДЛЯ РАБОТЫ С MAX ==========
class MaxAuth:
    """Простой клиент для авторизации в Max через WebSocket"""
    
    def __init__(self):
        self.websocket = None
        self.device_id = None
    
    async def connect(self):
        """Подключается к WebSocket Max"""
        uri = "wss://ws-api.oneme.ru/websocket"
        
        # Заголовки как в браузере/приложении
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            "Origin": "https://web.max.ru"
        }
        
        self.websocket = await websockets.connect(
            uri, 
            extra_headers=headers,
            ssl=ssl.create_default_context()
        )
        print("[MAX] WebSocket подключён")
        return self
    
    async def send_code(self, phone: str) -> dict:
        """Отправляет номер, возвращает hash"""
        request = {
            "type": "auth",
            "payload": {"phone": phone}
        }
        
        await self.websocket.send(json.dumps(request))
        response = await self.websocket.recv()
        data = json.loads(response)
        
        print(f"[MAX] Отправка номера, ответ: {data}")
        
        if "payload" not in data:
            raise Exception(f"Неожиданный ответ: {data}")
        
        return data["payload"]  # здесь будет hash
    
    async def verify_code(self, auth_payload: dict, code: str) -> dict:
        """Подтверждает код, возвращает токен"""
        request = {
            "type": "auth",
            "payload": {
                "hash": auth_payload.get("hash"),
                "code": code
            }
        }
        
        await self.websocket.send(json.dumps(request))
        response = await self.websocket.recv()
        data = json.loads(response)
        
        print(f"[MAX] Подтверждение кода, ответ: {data}")
        
        if "payload" not in data:
            raise Exception(f"Неожиданный ответ: {data}")
        
        return data["payload"]
    
    async def close(self):
        """Закрывает соединение"""
        if self.websocket:
            await self.websocket.close()

# ========== КОМАНДА /START ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "👋 Привет! Я помогу зарегистрироваться в Max.\n\n"
        "Введите ваш номер телефона в формате:\n"
        "`+79123456789`",
        parse_mode="Markdown"
    )
    await state.set_state(RegStates.waiting_phone)

# ========== ОБРАБОТКА НОМЕРА ==========
@dp.message(RegStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    
    # Проверка формата
    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer("❌ Неверный формат. Пример: `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    try:
        # Создаём клиент и подключаемся
        max_auth = MaxAuth()
        await max_auth.connect()
        
        # Отправляем номер, получаем hash
        auth_payload = await max_auth.send_code(phone)
        
        # Сохраняем сессию
        temp_sessions[user_id] = {
            "max_auth": max_auth,
            "auth_payload": auth_payload,
            "phone": phone
        }
        
        await message.answer("✅ Код отправлен! Введите код из SMS:")
        await state.set_state(RegStates.waiting_code)
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

# ========== ОБРАБОТКА КОДА ==========
@dp.message(RegStates.waiting_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    # Проверяем сессию
    if user_id not in temp_sessions:
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    max_auth = session["max_auth"]
    auth_payload = session["auth_payload"]
    phone = session["phone"]
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        # Подтверждаем код, получаем токен
        result = await max_auth.verify_code(auth_payload, code)
        
        # Извлекаем токен
        login_token = None
        if "tokenAttrs" in result and "LOGIN" in result["tokenAttrs"]:
            login_token = result["tokenAttrs"]["LOGIN"]["token"]
        elif "token" in result:
            login_token = result["token"]
        
        if not login_token:
            await message.answer(f"❌ Не удалось получить токен. Ответ: {str(result)[:200]}")
            return
        
        # Закрываем соединение
        await max_auth.close()
        
        # Успех!
        await message.answer(
            f"✅ **Регистрация в Max успешна!**\n\n"
            f"📱 Номер: `{phone}`\n"
            f"🔑 Токен: `{login_token[:30]}...`\n\n"
            f"⚠️ Сохраните этот токен для будущих входов.",
            parse_mode="Markdown"
        )
        
        # Выводим полный токен в консоль (логи Railway)
        print(f"\n{'='*50}")
        print(f"✅ НОВЫЙ ПОЛЬЗОВАТЕЛЬ ЗАРЕГИСТРИРОВАН")
        print(f"📱 Номер: {phone}")
        print(f"🔑 Токен: {login_token}")
        print(f"🆔 User ID: {user_id}")
        print(f"{'='*50}\n")
        
        # Очищаем сессию
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

# ========== ЗАПУСК ==========
async def main():
    print("=" * 50)
    print("🚀 Бот запущен")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
