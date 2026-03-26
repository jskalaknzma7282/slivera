import asyncio
import os
import traceback
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from vkmax.client import MaxClient

# ========== ПАТЧ ДЛЯ vkmax ==========
print("🔧 Применяю патч для vkmax...")
_original_send_code = MaxClient.send_code

async def patched_send_code(self, phone: str):
    """Исправленная версия send_code - возвращает payload целиком"""
    start_auth_response = await self.ws.send_and_receive(
        "auth", {"phone": phone}
    )
    # Возвращаем весь payload (там будет hash или другая информация)
    if "payload" in start_auth_response:
        return start_auth_response["payload"]
    return start_auth_response

MaxClient.send_code = patched_send_code
print("✅ vkmax.send_code исправлен")
# ====================================

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

# ========== КОМАНДА /START ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    print(f"[DEBUG] /start от пользователя {message.from_user.id}")
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
    
    print(f"[DEBUG] Пользователь {user_id} ввёл номер: {phone}")
    
    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer("❌ Неверный формат. Пример: `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    try:
        client = MaxClient()
        await client.connect()
        
        # send_code теперь возвращает payload (с hash)
        auth_payload = await client.send_code(phone)
        print(f"[DEBUG] auth_payload: {auth_payload}")
        
        # Сохраняем сессию
        temp_sessions[user_id] = {
            "client": client,
            "auth_payload": auth_payload,
            "phone": phone
        }
        
        await message.answer("✅ Код отправлен! Введите код из SMS:")
        await state.set_state(RegStates.waiting_code)
        
    except Exception as e:
        print(f"[DEBUG] Ошибка: {type(e).__name__}: {e}")
        traceback.print_exc()
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

# ========== ОБРАБОТКА КОДА ==========
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
    auth_payload = session["auth_payload"]
    phone = session["phone"]
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        # Используем sign_in с правильными параметрами
        # В зависимости от структуры auth_payload, может понадобиться hash
        account_data = await client.sign_in(auth_payload, code)
        print(f"[DEBUG] account_data: {account_data}")
        
        # Ищем токен
        login_token = None
        device_id = client.device_id
        
        if "payload" in account_data and "tokenAttrs" in account_data["payload"]:
            login_token = account_data["payload"]["tokenAttrs"]["LOGIN"]["token"]
        elif "token" in account_data:
            login_token = account_data["token"]
        
        if not login_token:
            await message.answer(f"❌ Не удалось получить токен. Ответ: {str(account_data)[:200]}")
            return
        
        await message.answer(
            f"✅ **Регистрация успешна!**\n\n"
            f"📱 Номер: `{phone}`\n"
            f"🔑 Токен: `{login_token[:20]}...`\n"
            f"🆔 Device ID: `{device_id}`",
            parse_mode="Markdown"
        )
        
        print(f"[DEBUG] Токен: {login_token}")
        
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        print(f"[DEBUG] Ошибка: {type(e).__name__}: {e}")
        traceback.print_exc()
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

# ========== ЗАПУСК ==========
async def main():
    print("🚀 Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
