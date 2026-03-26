import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from max_client import MaxClient

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден")

# Прокси из переменных окружения
PROXY_HOST = os.getenv("PROXY_HOST", "103.84.95.54")
PROXY_PORT = int(os.getenv("PROXY_PORT", "7890"))

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class Form(StatesGroup):
    phone = State()
    code = State()

temp_data = {}

@dp.message(Command("start"))
async def start(msg: types.Message, state: FSMContext):
    await msg.answer("Введите номер телефона в формате +79123456789")
    await state.set_state(Form.phone)

@dp.message(Form.phone)
async def get_phone(msg: types.Message, state: FSMContext):
    phone = msg.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await msg.answer("Неверный формат. Пример: +79123456789")
        return
    
    await msg.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    try:
        client = MaxClient(proxy_host=PROXY_HOST, proxy_port=PROXY_PORT)
        client.connect()
        token = client.request_code(phone)
        
        temp_data[msg.from_user.id] = {
            "client": client,
            "token": token,
            "phone": phone
        }
        
        await msg.answer("✅ Код отправлен. Введите код из SMS")
        await state.set_state(Form.code)
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
        await state.clear()

@dp.message(Form.code)
async def get_code(msg: types.Message, state: FSMContext):
    code = msg.text.strip()
    user_id = msg.from_user.id
    
    if user_id not in temp_data:
        await msg.answer("❌ Сессия истекла. Начните заново с /start")
        await state.clear()
        return
    
    data = temp_data[user_id]
    client = data["client"]
    token = data["token"]
    phone = data["phone"]
    
    await msg.answer("🔐 Подтверждаю код...")
    
    try:
        auth_data = client.verify_code(token, code)
        
        reg_token = auth_data.get('tokenAttrs', {}).get('REGISTER', {}).get('token')
        if reg_token:
            final_token = client.register(reg_token)
            await msg.answer(f"✅ **Регистрация успешна!**\n\n📱 Номер: `{phone}`\n🔑 Токен: `{final_token[:30]}...`", parse_mode="Markdown")
        else:
            login_token = auth_data.get('tokenAttrs', {}).get('LOGIN', {}).get('token')
            if login_token:
                await msg.answer(f"✅ **Вход выполнен!**\n\n📱 Номер: `{phone}`\n🔑 Токен: `{login_token[:30]}...`", parse_mode="Markdown")
            else:
                raise Exception("Не удалось получить токен")
        
        client.close()
        del temp_data[user_id]
        await state.clear()
        
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
        client.close()
        del temp_data[user_id]
        await state.clear()

async def main():
    logging.basicConfig(level=logging.INFO)
    print("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
