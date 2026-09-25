import asyncio
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = "ВСТАВЬ_ТОКЕН_БОТА_СЮДА"
ADMIN_ID = 123456789  # Ваш Telegram ID

# =========================
# БАЗА ДАННЫХ
# =========================

db = sqlite3.connect("projectghoul.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0
)
""")
db.commit()

# =========================
# BOT & FSM STATES
# =========================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_give = State()
    waiting_for_broadcast = State()
    waiting_for_ban = State()


def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="stats")
    kb.button(text="👥 Пользователи", callback_data="users")
    kb.button(text="🎁 Выдать бонус", callback_data="give")
    kb.button(text="📢 Рассылка", callback_data="broadcast")
    kb.button(text="🚫 Заблокировать", callback_data="ban")
    kb.adjust(2)
    return kb.as_markup()

# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    user = message.from_user

    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
    exists = cursor.fetchone()

    if not exists:
        cursor.execute(
            "INSERT INTO users (user_id, username) VALUES (?, ?)",
            (user.id, user.username or "")
        )
        db.commit()

    cursor.execute("SELECT banned FROM users WHERE user_id = ?", (user.id,))
    banned = cursor.fetchone()

    if banned and banned[0] == 1:
        await message.answer("🚫 Вы заблокированы.")
        return

    await message.answer(
        "👋 Добро пожаловать в ProjectGhoul!\n\n"
        "🎮 Здесь можно получать игровые бонусы."
    )

# =========================
# ADMIN PANEL
# =========================

@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа.")
        return

    await message.answer(
        "🛠 <b>Админ-панель ProjectGhoul</b>\n\nВыберите действие:",
        reply_markup=admin_menu(),
        parse_mode="HTML"
    )

# СТАТИСТИКА
@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE banned = 1")
    banned = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
    balance = cursor.fetchone()[0]

    await call.message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {total}\n"
        f"🚫 Заблокировано: {banned}\n"
        f"💰 Всего бонусов: {balance}",
        parse_mode="HTML"
    )
    await call.answer()

# ПОЛЬЗОВАТЕЛИ
@dp.callback_query(F.data == "users")
async def users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    cursor.execute("""
        SELECT user_id, username, balance, banned
        FROM users
        ORDER BY user_id DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()

    if not rows:
        await call.message.answer("Пользователей пока нет.")
        return

    text = "👥 <b>Последние пользователи:</b>\n\n"
    for user_id, username, balance, banned in rows:
        status = "🚫" if banned else "✅"
        text += (
            f"{status} <code>{user_id}</code> @{username or 'без_username'}\n"
            f"💰 Бонусов: {balance}\n\n"
        )

    await call.message.answer(text, parse_mode="HTML")
    await call.answer()

# =========================
# ДЕЙСТВИЯ АДМИНА (FSM)
# =========================

# 1. Выдача бонуса
@dp.callback_query(F.data == "give")
async def give_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return

    await state.set_state(AdminStates.waiting_for_give)
    await call.message.answer(
        "🎁 Отправь данные в формате:\n\n<code>ID количество</code>\n\nНапример:\n<code>123456789 100</code>",
        parse_mode="HTML"
    )
    await call.answer()

@dp.message(AdminStates.waiting_for_give)
async def process_give(message: Message, state: FSMContext):
    try:
        user_id, amount = map(int, message.text.split())

        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            await message.answer("❌ Пользователь не найден.")
            await state.clear()
            return

        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        db.commit()

        await message.answer(f"✅ Пользователю <code>{user_id}</code> выдано <b>{amount}</b> бонусов.", parse_mode="HTML")

        try:
            await bot.send_message(user_id, f"🎁 Вам начислено <b>{amount}</b> бонусов!", parse_mode="HTML")
        except Exception:
            pass

    except ValueError:
        await message.answer("❌ Неверный формат. Используй: <code>ID количество</code>", parse_mode="HTML")

    await state.clear()

# 2. Рассылка
@dp.callback_query(F.data == "broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return

    await state.set_state(AdminStates.waiting_for_broadcast)
    await call.message.answer("📢 Введите текст сообщения для рассылки всем пользователям:")
    await call.answer()

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    cursor.execute("SELECT user_id FROM users WHERE banned = 0")
    users_list = cursor.fetchall()

    sent = 0
    for (user_id,) in users_list:
        try:
            await bot.send_message(user_id, message.text)
            sent += 1
            await asyncio.sleep(0.05)  # Защита от лимитов Telegram API
        except Exception:
            pass

    await message.answer(f"✅ Рассылка завершена. Успешно доставлено: {sent} из {len(users_list)}")
    await state.clear()

# 3. Блокировка
@dp.callback_query(F.data == "ban")
async def ban_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return

    await state.set_state(AdminStates.waiting_for_ban)
    await call.message.answer("🚫 Отправьте Telegram ID пользователя для блокировки/разблокировки:")
    await call.answer()

@dp.message(AdminStates.waiting_for_ban)
async def process_ban(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())

        cursor.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if not row:
            await message.answer("❌ Пользователь с таким ID не найден.")
            await state.clear()
            return

        new_status = 0 if row[0] == 1 else 1
        cursor.execute("UPDATE users SET banned = ? WHERE user_id = ?", (new_status, user_id))
        db.commit()

        status_text = "заблокирован" if new_status == 1 else "разблокирован"
        await message.answer(f"✅ Пользователь <code>{user_id}</code> успешно {status_text}.", parse_mode="HTML")

    except ValueError:
        await message.answer("❌ ID должен быть числом.")

    await state.clear()

# =========================
# ЗАПУСК
# =========================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
