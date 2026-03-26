import asyncio
import os
import sys
import traceback
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# ========== ПРОВЕРКА БИБЛИОТЕКИ max-private-api ==========
print("=" * 60)
print("🔍 ПРОВЕРКА БИБЛИОТЕКИ max-private-api")
print("=" * 60)

try:
    from max_private_api import MaxClient
    print("✅ Библиотека max_private_api найдена и импортирована")
except ImportError as e:
    print(f"❌ Библиотека max_private_api не установлена: {e}")
    print("❌ Бот не может работать без этой библиотеки")
    sys.exit(1)

# Выводим все методы MaxClient
print("\n📋 Методы MaxClient:")
methods = [m for m in dir(MaxClient) if not m.startswith('_')]
for method in methods:
    print(f"  - {method}")

# Проверяем наличие ключевых методов
print("\n🔑 Ключевые методы:")
key_methods = ['login', 'auth', 'send_code', 'confirm_code', 'verify_code', 'submit_code', 'run_polling', 'on']
for km in key_methods:
    if km in methods:
        print(f"  ✅ {km} - есть")
    else:
        print(f"  ❌ {km} - нет")

# Пробуем создать экземпляр и посмотреть его атрибуты
try:
    client_test = MaxClient()
    print("\n📦 Атрибуты экземпляра MaxClient (первые 20):")
    attrs = [a for a in dir(client_test) if not a.startswith('_')]
    for attr in attrs[:20]:
        print(f"  - {attr}")
    if len(attrs) > 20:
        print(f"  ... и ещё {len(attrs) - 20} атрибутов")
except Exception as e:
    print(f"\n❌ Ошибка при создании MaxClient: {e}")

print("\n" + "=" * 60)
print("✅ ПРОВЕРКА ЗАВЕРШЕНА, ЗАПУСКАЮ БОТА")
print("=" * 60)
print("")
# ========== КОНЕЦ ПРОВЕРКИ ==========

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
        # Создаём клиент Max
        client = MaxClient()
        
        # Сохраняем клиент
        temp_sessions[user_id] = {
            "client": client,
            "phone": phone
        }
        
        # Запускаем авторизацию в фоне
        async def auth_task():
            try:
                # Проверяем, какой метод доступен
                if hasattr(client, 'login'):
                    # Если есть login, вызываем его
                    print(f"[DEBUG] Вызываю client.login(phone={phone})")
                    await client.login(phone=phone)
                elif hasattr(client, 'auth'):
                    print(f"[DEBUG] Вызываю client.auth(phone={phone})")
                    await client.auth(phone=phone)
                elif hasattr(client, 'send_code'):
                    print(f"[DEBUG] Вызываю client.send_code(phone={phone})")
                    await client.send_code(phone=phone)
                else:
                    raise Exception("Нет метода для отправки номера")
                
                # Создаём событие для ожидания кода
                code_event = asyncio.Event()
                temp_sessions[user_id]["code_event"] = code_event
                
                await message.answer("📲 Код отправлен! Введите код из SMS:")
                await state.set_state(RegStates.waiting_code)
                
                # Ждём код от пользователя
                await code_event.wait()
                
                # Получаем код
                code = temp_sessions[user_id].get("code")
                if not code:
                    return
                
                # Передаём код в библиотеку
                if hasattr(client, 'confirm_code'):
                    print(f"[DEBUG] Вызываю client.confirm_code({code})")
                    await client.confirm_code(code)
                elif hasattr(client, 'verify_code'):
                    print(f"[DEBUG] Вызываю client.verify_code({code})")
                    await client.verify_code(code)
                elif hasattr(client, 'submit_code'):
                    print(f"[DEBUG] Вызываю client.submit_code({code})")
                    await client.submit_code(code)
                else:
                    # Если нет метода для кода, пробуем установить напрямую
                    print(f"[DEBUG] Нет метода для кода, пробую client.code = {code}")
                    client.code = code
                
                # Ждём завершения авторизации
                await asyncio.sleep(3)
                
                # Проверяем, авторизован ли клиент
                is_auth = False
                token = None
                
                if hasattr(client, 'is_authorized'):
                    is_auth = client.is_authorized
                elif hasattr(client, 'authorized'):
                    is_auth = client.authorized
                elif hasattr(client, 'me'):
                    is_auth = client.me is not None
                
                if hasattr(client, 'token'):
                    token = client.token
                elif hasattr(client, 'access_token'):
                    token = client.access_token
                
                if is_auth or token:
                    await message.answer(
                        f"✅ **Регистрация в Max успешна!**\n\n"
                        f"📱 Номер: `{phone}`\n"
                        f"🔑 Токен: `{str(token)[:30]}...`\n\n"
                        f"⚠️ Сохраните токен.",
                        parse_mode="Markdown"
                    )
                    print(f"✅ Токен: {token}")
                else:
                    await message.answer(
                        "❌ **Неверный код!**\n\n"
                        "Попробуйте ещё раз. Если код не пришёл, запросите новый через /start",
                        parse_mode="Markdown"
                    )
                    # Даём попробовать снова
                    temp_sessions[user_id]["code_event"] = asyncio.Event()
                    await state.set_state(RegStates.waiting_code)
                    return
                    
            except Exception as e:
                print(f"[ERROR] auth_task: {type(e).__name__}: {e}")
                traceback.print_exc()
                await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")
            finally:
                # Очищаем сессию при успехе или ошибке
                if user_id in temp_sessions:
                    del temp_sessions[user_id]
                await state.clear()
        
        # Запускаем задачу
        asyncio.create_task(auth_task())
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

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
    code_event = session.get("code_event")
    
    if code_event:
        session["code"] = code
        code_event.set()
        await message.answer("🔐 Код принят, завершаю регистрацию...")
    else:
        await message.answer("❌ Ошибка: не найден обработчик кода. Начните заново с /start")
        del temp_sessions[user_id]
        await state.clear()

# ========== ЗАПУСК ==========
async def main():
    print("=" * 60)
    print("🚀 Бот запущен")
    print("=" * 60)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
