import os
import re
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode
from dotenv import load_dotenv
import asyncpg

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_LINK = os.getenv("CHANNEL_LINK")
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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

    async def update_balance(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                amount, user_id
            )

    async def create_order(self, user_id: int, phone: str = "", status: str = "pending") -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO orders (user_id, phone, status) VALUES ($1, $2, $3) RETURNING id",
                user_id, phone, status
            )
            return row["id"]

    async def update_order_status(self, order_id: int, status: str, confirmed_at: datetime = None):
        async with self.pool.acquire() as conn:
            if confirmed_at:
                await conn.execute(
                    "UPDATE orders SET status = $1, confirmed_at = $2 WHERE id = $3",
                    status, confirmed_at.replace(tzinfo=None), order_id
                )
            else:
                await conn.execute(
                    "UPDATE orders SET status = $1 WHERE id = $2",
                    status, order_id
                )

    async def get_active_session(self, user_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM active_sessions WHERE user_id = $1", user_id)
            if row:
                return {
                    "user_id": row["user_id"],
                    "order_id": row["order_id"],
                    "step": row["step"],
                    "data": json.loads(row["data"]) if row["data"] else {},
                    "started_at": row["started_at"]
                }
            return None

    async def set_active_session(self, user_id: int, order_id: int, step: str, data: dict = None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO active_sessions (user_id, order_id, step, data)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO UPDATE SET
                order_id = $2, step = $3, data = $4, started_at = NOW()
            """, user_id, order_id, step, json.dumps(data or {}))

    async def clear_active_session(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM active_sessions WHERE user_id = $1", user_id)

    async def add_order_message(self, order_id: int, message_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE orders SET message_id = $1 WHERE id = $2",
                message_id, order_id
            )
    
    async def clear_all_sessions(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM active_sessions")
            await conn.execute("UPDATE orders SET status = 'cancelled' WHERE status IN ('taken', 'phone_received', 'code_sent', 'waiting_confirmation')")
            logger.info("All stale sessions cleared")

db = Database()

def normalize_phone(phone: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7":
        return digits
    return None

def format_time(dt: datetime) -> str:
    return dt.astimezone(MOSCOW_TZ).strftime("%H:%M UTC")

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

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    text = (
        "<blockquote>💳 ваш баланс</blockquote>\n\n"
        f"<i>• <code>{user['balance']:.2f}</code> USDT</i>\n\n"
        "<i>• Для вывода напишите вывод сумма</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "<blockquote>⛔️ вывод средств</blockquote>\n\n<i>• временно недоступен</i>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    now = datetime.now(MOSCOW_TZ)
    start_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end_time = now.replace(hour=20, minute=0, second=0, microsecond=0)
    
    async with db.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.user_id, u.username, o.phone, o.confirmed_at
            FROM users u
            JOIN orders o ON u.user_id = o.user_id
            WHERE o.status = 'confirmed'
            AND o.confirmed_at BETWEEN $1 AND $2
            ORDER BY u.username
        """, start_time.replace(tzinfo=None), end_time.replace(tzinfo=None))
        
        if not rows:
            await update.message.reply_text("✏️ Отчетов сегодня нету")
            return
        
        users_data = {}
        for row in rows:
            uid = row["user_id"]
            if uid not in users_data:
                users_data[uid] = {
                    "username": row["username"] or f"user_{uid}",
                    "phones": [],
                    "total": 0
                }
            users_data[uid]["phones"].append(row["phone"])
            users_data[uid]["total"] += 4.0
        
        lines = []
        for uid, data in users_data.items():
            lines.append(f"{data['username']} (ID: {uid}) • {data['total']:.2f} USDT")
            for phone in data["phones"]:
                lines.append(f"  {phone} • 4.00 USDT")
            lines.append("")
        
        report_text = "\n".join(lines)
        await update.message.reply_document(
            document=report_text.encode(),
            filename=f"report_{now.strftime('%Y%m%d')}.txt"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if text == "бал":
        await balance_command(update, context)
        return
    
    if text.startswith("вывод"):
        await withdraw_command(update, context)
        return
    
    session = await db.get_active_session(user_id)
    if not session:
        await update.message.reply_text("У вас нет активной заявки")
        return
    
    order_id = session["order_id"]
    step = session["step"]
    data = session["data"] or {}
    
    if step == "waiting_phone":
        phone = normalize_phone(text)
        if not phone:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("повторить", callback_data="retry_phone")]
            ])
            await update.message.reply_text(
                "<blockquote>📌 ошибка</blockquote>\n\n"
                "<i>• введен неккоректный номер телефона! формат: 7хххХХХхххх</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return
        
        await db.update_order_status(order_id, "phone_received")
        await db.set_active_session(user_id, order_id, "waiting_sms", {"phone": phone})
        
        user = await db.get_user(user_id)
        
        admin_text = (
            f"<blockquote>🔖 заявка <code>#{phone}</code></blockquote>\n\n"
            f"<i>от: {user['username'] or f'user_{user_id}'} [<code>{user_id}</code>]</i>\n"
            f"<i>номер: <code>{phone}</code></i>\n"
            f"<i>время: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("+", callback_data=f"admin_sms_{order_id}"),
                InlineKeyboardButton("-", callback_data=f"admin_reject_{order_id}")
            ]
        ])
        admin_msg = await context.bot.send_message(
            ADMIN_ID, admin_text, parse_mode=ParseMode.HTML, reply_markup=keyboard
        )
        await db.add_order_message(order_id, admin_msg.message_id)
        
        await update.message.reply_text(
            "<blockquote>📮 номер в обработке</blockquote>\n\n"
            "<i>• ожидайте запроса SMS (в среднем занимает ≈ 2м)</i>",
            parse_mode=ParseMode.HTML
        )
        return

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
        logger.info(f"Take order from {user_id}")
        
        session = await db.get_active_session(user_id)
        if session:
            logger.info(f"Cleaning old session for user {user_id}")
            await db.clear_active_session(user_id)
            await db.update_order_status(session["order_id"], "cancelled")
        
        order_id = await db.create_order(user_id, "", "taken")
        await db.set_active_session(user_id, order_id, "waiting_phone", {})
        
        await query.message.delete()
        
        try:
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
            logger.info(f"Message sent to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to send: {e}")
            await db.clear_active_session(user_id)
            await db.update_order_status(order_id, "cancelled")
            await query.answer("Сначала напишите /start", show_alert=True)
        return
    
    if data == "cancel":
        session = await db.get_active_session(user_id)
        if session:
            await db.clear_active_session(user_id)
            await db.update_order_status(session["order_id"], "cancelled")
            await query.message.delete()
            await query.answer("Заявка отменена")
        return
    
    if data == "retry_phone":
        session = await db.get_active_session(user_id)
        if session and session["step"] == "waiting_phone":
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
            await db.add_order_message(session["order_id"], msg.message_id)
        return

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("bal", balance_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def init_and_start():
        await db.init()
        await db.clear_all_sessions()
        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()
    
    try:
        loop.run_until_complete(init_and_start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(app.stop())
        loop.close()

if __name__ == "__main__":
    main()
