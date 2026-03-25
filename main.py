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
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    phone TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    confirmed_at TIMESTAMP,
                    message_id BIGINT,
                    admin_message_id BIGINT,
                    channel_message_id BIGINT
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
            logger.info("Tables created")

    async def get_user(self, user_id: int, username: str = None):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not row:
                await conn.execute(
                    "INSERT INTO users (user_id, username, balance) VALUES ($1, $2, 0)",
                    user_id, username or ""
                )
                return {"user_id": user_id, "username": username, "balance": 0}
            return dict(row)

    async def update_balance(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                amount, user_id
            )
            logger.info(f"Updated balance for user {user_id}: +{amount}")

    async def set_balance(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                amount, user_id
            )
            logger.info(f"Set balance for user {user_id}: {amount}")

db = Database()

# =============== FLASK ===============
flask_app = Flask(__name__, static_folder='static')

@flask_app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@flask_app.route('/profile')
def profile():
    return send_from_directory('static', 'index.html')

@flask_app.route('/blackjack')
def blackjack():
    return send_from_directory('static', 'blackjack.html')

@flask_app.route('/api/profile')
def api_profile():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'no user_id'}), 400
    
    async def get_data():
        user = await db.get_user(user_id)
        async with db.pool.acquire() as conn:
            total_orders = await conn.fetchval(
                "SELECT COUNT(*) FROM orders WHERE user_id = $1 AND status IN ('confirmed', 'funded')",
                user_id
            )
            today_orders = await conn.fetchval(
                "SELECT COUNT(*) FROM orders WHERE user_id = $1 AND status IN ('confirmed', 'funded') AND created_at::date = CURRENT_DATE",
                user_id
            )
        rank = "🟢 Новичок"
        if total_orders >= 100: rank = "👑 Легенда"
        elif total_orders >= 50: rank = "💎 Мастер"
        elif total_orders >= 25: rank = "⭐ Профи"
        elif total_orders >= 10: rank = "🟡 Опытный"
        return {'balance': float(user['balance']), 'total_orders': total_orders, 'today_orders': today_orders, 'rank': rank}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    data = loop.run_until_complete(get_data())
    return jsonify(data)

@flask_app.route('/api/balance')
def api_balance():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'no user_id'}), 400
    
    async def get_balance():
        user = await db.get_user(user_id)
        return float(user['balance'])
    
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
        await db.set_balance(user_id, new_balance)
    
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
    
    # Если баланс 0, выдаем 10 USDT
    if user["balance"] == 0:
        await db.update_balance(user_id, 10)
        await update.message.reply_text(
            "<b>🎁 Вам начислено 10 USDT!</b>\n\n"
            "<i>Используйте /profile и /blackjack</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    await update.message.reply_text(
        "<b>👋 Добро пожаловать в MaxHub!</b>\n\n"
        "<i>Доступные команды:</i>\n"
        "<code>/profile</code> - Ваш профиль\n"
        "<code>/blackjack</code> - Играть в Black Jack\n"
        "<code>/addmoney 100</code> - Пополнить баланс (админ)\n"
        "<code>/bal</code> - Проверить баланс",
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
    
    # Только для админа
    if user_id != int(os.getenv("ADMIN_ID", 0)):
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
        
        # Отправляем уведомление пользователю
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

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Открыть профиль", web_app={"url": f"{APP_URL}/profile"})
    ]])
    await update.message.reply_text(
        "<b>🌟 Ваш профиль MaxHub</b>\n\n"
        "<i>Нажмите кнопку, чтобы открыть красивый профиль</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

async def blackjack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎰 Играть в Black Jack", web_app={"url": f"{APP_URL}/blackjack"})
    ]])
    await update.message.reply_text(
        "<b>🎲 Black Jack (21)</b>\n\n"
        "<i>Сделайте ставку и попробуйте обыграть дилера!</i>\n"
        "💰 Баланс можно проверить командой /bal",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

def main():
    # Запускаем Flask
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Инициализируем БД
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init())
    
    # Запускаем бота
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("blackjack", blackjack_command))
    app.add_handler(CommandHandler("bal", balance_command))
    app.add_handler(CommandHandler("addmoney", addmoney_command))
    
    logger.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
