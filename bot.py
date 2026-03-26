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

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class RegStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()

temp_sessions: Dict[int, dict] = {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "👋 Введите номер телефона в формате:\n`+79123456789`",
        parse_mode="Markdown"
    )
    await state.set_state(RegStates.waiting_phone)

@dp.message(RegStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    
    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer("❌ Неверный формат. Пример: `+79123456789`", parse_mode="Markdown")
        return
    
    await message.answer(f"📱 Отправляю запрос на номер {phone}...")
    
    try:
        client = MaxClient()
        await client.connect()
        sms_token = await client.send_code(phone)
        
        temp_sessions[user_id] = {
            "client": client,
            "sms_token": sms_token,
            "phone": phone
        }
        
        await message.answer("✅ Код отправлен! Введите код из SMS:")
        await state.set_state(RegStates.waiting_code)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

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
    sms_token = session["sms_token"]
    phone = session["phone"]
    
    await message.answer("🔐 Подтверждаю код...")
    
    try:
        account_data = await client.sign_in(sms_token, code)
        login_token = account_data['payload']['tokenAttrs']['LOGIN']['token']
        device_id = client.device_id
        
        await message.answer(
            f"✅ **Регистрация успешна!**\n\n"
            f"📱 Номер: `{phone}`\n"
            f"🔑 Токен: `{login_token[:20]}...`\n"
            f"🆔 Device ID: `{device_id}`",
            parse_mode="Markdown"
        )
        
        del temp_sessions[user_id]
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\nПопробуйте /start")

async def main():
    print("🚀 Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
