import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data="yes", style="success"),
            InlineKeyboardButton(text="❌ Нет", callback_data="no", style="danger")
        ]
    ])
    await message.answer("Выберите:", reply_markup=keyboard)

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    await callback.answer(f"Вы нажали: {callback.data}")
    await callback.message.edit_text(f"✅ Нажато: {callback.data}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
