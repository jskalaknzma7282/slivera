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

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ==========
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден в .env файле")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
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
    
    # Проверка формата
    if not phone.startswith("+") or not phone[1:].isdigit():
        print(f"[DEBUG] Неверный формат номера: {phone}")
        await message.answer("❌ Неверный формат. Введите номер в формате `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    print(f"[DEBUG] Создаю клиент MaxClient...")
    
    try:
        # Создаём клиент
        client = MaxClient()
        print(f"[DEBUG] Клиент создан: {client}")
        
        # Подключаемся к WebSocket
        print(f"[DEBUG] Вызываю client.connect()...")
        await client.connect()
        print(f"[DEBUG] WebSocket подключён успешно")
        
        # Отправляем номер
        print(f"[DEBUG] Вызываю client.send_code({phone})...")
        sms_token = await client.send_code(phone)
        print(f"[DEBUG] send_code вернул: {sms_token} (тип: {type(sms_token)})")
        
        # Сохраняем сессию
        temp_sessions[user_id] = {
            "client": client,
            "sms_token": sms_token,
            "phone": phone
        }
        print(f"[DEBUG] Сессия сохранена для {user_id}")
        
        await message.answer("✅ Код отправлен! Проверьте SMS и введите 6-значный код:")
        await state.set_state(RegStates.waiting_code)
        
    except Exception as e:
        print(f"[DEBUG] ========== ОШИБКА ==========")
        print(f"[DEBUG] Тип ошибки: {type(e).__name__}")
        print(f"[DEBUG] Текст: {e}")
        print(f"[DEBUG] Полный traceback:")
        traceback.print_exc()
        print(f"[DEBUG] ============================")
        
        await message.answer(
            f"❌ Ошибка при отправке номера:\n"
            f"`{type(e).__name__}: {e}`\n\n"
            f"Попробуйте позже или начните заново с /start",
            parse_mode="Markdown"
        )

# ========== ОБРАБОТКА КОДА ==========
@dp.message(RegStates.waiting_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    print(f"[DEBUG] Пользователь {user_id} ввёл код: {code}")
    
    # Проверяем сессию
    if user_id not in temp_sessions:
        print(f"[DEBUG] Сессия не найдена для {user_id}")
        await message.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    session = temp_sessions[user_id]
    client = session["client"]
    sms_token = session["sms_token"]
    phone = session["phone"]
    
    print(f"[DEBUG] sms_token из сессии: {sms_token}")
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        print(f"[DEBUG] Вызываю client.sign_in({sms_token}, {code})...")
        account_data = await client.sign_in(sms_token, code)
        print(f"[DEBUG] sign_in вернул: {account_data}")
        print(f"[DEBUG] Тип ответа: {type(account_data)}")
        
        # Пробуем разные пути к токену
        login_token = None
        device_id = client.device_id
        print(f"[DEBUG] device_id: {device_id}")
        
        # Старый путь (из примера)
        if 'payload' in account_data and 'tokenAttrs' in account_data['payload']:
            login_token = account_data['payload']['tokenAttrs']['LOGIN']['token']
            print(f"[DEBUG] Токен найден по пути payload.tokenAttrs.LOGIN.token")
        
        # Альтернативный путь
        elif 'token' in account_data:
            login_token = account_data['token']
            print(f"[DEBUG] Токен найден по пути token")
        
        # Если токен в data
        elif 'data' in account_data and 'token' in account_data['data']:
            login_token = account_data['data']['token']
            print(f"[DEBUG] Токен найден по пути data.token")
        
        # Если пришёл список
        elif isinstance(account_data, list) and len(account_data) > 0:
            print(f"[DEBUG] Ответ - список, ищу токен в первом элементе")
            if 'token' in account_data[0]:
                login_token = account_data[0]['token']
        
        # Если ничего не нашли
        if not login_token:
            print(f"[DEBUG] НЕ УДАЛОСЬ НАЙТИ ТОКЕН в ответе")
            print(f"[DEBUG] Полный ответ: {account_data}")
            await message.answer(
                f"❌ Не удалось получить токен. Ответ сервера:\n"
                f"```json\n{str(account_data)[:500]}\n```\n"
                f"Попробуйте снова /start",
                parse_mode="Markdown"
            )
            return
        
        print(f"[DEBUG] Токен найден: {login_token[:20]}...")
        
        await message.answer(
            f"✅ **Регистрация в Max успешна!**\n\n"
            f"📱 Номер: `{phone}`\n"
            f"🔑 Токен: `{login_token[:20]}...` (полный сохранён в консоли)\n"
            f"🆔 Device ID: `{device_id}`\n\n"
            f"⚠️ Сохраните эти данные для будущего входа.",
            parse_mode="Markdown"
        )
        
        # Выводим полный токен в консоль
        print(f"[DEBUG] ========== ПОЛНЫЙ ТОКЕН ПОЛЬЗОВАТЕЛЯ ==========")
        print(f"[DEBUG] User ID: {user_id}")
        print(f"[DEBUG] Phone: {phone}")
        print(f"[DEBUG] Token: {login_token}")
        print(f"[DEBUG] Device ID: {device_id}")
        print(f"[DEBUG] ================================================")
        
        # Очищаем сессию
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        print(f"[DEBUG] ========== ОШИБКА ПРИ ПОДТВЕРЖДЕНИИ ==========")
        print(f"[DEBUG] Тип ошибки: {type(e).__name__}")
        print(f"[DEBUG] Текст: {e}")
        print(f"[DEBUG] Полный traceback:")
        traceback.print_exc()
        print(f"[DEBUG] ===============================================")
        
        await message.answer(
            f"❌ Ошибка при подтверждении кода:\n"
            f"`{type(e).__name__}: {e}`\n\n"
            f"Попробуйте снова /start",
            parse_mode="Markdown"
        )

# ========== ЗАПУСК БОТА ==========
async def main():
    print("=" * 50)
    print("🚀 Бот запущен с режимом отладки")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
