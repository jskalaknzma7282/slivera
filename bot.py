import asyncio
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GREEN_API_ID = os.getenv("GREEN_API_ID")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN")
GREEN_API_URL = os.getenv("GREEN_API_URL", "https://api.green-api.com")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class Form(StatesGroup):
    phone = State()
    code = State()

def green_api(method, data=None):
    url = f"{GREEN_API_URL}/waInstance{GREEN_API_ID}/{method}/{GREEN_API_TOKEN}"
    headers = {"Content-Type": "application/json"}
    r = requests.post(url, json=data, headers=headers) if data else requests.get(url, headers=headers)
    return r.json() if r.status_code == 200 else {"error": r.text}

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
    resp = green_api("startAuthorization", {"phone": phone})
    if "error" in resp:
        await msg.answer(f"Ошибка: {resp['error']}")
        return
    await state.update_data(phone=phone)
    await msg.answer("Код отправлен. Введите 6-значный код из SMS")
    await state.set_state(Form.code)

@dp.message(Form.code)
async def get_code(msg: types.Message, state: FSMContext):
    code = msg.text.strip()
    data = await state.get_data()
    resp = green_api("sendAuthorizationCode", {"code": code})
    if "error" in resp:
        await msg.answer(f"Ошибка: {resp['error']}")
        return
    token = resp.get("token")
    if token:
        await msg.answer(f"✅ Регистрация успешна!\nТокен: {token[:30]}...")
    else:
        await msg.answer("✅ Авторизация прошла успешно!")
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
