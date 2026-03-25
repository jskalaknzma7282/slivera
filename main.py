import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_LINK = os.getenv("CHANNEL_LINK")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    
    logger.info(f"Callback received: {data} from {user_id}")
    await query.answer()
    
    if data == "new_order" and user_id == ADMIN_ID:
        logger.info("Creating new order")
        await publish_new_order(context)
        await query.message.delete()
        return
    
    if data == "take_order":
        logger.info(f"User {user_id} accepted order")
        await query.message.delete()
        await context.bot.send_message(
            user_id,
            "✅ Вы приняли заявку!\nВведите номер телефона:",
            parse_mode=ParseMode.HTML
        )
        return

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)
    
    logger.info("Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
