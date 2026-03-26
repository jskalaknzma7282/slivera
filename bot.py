import asyncio
import os
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from vkmax.client import MaxClient

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден в .env файле")

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ========== СОСТОЯНИЯ ДЛЯ РЕГИСТРАЦИИ ==========
class RegStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()

# ========== ВРЕМЕННОЕ ХРАНИЛИЩЕ ==========
temp_sessions: Dict[int, dict] = {}

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

# ========== ОБРАБОТКА НОМЕРА ТЕЛЕФОНА ==========
@dp.message(RegStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    
    # Простая проверка формата номера
    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer("❌ Неверный формат. Введите номер в формате `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    try:
        # Создаём клиент Max и подключаемся
        client = MaxClient()
        await client.connect()  # <--- ВАЖНО: сначала подключаемся
        
        # Отправляем номер
        sms_token = await client.send_code(phone)
        
        # Сохраняем временные данные
        temp_sessions[user_id] = {
            "client": client,
            "sms_token": sms_token,
            "phone": phone
        }
        
        await message.answer("✅ Код отправлен! Проверьте SMS и введите 6-значный код:")
        await state.set_state(RegStates.waiting_code)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте снова /start")

# ========== ОБРАБОТКА КОДА ИЗ SMS ==========
@dp.message(RegStates.waiting_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    # Проверяем, что есть активная сессия
    if user_id not in temp_sessions:
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    client = session["client"]
    sms_token = session["sms_token"]
    phone = session["phone"]
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        # Подтверждаем код и получаем токен
        account_data = await client.sign_in(sms_token, code)
        
        # Извлекаем токен и device_id
        login_token = account_data['payload']['tokenAttrs']['LOGIN']['token']
        device_id = client.device_id
        
        print(f"✅ Пользователь {user_id} зарегистрирован")
        print(f"   Token: {login_token}")
        print(f"   Device ID: {device_id}")
        
        # Отправляем результат пользователю
        await message.answer(
            f"✅ **Регистрация в Max успешна!**\n\n"
            f"📱 Номер: `{phone}`\n"
            f"🔑 Токен: `{login_token[:20]}...`\n"
            f"🆔 Device ID: `{device_id}`\n\n"
            f"⚠️ Сохраните эти данные для будущего входа.",
            parse_mode="Markdown"
        )
        
        # Очищаем временные данные
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при подтверждении: {e}\nПопробуйте снова /start")

# ========== ЗАПУСК БОТА ==========
async def main():
    print("🚀 Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
