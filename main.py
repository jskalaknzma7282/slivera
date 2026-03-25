import os
import logging
import threading
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
import asyncpg

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
APP_URL = os.getenv("APP_URL", "https://slivera-production.up.railway.app")
PORT = int(os.getenv("PORT", 8080))
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============== DATABASE ===============
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
            logger.info("Tables created")

    async def get_user(self, user_id: int, username: str = None):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not row:
                await conn.execute(
                    "INSERT INTO users (user_id, username, balance) VALUES ($1, $2, 10)",
                    user_id, username or ""
                )
                return {"user_id": user_id, "username": username, "balance": 10}
            return dict(row)

    async def update_balance(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                amount, user_id
            )

    async def set_balance(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                amount, user_id
            )

db = Database()

# =============== FLASK ===============
flask_app = Flask(__name__, static_folder='static')

@flask_app.route('/')
def index():
    return send_from_directory('static', 'blackjack.html')

@flask_app.route('/blackjack')
def blackjack():
    return send_from_directory('static', 'blackjack.html')

@flask_app.route('/api/balance')
def api_balance():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'no user_id'}), 400
    
    async def get_balance():
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT balance FROM users WHERE user_id = $1", user_id)
            if row:
                return float(row['balance'])
            await conn.execute(
                "INSERT INTO users (user_id, username, balance) VALUES ($1, $2, 10)",
                user_id, ""
            )
            return 10.0
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    balance = loop.run_until_complete(get_balance())
    return jsonify({'balance': balance})

@flask_app.route('/api/update_balance', methods=['POST'])
def update_balance_api():
    data = request.get_json()
    user_id = data.get('user_id')
    new_balance = data.get('balance')
    
    async def update():
        async with db.pool.acquire() as conn:
            await conn.execute("UPDATE users SET balance = $1 WHERE user_id = $2", new_balance, user_id)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(update())
    return jsonify({'ok': True})

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# =============== TELEGRAM BOT ===============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    user = await db.get_user(user_id, username)
    
    await update.message.reply_text(
        "<b>🎲 MaxHub Black Jack</b>\n\n"
        f"<i>Ваш баланс: <code>{user['balance']:.2f}</code> USDT</i>\n\n"
        "<i>Доступные команды:</i>\n"
        "<code>/blackjack</code> - Играть\n"
        "<code>/bal</code> - Баланс\n"
        "<code>/addmoney user_id сумма</code> - Пополнить (админ)",
        parse_mode=ParseMode.HTML
    )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    await update.message.reply_text(
        f"<b>💳 Ваш баланс</b>\n\n"
        f"<i><code>{user['balance']:.2f}</code> USDT</i>",
        parse_mode=ParseMode.HTML
    )

async def addmoney_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Только для админа")
        return
    
    if not context.args or len(context.args) != 2:
        await update.message.reply_text(
            "<b>❌ Ошибка</b>\n\n"
            "<i>Используйте: /addmoney user_id сумма</i>\n"
            "Пример: /addmoney 123456789 50",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        target_user = int(context.args[0])
        amount = float(context.args[1])
        
        await db.update_balance(target_user, amount)
        await update.message.reply_text(
            f"<b>✅ Успешно!</b>\n\n"
            f"<i>Пользователю <code>{target_user}</code> начислено <code>{amount:.2f}</code> USDT</i>",
            parse_mode=ParseMode.HTML
        )
        
        try:
            await context.bot.send_message(
                target_user,
                f"<b>💰 Пополнение баланса</b>\n\n"
                f"<i>Вам начислено <code>{amount:.2f}</code> USDT</i>",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
            
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def blackjack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎰 Играть в Black Jack", web_app={"url": f"{APP_URL}/blackjack"})
    ]])
    await update.message.reply_text(
        "<b>🎲 Black Jack (21)</b>\n\n"
        "<i>Сделайте ставку и попробуйте обыграть дилера!</i>\n"
        "💰 Баланс: /bal",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init())
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("blackjack", blackjack_command))
    app.add_handler(CommandHandler("bal", balance_command))
    app.add_handler(CommandHandler("addmoney", addmoney_command))
    
    logger.info("BlackJack бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
