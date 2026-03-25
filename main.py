import os
import re
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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

    async def add_order_message(self, order_id: int, message_id: int, is_admin: bool = False):
        async with self.pool.acquire() as conn:
            if is_admin:
                await conn.execute(
                    "UPDATE orders SET admin_message_id = $1 WHERE id = $2",
                    message_id, order_id
                )
            else:
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
    bot_username = (await context.bot.get_me()).username
    text = (
        "<blockquote>🔖 заявка создана</blockquote>\n\n"
        "<i>• для принятия заявки нажмите кнопку ниже:</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("принять заявку", callback_data="take_order"),
            InlineKeyboardButton("💬 открыть чат", url=f"https://t.me/{bot_username}")
        ]
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
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("➕ новая заявка")]],
            resize_keyboard=True
        )
    
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

async def start_timer(context: ContextTypes.DEFAULT_TYPE, user_id: int, order_id: int, timer_type: str, seconds: int):
    for i in range(seconds, 0, -1):
        session = await db.get_active_session(user_id)
        if not session or session["order_id"] != order_id:
            return
        
        text = ""
        if timer_type == "phone":
            text = (
                "<blockquote>✏️ введите номер телефона</blockquote>\n\n"
                f"<i>• формат не важен, на отправку материала у вас ровно: <code>{i}</code></i>"
            )
        else:
            text = (
                "<blockquote>📮 запрошено SMS</blockquote>\n\n"
                f"<i>• введите код из смс, у вас ровно: <code>{i}</code></i>"
            )
        
        order = await db.pool.fetchrow("SELECT message_id FROM orders WHERE id = $1", order_id)
        if order and order["message_id"]:
            try:
                await context.bot.edit_message_text(
                    text, user_id, order["message_id"],
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("отмена", callback_data=f"cancel_{order_id}")]
                    ])
                )
            except:
                pass
        
        await asyncio.sleep(1)
    
    session = await db.get_active_session(user_id)
    if not session or session["order_id"] != order_id:
        return
    
    await db.clear_active_session(user_id)
    
    phone = session["data"].get("phone") if session["data"] else None
    
    if phone:
        user_text = (
            f"<blockquote>📌 заявка <code>#{phone}</code></blockquote>\n\n"
            f"<i>статус: время вышло</i>\n"
            f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
        )
    else:
        user_text = (
            f"<blockquote>📌 заявка</blockquote>\n\n"
            f"<i>статус: время вышло</i>\n"
            f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
        )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("заявки", url=CHANNEL_LINK)]
    ])
    
    order = await db.pool.fetchrow("SELECT message_id FROM orders WHERE id = $1", order_id)
    if order and order["message_id"]:
        try:
            await context.bot.edit_message_text(
                user_text, user_id, order["message_id"],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        except:
            pass
    
    await publish_new_order(context)

async def start_fund_timer(context: ContextTypes.DEFAULT_TYPE, user_id: int, order_id: int, phone: str):
    for i in range(600, 0, -1):
        minutes = i // 60
        seconds = i % 60
        timer_text = f"{minutes:02d}:{seconds:02d}"
        
        order = await db.pool.fetchrow("SELECT message_id FROM orders WHERE id = $1", order_id)
        if order and order["message_id"]:
            text = (
                f"<blockquote>🔖 заявка <code>#{phone}</code></blockquote>\n\n"
                f"<i>статус: подтверждена</i>\n"
                f"<i>до зачисления средств: <code>{timer_text}</code></i>\n"
                f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
            )
            try:
                await context.bot.edit_message_text(
                    text, user_id, order["message_id"],
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("заявки", url=CHANNEL_LINK)]
                    ])
                )
            except:
                pass
        
        await asyncio.sleep(1)
    
    await db.update_balance(user_id, 4.0)
    
    user_text = (
        f"<blockquote>🎉 денюжки</blockquote>\n\n"
        f"<i>ваш счет пополнен: + <code>4.00</code> USDT</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("заявки", url=CHANNEL_LINK)]
    ])
    
    order = await db.pool.fetchrow("SELECT message_id FROM orders WHERE id = $1", order_id)
    if order and order["message_id"]:
        try:
            await context.bot.edit_message_text(
                user_text, user_id, order["message_id"],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        except:
            pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if text == "бал":
        await balance_command(update, context)
        return
    
    if text.startswith("вывод"):
        await withdraw_command(update, context)
        return
    
    if text == "➕ новая заявка" and user_id == ADMIN_ID:
        await publish_new_order(context)
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
        await db.add_order_message(order_id, admin_msg.message_id, is_admin=True)
        
        await update.message.reply_text(
            "<blockquote>📮 номер в обработке</blockquote>\n\n"
            "<i>• ожидайте запроса SMS (в среднем занимает ≈ 2м)</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    if step == "waiting_code":
        if not text.isdigit() or len(text) != 6:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("повторить", callback_data="retry_code")]
            ])
            await update.message.reply_text(
                "<blockquote>📌 ошибка</blockquote>\n\n"
                "<i>• введен неккоректный код! формат: хххххх</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return
        
        phone = data.get("phone")
        user = await db.get_user(user_id)
        
        admin_text = (
            f"<blockquote>🔖 SMS! заявка <code>#{phone}</code></blockquote>\n\n"
            f"<i>код: <code>{text}</code></i>\n"
            f"<i>от: {user['username'] or f'user_{user_id}'} [<code>{user_id}</code>]</i>\n"
            f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("+", callback_data=f"admin_confirm_{order_id}"),
                InlineKeyboardButton("-", callback_data=f"admin_reject_registered_{order_id}"),
                InlineKeyboardButton("ош", callback_data=f"admin_error_{order_id}")
            ]
        ])
        admin_msg = await context.bot.send_message(
            ADMIN_ID, admin_text, parse_mode=ParseMode.HTML, reply_markup=keyboard
        )
        await db.add_order_message(order_id, admin_msg.message_id, is_admin=True)
        
        await db.update_order_status(order_id, "code_sent")
        await db.set_active_session(user_id, order_id, "waiting_confirmation", {"phone": phone, "code": text})
        
        user_msg = await update.message.reply_text(
            f"<blockquote>📮 SMS заявка <code>#{phone}</code></blockquote>\n\n"
            f"<i>статус: код ожидает подтверждения</i>\n"
            f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("обновить", callback_data=f"check_status_{order_id}")]
            ])
        )
        await db.add_order_message(order_id, user_msg.message_id)
        return

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"Callback: {data} from {user_id}")
    await query.answer()
    
    if data == "take_order":
        session = await db.get_active_session(user_id)
        if session:
            await query.answer("У вас уже есть активная заявка!", show_alert=True)
            return
        
        order_id = await db.create_order(user_id, "", "taken")
        await db.set_active_session(user_id, order_id, "waiting_phone", {})
        
        await query.message.delete()
        
        try:
            msg = await context.bot.send_message(
                user_id,
                "<blockquote>✏️ введите номер телефона</blockquote>\n\n"
                f"<i>• формат не важен, на отправку материала у вас ровно: <code>60</code></i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("отмена", callback_data=f"cancel_{order_id}")]
                ])
            )
            await db.add_order_message(order_id, msg.message_id)
            asyncio.create_task(start_timer(context, user_id, order_id, "phone", 60))
        except Exception as e:
            logger.error(f"Failed to send: {e}")
            await db.clear_active_session(user_id)
            await db.update_order_status(order_id, "cancelled")
            await query.answer("Сначала напишите /start", show_alert=True)
        return
    
    if data.startswith("cancel_"):
        order_id = int(data.split("_")[1])
        session = await db.get_active_session(user_id)
        if not session or session["order_id"] != order_id:
            return
        
        phone = session["data"].get("phone") if session["data"] else None
        
        await db.clear_active_session(user_id)
        await db.update_order_status(order_id, "cancelled")
        
        if phone:
            admin_text = (
                f"<blockquote>🔖 заявка <code>#{phone}</code></blockquote>\n\n"
                f"<i>от: {query.from_user.username or f'user_{user_id}'} [<code>{user_id}</code>]</i>\n"
                f"<i>статус: отменена</i>\n"
                f"<i>время: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
            )
        else:
            admin_text = (
                f"<blockquote>🔖 заявка</blockquote>\n\n"
                f"<i>от: {query.from_user.username or f'user_{user_id}'} [<code>{user_id}</code>]</i>\n"
                f"<i>статус: отменена</i>"
            )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("инфо", url=f"tg://user?id={user_id}")]
        ])
        
        await context.bot.send_message(ADMIN_ID, admin_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        
        if query.message:
            await query.message.delete()
        
        await publish_new_order(context)
        return
    
    if data.startswith("admin_sms_"):
        order_id = int(data.split("_")[2])
        order = await db.pool.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            return
        
        await db.set_active_session(order["user_id"], order_id, "waiting_code", {"phone": order["phone"]})
        
        user_msg = await context.bot.send_message(
            order["user_id"],
            "<blockquote>📮 запрошено SMS</blockquote>\n\n"
            f"<i>• введите код из смс, у вас ровно: <code>60</code></i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("отмена", callback_data=f"cancel_{order_id}")]
            ])
        )
        await db.add_order_message(order_id, user_msg.message_id)
        
        asyncio.create_task(start_timer(context, order["user_id"], order_id, "code", 60))
        
        await query.message.delete()
        return
    
    if data.startswith("admin_reject_"):
        order_id = int(data.split("_")[2])
        order = await db.pool.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            return
        
        await db.clear_active_session(order["user_id"])
        await db.update_order_status(order_id, "rejected")
        
        user_text = (
            f"<blockquote>🔖 заявка <code>#{order['phone']}</code></blockquote>\n\n"
            f"<i>статус: отменена</i>\n"
            f"<i>причина: отклонена администрацией</i>"
        )
        await context.bot.send_message(
            order["user_id"],
            user_text,
            parse_mode=ParseMode.HTML
        )
        
        await query.message.delete()
        await publish_new_order(context)
        return
    
    if data.startswith("admin_reject_registered_"):
        order_id = int(data.split("_")[3])
        order = await db.pool.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            return
        
        await db.clear_active_session(order["user_id"])
        await db.update_order_status(order_id, "rejected")
        
        user_text = (
            f"<blockquote>🔖 заявка <code>#{order['phone']}</code></blockquote>\n\n"
            f"<i>статус: отменена</i>\n"
            f"<i>причина: уже зарегистрирован</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("заявки", url=CHANNEL_LINK)]
        ])
        await context.bot.send_message(
            order["user_id"],
            user_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        
        await query.message.delete()
        await publish_new_order(context)
        return
    
    if data.startswith("admin_error_"):
        order_id = int(data.split("_")[2])
        order = await db.pool.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            return
        
        await db.clear_active_session(order["user_id"])
        await db.update_order_status(order_id, "error")
        
        user_text = (
            f"<blockquote>🔖 заявка <code>#{order['phone']}</code></blockquote>\n\n"
            f"<i>статус: отменена</i>\n"
            f"<i>причина: ошибка/невалид</i>\n"
            f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("заявки", url=CHANNEL_LINK)]
        ])
        await context.bot.send_message(
            order["user_id"],
            user_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        
        await query.message.delete()
        await publish_new_order(context)
        return
    
    if data.startswith("admin_confirm_"):
        order_id = int(data.split("_")[2])
        order = await db.pool.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            return
        
        await db.update_order_status(order_id, "confirmed", datetime.now(MOSCOW_TZ))
        
        user_text = (
            f"<blockquote>📮 SMS заявка <code>#{order['phone']}</code></blockquote>\n\n"
            f"<i>статус: код ожидает подтверждения</i>\n"
            f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("обновить", callback_data=f"check_status_{order_id}")]
        ])
        
        order_row = await db.pool.fetchrow("SELECT message_id FROM orders WHERE id = $1", order_id)
        if order_row and order_row["message_id"]:
            try:
                await context.bot.edit_message_text(
                    user_text,
                    order["user_id"],
                    order_row["message_id"],
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
            except:
                pass
        
        await query.message.delete()
        return
    
    if data.startswith("check_status_"):
        order_id = int(data.split("_")[2])
        order = await db.pool.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order or order["status"] != "confirmed":
            return
        
        session = await db.get_active_session(user_id)
        if session:
            await db.clear_active_session(user_id)
        
        user_text = (
            f"<blockquote>🔖 заявка <code>#{order['phone']}</code></blockquote>\n\n"
            f"<i>статус: подтверждена</i>\n"
            f"<i>до зачисления средств: <code>10:00</code></i>\n"
            f"<i>дата: <code>{format_time(datetime.now(MOSCOW_TZ))}</code></i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("заявки", url=CHANNEL_LINK)]
        ])
        await query.edit_message_text(
            user_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        
        asyncio.create_task(start_fund_timer(context, order["user_id"], order_id, order["phone"]))
        return
    
    if data.startswith("retry_phone"):
        session = await db.get_active_session(user_id)
        if session and session["step"] == "waiting_phone":
            await query.message.delete()
            msg = await context.bot.send_message(
                user_id,
                "<blockquote>✏️ введите номер телефона</blockquote>\n\n"
                f"<i>• формат не важен, на отправку материала у вас ровно: <code>60</code></i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("отмена", callback_data=f"cancel_{session['order_id']}")]
                ])
            )
            await db.add_order_message(session["order_id"], msg.message_id)
            asyncio.create_task(start_timer(context, user_id, session["order_id"], "phone", 60))
        return
    
    if data.startswith("retry_code"):
        session = await db.get_active_session(user_id)
        if session and session["step"] == "waiting_code":
            await query.message.delete()
            msg = await context.bot.send_message(
                user_id,
                "<blockquote>📮 запрошено SMS</blockquote>\n\n"
                f"<i>• введите код из смс, у вас ровно: <code>60</code></i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("отмена", callback_data=f"cancel_{session['order_id']}")]
                ])
            )
            await db.add_order_message(session["order_id"], msg.message_id)
            asyncio.create_task(start_timer(context, user_id, session["order_id"], "code", 60))
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
