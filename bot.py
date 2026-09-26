import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# -------------------------------------------------------------------
# КОНФИГУРАЦИЯ
# -------------------------------------------------------------------
# Бот берёт токен из переменной окружения BOT_TOKEN или использует значение по умолчанию
BOT_TOKEN = os.getenv("BOT_TOKEN", "8877707155:AAGi6BMp6n09wQfRgLF6dxOyJ4P-4QVPkWo")

# Укажи здесь список Telegram ID администраторов
ADMIN_IDS = [6624873620, 987654321]  # Замени на реальные Telegram ID

# PORT для Render (Render автоматически передает порт через переменную окружения)
PORT = int(os.getenv("PORT", 10000))

# -------------------------------------------------------------------
# ИМИТАЦИЯ БАЗЫ ДАННЫХ (In-Memory)
# -------------------------------------------------------------------
# В реальном проекте используй SQLite / PostgreSQL (например, SQLAlchemy или Aiosqlite)
db = {
    "users": {},      # user_id: {"game_id": str, "balance": int, "referrer": int|None, "referrals_count": int, "is_banned": bool}
    "banned": set()   # Множество заблокированных user_id
}

def get_or_create_user(user_id: int, referrer_id: int = None):
    if user_id not in db["users"]:
        db["users"][user_id] = {
            "game_id": None,
            "balance": 0,
            "referrer": referrer_id,
            "referrals_count": 0,
            "is_banned": False
        }
        if referrer_id and referrer_id in db["users"]:
            db["users"][referrer_id]["referrals_count"] += 1
            db["users"][referrer_id]["balance"] += 100  # Бонус 100 за приглашенного друга
    return db["users"][user_id]

# -------------------------------------------------------------------
# FSM (Машина состояний)
# -------------------------------------------------------------------
class Form(StatesGroup):
    bind_game_account = State()
    admin_give_bonus_id = State()
    admin_give_bonus_amount = State()
    admin_ban_id = State()
    admin_unban_id = State()
    admin_broadcast_msg = State()

# -------------------------------------------------------------------
# ИНИЦИАЛИЗАЦИЯ БОТА И ДИСПЕТЧЕРА
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# -------------------------------------------------------------------
# КЛАВИАТУРЫ
# -------------------------------------------------------------------
def get_main_keyboard(user_id: int):
    buttons = [
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"), InlineKeyboardButton(text="🎮 Привязка аккаунта", callback_data="bind_game")],
        [InlineKeyboardButton(text="🔗 Рефералы", callback_data="ref_system"), InlineKeyboardButton(text="🏆 Рейтинг рефералов", callback_data="ref_leaderboard")]
    ]
    if user_id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Выдать бонус", callback_data="admin_give_bonus")],
        [InlineKeyboardButton(text="🚫 Забанить", callback_data="admin_ban"), InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])

# -------------------------------------------------------------------
# ОБРАБОТЧИКИ (HANDLERS)
# -------------------------------------------------------------------

# Проверка на бан
@dp.message.outer_middleware()
async def check_ban_middleware(handler, event, data):
    user = data.get("event_from_user")
    if user and user.id in db["banned"]:
        if isinstance(event, types.CallbackQuery):
            await event.answer("🚫 Вы заблокированы в боте!", show_alert=True)
        elif isinstance(event, types.Message):
            await event.answer("🚫 Вы заблокированы в боте!")
        return
    return await handler(event, data)

# Старт
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    args = message.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    
    if referrer_id == message.from_user.id:
        referrer_id = None

    get_or_create_user(message.from_user.id, referrer_id)
    
    bot_info = await bot.get_me()
    await message.answer(
        f"👋 Добро пожаловать в бота, {message.from_user.first_name}!\n\n"
        f"Используйте меню ниже для навигации:",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# Главное меню
@dp.callback_query(F.data == "main_menu")
async def process_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "👋 Главное меню:",
        reply_markup=get_main_keyboard(callback.from_user.id)
    )

# 👤 Профиль
@dp.callback_query(F.data == "profile")
async def process_profile(callback: CallbackQuery):
    user = get_or_create_user(callback.from_user.id)
    game_id = user["game_id"] if user["game_id"] else "Не привязан"
    
    text = (
        f"👤 **Профиль игрока**\n\n"
        f"🆔 Telegram ID: `{callback.from_user.id}`\n"
        f"🎮 Игровой ID: `{game_id}`\n"
        f"💰 Баланс: {user['balance']} бонусов\n"
        f"👥 Приглашено друзей: {user['referrals_count']}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# 🎮 Привязка аккаунта
@dp.callback_query(F.data == "bind_game")
async def process_bind_game(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.bind_game_account)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="main_menu")]])
    await callback.message.edit_text("🎮 Введите ваш игровой ID / Никнейм:", reply_markup=kb)

@dp.message(Form.bind_game_account)
async def process_game_account_input(message: types.Message, state: FSMContext):
    user = get_or_create_user(message.from_user.id)
    user["game_id"] = message.text
    await state.clear()
    await message.answer(
        f"✅ Игровой аккаунт `{message.text}` успешно привязан!",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# 🔗 Реферальная система
@dp.callback_query(F.data == "ref_system")
async def process_ref_system(callback: CallbackQuery):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={callback.from_user.id}"
    user = get_or_create_user(callback.from_user.id)
    
    text = (
        f"🔗 **Реферальная система**\n\n"
        f"Делитесь ссылкой и получайте бонусы за каждого приглашенного друга!\n\n"
        f"Ваша ссылка: `{ref_link}`\n"
        f"Приглашено человек: {user['referrals_count']}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# 🏆 Рейтинг рефералов
@dp.callback_query(F.data == "ref_leaderboard")
async def process_leaderboard(callback: CallbackQuery):
    sorted_users = sorted(db["users"].items(), key=lambda x: x[1]["referrals_count"], reverse=True)[:10]
    
    text = "🏆 **Топ-10 по рефералам:**\n\n"
    if not sorted_users:
        text += "Рейтинг пока пуст."
    else:
        for i, (u_id, u_data) in enumerate(sorted_users, 1):
            text += f"{i}. ID: `{u_id}` — {u_data['referrals_count']} рефералов\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# 🛠 Админ-панель
@dp.callback_query(F.data == "admin_panel")
async def process_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ У вас нет доступа к админ-панели!", show_alert=True)
    
    await callback.message.edit_text("🛠 **Панель Администратора**", parse_mode="Markdown", reply_markup=get_admin_keyboard())

# --- Админ функции ---

# Выдача бонуса
@dp.callback_query(F.data == "admin_give_bonus")
async def admin_give_bonus_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_bonus_id)
    await callback.message.edit_text("Введите Telegram ID пользователя для выдачи бонуса:")

@dp.message(Form.admin_give_bonus_id)
async def admin_give_bonus_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("ID должен состоять только из цифр!")
    await state.update_data(target_id=int(message.text))
    await state.set_state(Form.admin_give_bonus_amount)
    await message.answer("Введите сумму бонуса:")

@dp.message(Form.admin_give_bonus_amount)
async def admin_give_bonus_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Сумма должна быть числом!")
    
    data = await state.get_data()
    target_id = data["target_id"]
    amount = int(message.text)
    
    user = get_or_create_user(target_id)
    user["balance"] += amount
    await state.clear()
    
    # Уведомление игроку
    try:
        await bot.send_message(target_id, f"🎁 Администратор начислил вам **{amount}** бонусов!", parse_mode="Markdown")
    except Exception:
        pass

    await message.answer(f"✅ Пользователю `{target_id}` успешно начислено {amount} бонусов.", parse_mode="Markdown")

# Бан
@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_ban_id)
    await callback.message.edit_text("Введите Telegram ID пользователя для забана:")

@dp.message(Form.admin_ban_id)
async def admin_ban_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("ID должен быть числом!")
    
    target_id = int(message.text)
    db["banned"].add(target_id)
    await state.clear()
    await message.answer(f"🚫 Пользователь `{target_id}` заблокирован.", parse_mode="Markdown")

# Разбан
@dp.callback_query(F.data == "admin_unban")
async def admin_unban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_unban_id)
    await callback.message.edit_text("Введите Telegram ID пользователя для разбана:")

@dp.message(Form.admin_unban_id)
async def admin_unban_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("ID должен быть числом!")
    
    target_id = int(message.text)
    db["banned"].discard(target_id)
    await state.clear()
    await message.answer(f"✅ Пользователь `{target_id}` разблокирован.", parse_mode="Markdown")

# Рассылка
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_broadcast_msg)
    await callback.message.edit_text("Введите сообщение для рассылки всем пользователям:")

@dp.message(Form.admin_broadcast_msg)
async def admin_broadcast_process(message: types.Message, state: FSMContext):
    await state.clear()
    count = 0
    for u_id in list(db["users"].keys()):
        try:
            await bot.send_message(u_id, f"📢 **Сообщение от администрации:**\n\n{message.text}", parse_mode="Markdown")
            count += 1
            await asyncio.sleep(0.05)  # Защита от лимитов Telegram
        except Exception:
            pass
    await message.answer(f"📢 Рассылка завершена. Доставлено `{count}` пользователям.", parse_mode="Markdown")

# -------------------------------------------------------------------
# ВЕБ-СЕРВЕР ДЛЯ RENDER (Health Check)
# -------------------------------------------------------------------
async def handle_ping(request):
    return web.Response(text="Bot is live!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# -------------------------------------------------------------------
# ТОЧКА ВХОДА И ЗАПУСК
# -------------------------------------------------------------------
async def main():
    # Запуск веб-сервера для порта Render
    await start_web_server()
    logging.info(f"Веб-сервер запущен на порту {PORT}")
    
    # Запуск бота в режиме Long Polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
