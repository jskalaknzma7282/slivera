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

# Список прокси из твоего файла
PROXY_LIST = [
    ("47.250.155.254", 45),
    ("150.107.140.238", 3128),
    ("65.108.203.37", 18080),
    ("89.208.106.138", 10808),
    ("38.145.208.192", 8443),
    ("13.230.49.39", 8080),
    ("137.220.150.152", 6005),
    ("35.225.22.61", 80),
    ("51.79.135.131", 8080),
    ("195.123.213.129", 1080),
    ("167.71.196.28", 8080),
    ("38.34.179.37", 8447),
    ("8.212.177.126", 8080),
    ("38.34.179.38", 8447),
    ("200.174.198.32", 8888),
    ("45.136.130.191", 8453),
    ("20.78.118.91", 8561),
    ("46.17.47.48", 80),
    ("4.195.16.140", 80),
    ("20.210.39.153", 8561),
    ("143.42.66.91", 80),
    ("158.160.215.167", 8123),
    ("38.34.179.53", 8452),
    ("38.34.179.186", 8444),
    ("38.34.179.172", 8451),
    ("45.136.130.168", 8452),
    ("89.116.88.19", 80),
    ("37.187.74.125", 80),
    ("38.145.208.189", 8443),
    ("32.223.6.94", 80),
    ("20.24.43.214", 80),
    ("20.78.26.206", 8561),
    ("23.247.136.254", 80),
    ("190.58.248.86", 80),
    ("50.122.86.118", 80),
    ("46.29.162.166", 80),
    ("190.119.132.62", 80),
    ("113.160.132.26", 8080),
    ("190.119.132.61", 80),
    ("192.73.244.36", 80),
    ("64.227.76.27", 1080),
    ("175.139.233.79", 80),
    ("210.223.44.230", 3128),
    ("89.58.55.33", 80),
    ("156.146.56.231", 8081),
    ("47.238.203.170", 50000),
    ("151.236.24.38", 80),
    ("38.34.179.150", 8449),
    ("213.157.6.50", 80),
    ("213.33.126.130", 80),
    ("194.158.203.14", 80),
    ("124.108.6.20", 8085),
    ("23.88.88.105", 443),
    ("38.34.179.182", 8443),
    ("139.162.200.213", 80),
    ("45.136.131.39", 8443),
    ("38.145.203.124", 8443),
    ("23.88.88.102", 80),
    ("62.99.138.162", 80),
    ("38.145.208.186", 8443),
    ("38.145.218.14", 8443),
    ("38.145.208.221", 8443),
    ("38.145.208.180", 8443),
    ("38.145.203.86", 8443),
    ("219.65.73.81", 80),
    ("121.126.185.63", 25152),
    ("172.193.178.226", 80),
    ("27.34.242.98", 80),
    ("103.125.31.222", 80),
    ("102.223.9.53", 80),
    ("34.44.49.215", 80),
    ("84.39.112.144", 3128),
    ("46.47.197.210", 3128),
    ("41.220.16.213", 80),
    ("103.84.95.54", 7890),
]

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
        client = MaxClient(proxy_list=PROXY_LIST)
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
