import os
import re
import json
import logging
from datetime import datetime
from typing import Optional
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from dotenv import load_dotenv
import asyncpg

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_LINK = os.getenv("CHANNEL_LINK")
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

class Database:
    def __init__(self):
        self.pool = None

    async def init(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        await self.create_tables()

    async def create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    balance DECIMAL DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    phone TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    confirmed_at TIMESTAMP,
                    message_id BIGINT,
                    admin_message_id BIGINT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS active_sessions (
                    user_id BIGINT PRIMARY KEY,
                    order_id INTEGER,
                    step TEXT,
                    data TEXT,
                    started_at TIMESTAMP DEFAULT NOW()
                )
            """)

    async def get_user(self, user_id: int, username: str = None):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not row:
                await conn.execute(
                    "INSERT INTO users (user_id, username, balance) VALUES ($1, $2, $3)",
                    user_id, username or "", 0
                )
                return {"user_id": user_id, "username": username, "balance": 0}
            return dict(row)

    async def create_order(self, user_id: int, phone: str = "", status: str = "pending") -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO orders (user_id, phone, status) VALUES ($1, $2, $3) RETURNING id",
                user_id, phone, status
            )
            return row["id"]

    async def update_order_status(self, order_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE orders SET status = $1 WHERE id = $2",
                status, order_id
            )

    async def set_active_session(self, user_id: int, order_id: int, step: str, data: dict = None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO active_sessions (user_id, order_id, step, data)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO UPDATE SET
                order_id = $2, step = $3, data = $4, started_at = NOW()
            """, user_id, order_id, step, json.dumps(data or {}))

    async def get_active_session(self, user_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM active_sessions WHERE user_id = $1", user_id)
            if row:
                return {
                    "user_id": row["user_id"],
                    "order_id": row["order_id"],
                    "step": row["step"],
                    "data": json.loads(row["data"]) if row["data"] else {}
                }
            return None

    async def clear_active_session(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM active_sessions WHERE user_id = $1", user_id)

    async def add_order_message(self, order_id: int, message_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE orders SET message_id = $1 WHERE id = $2",
                message_id, order_id
            )

db = Database()

def normalize_phone(phone: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7":
        return digits
    return None

async def publish_new_order(context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<blockquote>🔖 заявка создана</blockquote>\n\n"
        "<i>• для принятия заявки нажмите кнопку ниже:</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("принять заявку", callback_data="take_order")]
    ])
    await context.bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    await db.get_user(user_id, username)
    
    text = (
        "<blockquote><b>👋 Добро пожаловать в сервис JetMax!</b></blockquote>\n\n"
        f"• В данном боте обрабатываются SMS заявки, новостной канал: {CHANNEL_LINK}"
    )
    
    keyboard = None
    if user_id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ новая заявка", callback_data="new_order")]
        ])
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"Callback: {data} from {user_id}")
    await query.answer()
    
    if data == "new_order" and user_id == ADMIN_ID:
        await publish_new_order(context)
        await query.message.delete()
        return
    
    if data == "take_order":
        session = await db.get_active_session(user_id)
        if session:
            await query.answer("У вас уже есть активная заявка!", show_alert=True)
            return
        
        order_id = await db.create_order(user_id, "", "taken")
        await db.set_active_session(user_id, order_id, "waiting_phone", {})
        
        await query.message.delete()
        
        msg = await context.bot.send_message(
            user_id,
            "<blockquote>✏️ введите номер телефона</blockquote>\n\n"
            "<i>• формат не важен, на отправку материала у вас ровно: 60</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("отмена", callback_data="cancel")]
            ])
        )
        await db.add_order_message(order_id, msg.message_id)
        return

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)
    
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init())
    
    logger.info("Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
