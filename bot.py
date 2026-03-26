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

from max_api import MaxClient
from max_api.types import UserAgent

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
    user_id = message.from_user.id
    print(f"[DEBUG] /start от пользователя {user_id}")
    
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
        # Создаём клиент
        client = MaxClient(
            phone=phone,
            user_agent=UserAgent(
                device_type="DESKTOP",
                app_version="25.12.13"
            )
        )
        
        # Запускаем авторизацию в фоне
        code_event = asyncio.Event()
        user_code = None
        
        # Подписываемся на событие запроса кода
        @client.on_code_request
        async def on_code_request():
            nonlocal user_code
            await message.answer("📲 Код отправлен! Введите код из SMS:")
            await state.set_state(RegStates.waiting_code)
            await code_event.wait()
            return user_code
        
        # Сохраняем клиент
        temp_sessions[user_id] = {
            "client": client,
            "phone": phone,
            "code_event": code_event,
            "code": None
        }
        
        # Запускаем клиент
        asyncio.create_task(client.start())
        
    except Exception as e:
        error_msg = str(e).lower()
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        
        if "already registered" in error_msg or "exists" in error_msg:
            await message.answer(
                "❌ **Этот номер уже зарегистрирован в Max!**\n\n"
                "Используйте другой номер или войдите в существующий аккаунт.",
                parse_mode="Markdown"
            )
        else:
            await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")
        
        if user_id in temp_sessions:
            del temp_sessions[user_id]
        await state.clear()

# ========== ОБРАБОТКА КОДА ==========
@dp.message(RegStates.waiting_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    print(f"[DEBUG] Пользователь {user_id} ввёл код: {code}")
    
    if user_id not in temp_sessions:
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    client = session["client"]
    phone = session["phone"]
    code_event = session["code_event"]
    
    # Передаём код
    session["code"] = code
    code_event.set()
    
    await message.answer("🔐 Проверяю код...")
    
    try:
        # Ждём завершения авторизации
        await asyncio.sleep(5)
        
        if client.is_authorized:
            token = client.token if hasattr(client, 'token') else None
            
            if token:
                await message.answer(
                    f"✅ **Регистрация в Max успешна!**\n\n"
                    f"📱 Номер: `{phone}`\n"
                    f"🔑 Токен: `{token[:30]}...`\n\n"
                    f"⚠️ Сохраните токен.",
                    parse_mode="Markdown"
                )
                print(f"\n{'='*50}")
                print(f"✅ ПОЛЬЗОВАТЕЛЬ")
                print(f"📱 Номер: {phone}")
                print(f"🔑 Токен: {token}")
                print(f"{'='*50}\n")
            else:
                await message.answer(
                    f"✅ **Авторизация успешна!**\n\n"
                    f"📱 Номер: `{phone}`",
                    parse_mode="Markdown"
                )
        else:
            await message.answer(
                "❌ **Неверный код!**\n\n"
                "Попробуйте ещё раз. Если код не пришёл, запросите новый через /start",
                parse_mode="Markdown"
            )
            # Сбрасываем событие для нового кода
            session["code"] = None
            code_event.clear()
            await state.set_state(RegStates.waiting_code)
            return
        
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        
        error_msg = str(e).lower()
        if "invalid" in error_msg or "wrong" in error_msg:
            await message.answer(
                "❌ **Неверный код!**\n\n"
                "Попробуйте ещё раз.",
                parse_mode="Markdown"
            )
            session["code"] = None
            code_event.clear()
            await state.set_state(RegStates.waiting_code)
        else:
            await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")
            del temp_sessions[user_id]
            await state.clear()

# ========== ЗАПУСК ==========
async def main():
    print("=" * 50)
    print("🚀 Бот запущен (max-api)")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
