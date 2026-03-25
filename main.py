import os
import logging
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from dotenv import load_dotenv
from flask import Flask, send_from_directory

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL", "https://slivera-production.up.railway.app")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__, static_folder='static')

@flask_app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@flask_app.route('/profile')
def profile():
    return send_from_directory('static', 'index.html')

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>👋 Добро пожаловать в MaxHub!</b>\n\n"
        "<i>Нажмите /profile</i>",
        parse_mode=ParseMode.HTML
    )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Открыть профиль", web_app={"url": f"{APP_URL}/profile"})
    ]])
    await update.message.reply_text(
        "<b>🌟 Ваш профиль MaxHub</b>\n\n"
        "<i>Нажмите кнопку</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_command))
    
    logger.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
