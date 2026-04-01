import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Информация", style="success"),
            KeyboardButton(text="Ссылки")
        ],
        [
            KeyboardButton(text="Поддержка", style="danger")
        ]
    ],
    resize_keyboard=True
)

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Добро пожаловать в GSM👋\n\n"
        "<i>Выберите действие:</i>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.message(lambda message: message.text == "Информация")
async def info(message: types.Message):
    await message.answer("Заглушка: Информация. В разработке")

@dp.message(lambda message: message.text == "Ссылки")
async def links(message: types.Message):
    await message.answer("Заглушка: Ссылки. В разработке")

@dp.message(lambda message: message.text == "Поддержка")
async def support(message: types.Message):
    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Поддержка >",
                url="https://t.me/switchsupp",
                style="primary"
            )
        ]
    ])
    
    await message.answer(
        "<b>🚨 Поддержка/Вопросы</b>\n\n"
        "Если возникли баги с ботом, или проблемы по оплате/перевыдаче обратитесь к администратору. Отвечаем в течение 24 часов.",
        parse_mode="HTML",
        reply_markup=inline_keyboard
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
