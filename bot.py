import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Токен берем из переменных окружения Railway
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    
    # style может быть: "primary" (синий), "positive" (зеленый), "destructive" (красный)
    builder.row(types.InlineKeyboardButton(
        text="Зеленая кнопка ✅", 
        callback_data="btn_ok",
        style="positive"
    ))
    builder.row(types.InlineKeyboardButton(
        text="Красная кнопка ❌", 
        callback_data="btn_err",
        style="destructive"
    ))

    await message.answer("Пример цветных кнопок:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("btn_"))
async def handle_buttons(callback: types.CallbackQuery):
    await callback.answer(f"Вы нажали: {callback.data}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
