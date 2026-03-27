import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Integer, Float, DateTime, inspect, select
from sqlalchemy.sql import text
import enum
from dotenv import load_dotenv
from aiocryptopay import CryptoPay

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
CRYPTOPAY_TOKEN = os.getenv("CRYPTOPAY_TOKEN")

if DATABASE_URL and "postgresql://" in DATABASE_URL and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

FIXED_AMOUNT = 3.5
SMS_TIMEOUT_MINUTES = 5
CRYPTOPAY_TESTNET = True  # Для теста используем testnet

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    WAITING_CODE = "waiting_code"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    ERROR = "error"


class WithdrawStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[str] = mapped_column(String(50), default="idle")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    phone: Mapped[str] = mapped_column(String(20))
    sms_code: Mapped[str] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default=OrderStatus.WAITING_CODE)
    amount: Mapped[float] = mapped_column(Float, default=3.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    timeout_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    request_message_id: Mapped[int] = mapped_column(BigInteger, nullable=True)


class Withdraw(Base):
    __tablename__ = "withdraws"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default=WithdrawStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def add_column_if_not_exists(connection, table_name, column_name, column_type):
            inspector = inspect(connection)
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            if column_name not in columns:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))

        await conn.run_sync(
            lambda c: add_column_if_not_exists(c, "orders", "timeout_at", "TIMESTAMP")
        )
        await conn.run_sync(
            lambda c: add_column_if_not_exists(c, "orders", "completed_at", "TIMESTAMP")
        )
        await conn.run_sync(
            lambda c: add_column_if_not_exists(c, "orders", "request_message_id", "BIGINT")
        )


def normalize_phone(phone: str) -> str:
    digits = ''.join(filter(str.isdigit, phone))
    
    if len(digits) == 10:
        return f"+7{digits}"
    if len(digits) == 11 and digits.startswith('8'):
        return f"+7{digits[1:]}"
    if len(digits) == 11 and digits.startswith('7'):
        return f"+{digits}"
    if len(digits) == 12 and digits.startswith('7'):
        return f"+{digits}"
    
    return phone


def format_order_tag(phone: str) -> str:
    digits = ''.join(filter(str.isdigit, phone))
    return f"#s{digits}"


def format_main_menu() -> str:
    return f"""⚡️FreeLine - сервис по приему СМС на нужные вам сервисы!

<blockquote>Наши преимущества:
• Полная автоматизация работы, без посредника.
• Автоматические выводы и моментальные зачисления.
• Прозрачный сервис и отзывчивая администрация.</blockquote>

Цена на сегодня: Ⓜ️ — 3.5$"""


def format_profile(user_id: int, balance: float) -> str:
    return f"""🔐 Ваш профиль:

• ID: {user_id}
• Банк: ${balance:.2f}"""


def format_message_phone_sent(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: ⚡️Номер передан контрагенту</i>"""


def format_message_phone_sent2(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: В течение 2-х минут вам придет СМС, отправьте его ответом на данное сообщение</i>"""


def format_message_waiting_sms(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: ⚡️ СМС в обработке, ожидаем ответа от контрагента</i>"""


def format_message_success(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: ✅ Активация успешна</i>"""


def format_message_balance_added(amount: float) -> str:
    return f"""<i>📤 На ваш баланс зачислено: {amount} USDT</i>"""


def format_message_rejected(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: 🚫 Активация отменена</i>

<i>Причина: Неверный код</i>"""


def format_message_timeout(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: 🚫 Активация отменена</i>

<i>Причина: Истекло фиксированное время для ввода СМС, попробуйте снова</i>"""


def format_message_error(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: 🚫 Активация отменена</i>

<i>Причина: Произошла ошибка, попробуйте сдать номер повторно</i>"""


def format_message_duplicate(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🖱️Обработка</b> {tag}

<i>Статус: 🚫 Активация отменена</i>

<i>Причина: Произошла ошибка, попробуйте сдать номер повторно</i>"""


def format_message_not_reply() -> str:
    return """<i>Для того, чтобы сервис считал ваш код, его нужно отправить в ответном сообщении</i>

<i>Например: Нажмите на сообщение и выберите «Ответить»</i>"""


def format_message_invalid_format() -> str:
    return """<i>Некорректный формат. Пожалуйста, отправьте номер в международном формате или используйте /leave для выхода</i>"""


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Начать работу", callback_data="start_work"),
            InlineKeyboardButton(text="Профиль", callback_data="profile")
        ]
    ])


def get_profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Вывод", callback_data="withdraw"),
            InlineKeyboardButton(text="Назад", callback_data="back_to_menu")
        ]
    ])


async def check_phone_exists(phone: str) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order).where(
                Order.phone == phone,
                Order.status == OrderStatus.COMPLETED
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def create_order(user_id: int, phone: str, username: str = None) -> Optional[Order]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            user = User(id=user_id, username=username, state="waiting_phone")
            session.add(user)
        else:
            if username:
                user.username = username
            user.state = "waiting_phone"

        order = Order(
            user_id=user_id,
            phone=phone,
            status=OrderStatus.WAITING_CODE,
            timeout_at=datetime.utcnow() + timedelta(minutes=SMS_TIMEOUT_MINUTES)
        )
        session.add(order)
        await session.commit()
        return order


async def get_active_order(user_id: int) -> Optional[Order]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order).where(
                Order.user_id == user_id,
                Order.status.in_([OrderStatus.WAITING_CODE, OrderStatus.VERIFYING])
            ).order_by(Order.created_at.desc())
        )
        return result.scalar_one_or_none()


async def update_order_status(order_id: int, status: OrderStatus, sms_code: str = None):
    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if order:
            order.status = status
            if sms_code:
                order.sms_code = sms_code
            if status in [OrderStatus.COMPLETED, OrderStatus.REJECTED, OrderStatus.TIMEOUT, OrderStatus.ERROR]:
                order.completed_at = datetime.utcnow()
            await session.commit()


async def update_order_message_id(order_id: int, message_id: int):
    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if order:
            order.request_message_id = message_id
            await session.commit()


async def add_balance(user_id: int, amount: float) -> float:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.balance += amount
            await session.commit()
            return user.balance
        return 0.0


async def get_user_balance(user_id: int) -> float:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        return user.balance if user else 0.0


async def set_user_state(user_id: int, state: str):
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.state = state
            await session.commit()


async def get_user_state(user_id: int) -> str:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        return user.state if user else "idle"


async def create_withdraw(user_id: int, amount: float) -> Optional[Withdraw]:
    async with AsyncSessionLocal() as session:
        withdraw = Withdraw(user_id=user_id, amount=amount)
        session.add(withdraw)
        await session.commit()
        return withdraw


async def update_withdraw_status(withdraw_id: int, status: WithdrawStatus):
    async with AsyncSessionLocal() as session:
        withdraw = await session.get(Withdraw, withdraw_id)
        if withdraw:
            withdraw.status = status
            withdraw.completed_at = datetime.utcnow()
            await session.commit()


async def deduct_balance(user_id: int, amount: float) -> bool:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user and user.balance >= amount:
            user.balance -= amount
            await session.commit()
            return True
        return False


async def send_delayed_message(chat_id: int, phone: str, order_id: int, message_type: str = "sms_request"):
    await asyncio.sleep(2)
    if message_type == "sms_request":
        msg = await bot.send_message(chat_id, format_message_phone_sent2(phone), parse_mode="HTML")
        await update_order_message_id(order_id, msg.message_id)
    elif message_type == "error":
        await bot.send_message(chat_id, format_message_error(phone), parse_mode="HTML")
    elif message_type == "duplicate_error":
        await bot.send_message(chat_id, format_message_duplicate(phone), parse_mode="HTML")


async def send_balance_message(chat_id: int, amount: float):
    await asyncio.sleep(2)
    await bot.send_message(chat_id, format_message_balance_added(amount), parse_mode="HTML")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    state = await get_user_state(user_id)
    
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            user = User(id=user_id, username=username, state="waiting_phone")
            session.add(user)
            await session.commit()
            await message.answer(
                "В следующем сообщении отправьте номер телефона\n\nДля завершения работы введите /leave",
                parse_mode="HTML"
            )
            return
    
    if state == "idle":
        await message.answer(
            format_main_menu(),
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await message.answer(
            "В следующем сообщении отправьте номер телефона\n\nДля завершения работы введите /leave",
            parse_mode="HTML"
        )


@dp.message(Command("leave"))
async def cmd_leave(message: types.Message):
    user_id = message.from_user.id
    
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.state = "idle"
            await session.commit()
    
    order = await get_active_order(user_id)
    if order and order.status == OrderStatus.WAITING_CODE:
        await update_order_status(order.id, OrderStatus.ERROR)
    
    await message.answer(
        format_main_menu(),
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()
    )


@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Использование: /add <сумма>")
            return
        
        amount = float(args[1])
        
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительной")
            return
        
        crypto = CryptoPay(token=CRYPTOPAY_TOKEN, testnet=CRYPTOPAY_TESTNET)
        
        invoice = await crypto.create_invoice(
            asset="USDT",
            amount=amount,
            description=f"Пополнение баланса приложения FreeLine на {amount} USDT"
        )
        
        await message.answer(
            f"💰 Создан инвойс на пополнение на {amount} USDT\n\n"
            f"Ссылка для оплаты: {invoice.bot_url}\n"
            f"ID инвойса: {invoice.invoice_id}"
        )
        
        await crypto.close()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "start_work")
async def start_work_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    await set_user_state(user_id, "waiting_phone")
    
    await callback.message.edit_text(
        "В следующем сообщении отправьте номер телефона\n\nДля завершения работы введите /leave",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "profile")
async def profile_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    balance = await get_user_balance(user_id)
    
    await callback.message.edit_text(
        format_profile(user_id, balance),
        parse_mode="HTML",
        reply_markup=get_profile_keyboard()
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        format_main_menu(),
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    balance = await get_user_balance(user_id)
    
    if balance <= 0:
        await callback.answer("❌ Недостаточно средств", show_alert=True)
        return
    
    crypto = CryptoPay(token=CRYPTOPAY_TOKEN, testnet=CRYPTOPAY_TESTNET)
    
    try:
        transfer = await crypto.transfer(
            user_id=user_id,
            asset="USDT",
            amount=balance,
            spend_id=f"withdraw_{user_id}_{int(datetime.utcnow().timestamp())}",
            comment="Вывод средств из сервиса FreeLine"
        )
        
        await deduct_balance(user_id, balance)
        
        await callback.message.edit_text(
            format_profile(user_id, 0),
            parse_mode="HTML",
            reply_markup=get_profile_keyboard()
        )
        
        await callback.answer(f"✅ Вывод {balance}$ выполнен", show_alert=True)
        
    except Exception as e:
        error_text = str(e).lower()
        
        if "insufficient" in error_text or "balance" in error_text or "not enough" in error_text:
            await callback.answer("❌ Казна закончилась, ожидайте пополнения! (≈20 минут)", show_alert=True)
        else:
            logger.error(f"Ошибка вывода: {e}")
            await callback.answer("❌ Ошибка выплаты, попробуйте позже", show_alert=True)
    
    await crypto.close()


@dp.message(F.text)
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    state = await get_user_state(user_id)
    is_reply = message.reply_to_message is not None

    if state != "waiting_phone":
        return

    if is_reply:
        order = await get_active_order(user_id)
        if not order:
            return
        
        if message.reply_to_message.message_id != order.request_message_id:
            await message.answer(format_message_invalid_format(), parse_mode="HTML")
            return

        code = message.text.strip()

        if not code.isdigit() or len(code) != 6:
            await message.answer(format_message_waiting_sms(order.phone), parse_mode="HTML")
            asyncio.create_task(send_delayed_message(message.chat.id, order.phone, order.id, "error"))
            await update_order_status(order.id, OrderStatus.ERROR)
            return

        await update_order_status(order.id, OrderStatus.VERIFYING, sms_code=code)
        await message.answer(format_message_waiting_sms(order.phone), parse_mode="HTML")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="ПРИНЯТЬ", callback_data=f"accept_{order.id}_{code}"),
                InlineKeyboardButton(text="ОТКЛОНИТЬ", callback_data=f"reject_{order.id}")
            ]
        ])

        await bot.send_message(
            ADMIN_ID,
            f"Код для заявки #{order.id}\nНомер: `{order.phone}`\nКод: `{code}`\nПользователь: @{message.from_user.username or user_id} (id: {user_id})",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    text = message.text.strip()
    digits = ''.join(filter(str.isdigit, text))
    
    if len(digits) == 6 and text.isdigit():
        await message.answer(format_message_not_reply(), parse_mode="HTML")
        return
    
    is_phone = False
    if len(digits) >= 10 and len(digits) <= 12:
        first_digit = digits[0]
        if first_digit in ['7', '8', '9']:
            is_phone = True
    
    if not is_phone:
        await message.answer(format_message_invalid_format(), parse_mode="HTML")
        return

    phone = normalize_phone(text)
    username = message.from_user.username

    if await check_phone_exists(phone):
        await message.answer(format_message_phone_sent(phone), parse_mode="HTML")
        asyncio.create_task(send_delayed_message(message.chat.id, phone, 0, "duplicate_error"))
        return

    order = await create_order(user_id, phone, username)

    await message.answer(format_message_phone_sent(phone), parse_mode="HTML")
    asyncio.create_task(send_delayed_message(message.chat.id, phone, order.id, "sms_request"))

    await bot.send_message(
        ADMIN_ID,
        f"Новая заявка #{order.id}\nНомер: `{phone}`\nПользователь: @{username or user_id} (id: {user_id})",
        parse_mode="HTML"
    )

    asyncio.create_task(check_timeout(order.id, phone, user_id, message.chat.id))


@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    data = callback.data

    if data.startswith("accept_"):
        _, order_id, code = data.split("_")
        order_id = int(order_id)

        async with AsyncSessionLocal() as session:
            order = await session.get(Order, order_id)
            if not order:
                await callback.answer("Заявка не найдена")
                return

            if order.sms_code != code:
                await callback.answer("Код не совпадает")
                return

            if order.status != OrderStatus.VERIFYING:
                await callback.answer("Заявка уже обработана")
                return

            new_balance = await add_balance(order.user_id, FIXED_AMOUNT)
            await update_order_status(order_id, OrderStatus.COMPLETED)

            await bot.send_message(
                order.user_id,
                format_message_success(order.phone),
                parse_mode="HTML"
            )
            
            asyncio.create_task(send_balance_message(order.user_id, FIXED_AMOUNT))

            await callback.message.edit_text(f"✅ Заявка #{order_id} принята. Начислено {FIXED_AMOUNT}$")
            await callback.answer("Принято")

    elif data.startswith("reject_"):
        _, order_id = data.split("_")
        order_id = int(order_id)

        async with AsyncSessionLocal() as session:
            order = await session.get(Order, order_id)
            if not order:
                await callback.answer("Заявка не найдена")
                return

            if order.status != OrderStatus.VERIFYING:
                await callback.answer("Заявка уже обработана")
                return

            await update_order_status(order_id, OrderStatus.REJECTED)

            await bot.send_message(
                order.user_id,
                format_message_rejected(order.phone),
                parse_mode="HTML"
            )

            await callback.message.edit_text(f"❌ Заявка #{order_id} отклонена")
            await callback.answer("Отклонено")

    await callback.answer()


async def check_timeout(order_id: int, phone: str, user_id: int, chat_id: int):
    await asyncio.sleep(SMS_TIMEOUT_MINUTES * 60)

    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if order and order.status == OrderStatus.WAITING_CODE:
            order.status = OrderStatus.TIMEOUT
            order.completed_at = datetime.utcnow()
            await session.commit()

            await bot.send_message(
                user_id,
                format_message_timeout(phone),
                parse_mode="HTML"
            )


async def main():
    await init_db()
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
