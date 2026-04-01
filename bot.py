import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Ссылки"),
            KeyboardButton(text="Реферальная система")
        ]
    ],
    resize_keyboard=True
)

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🔐")
    
    await asyncio.sleep(1)
    
    await message.answer(
        "<b>🔑 Добро пожаловать в GSM!</b>\n\n"
        "<i>• Выберите действие:</i>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.message(lambda message: message.text == "Ссылки")
async def links(message: types.Message):
    await message.answer("Заглушка: Ссылки. В разработке")

@dp.message(lambda message: message.text == "Реферальная система")
async def referral(message: types.Message):
    await message.answer("Заглушка: Реферальная система. В разработке")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
