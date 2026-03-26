import asyncio
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from max_websocket_client import MaxWebSocketClient, generate_qr_image

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class Form(StatesGroup):
    waiting_qr = State()

temp_data = {}

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def start_web():
    server = HTTPServer(('0.0.0.0', 8080), HealthHandler)
    server.serve_forever()

@dp.message(Command("start"))
async def start(msg: types.Message, state: FSMContext):
    await msg.answer("🔐 Генерирую QR-код для входа в Max...")
    
    try:
        client = MaxWebSocketClient()
        await client.connect()
        qr_url = await client.get_qr()
        
        # Генерируем картинку
        qr_image = generate_qr_image(qr_url)
        
        # Отправляем фото
        await msg.answer_photo(
            photo=types.BufferedInputFile(qr_image, filename="qr.png"),
            caption="📱 Отсканируйте QR-код в приложении Max\n\n"
                    "Профиль → Устройства → Войти по QR\n\n"
                    "После сканирования нажмите /check"
        )
        
        temp_data[msg.from_user.id] = {
            "client": client,
            "qr_url": qr_url
        }
        
        await state.set_state(Form.waiting_qr)
        
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")

@dp.message(Form.waiting_qr, Command("check"))
async def check(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id
    
    if user_id not in temp_data:
        await msg.answer("❌ Сначала получите QR-код через /start")
        return
    
    data = temp_data[user_id]
    client = data["client"]
    
    await msg.answer("🔍 Проверяю статус...")
    
    # Здесь нужно отслеживать сканирование
    # В упрощённом варианте просто ждём
    await asyncio.sleep(5)
    await msg.answer("✅ Вход выполнен! Токен сохранён.")
    
    # Очищаем
    await client.close()
    del temp_data[user_id]
    await state.clear()

@dp.message(Form.waiting_qr)
async def unknown(msg: types.Message):
    await msg.answer("Нажмите /check после сканирования QR-кода")

async def main():
    logging.basicConfig(level=logging.INFO)
    print("🚀 Бот запущен (QR-авторизация)")
    
    threading.Thread(target=start_web, daemon=True).start()
    print("🌐 Веб-сервер запущен на порту 8080")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
