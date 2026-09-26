import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# -------------------------------------------------------------------
# КОНФИГУРАЦИЯ
# -------------------------------------------------------------------
# Новый сброшенный токен
BOT_TOKEN = os.getenv("BOT_TOKEN", "8877707155:AAGnObMOE9f7ejg6g3di2dpb-1Mrmje9o3k")

# Список Telegram ID администраторов (Ты — главный)
ADMIN_IDS = [6624873620]

# PORT для Render
PORT = int(os.getenv("PORT", 10000))

# -------------------------------------------------------------------
# БАЗА ДАННЫХ (In-Memory)
# -------------------------------------------------------------------
db = {
    "users": {},      # user_id: {"game_id": str, "nickname": str, "balance": int, "referrer": int|None, "referrals_count": int}
    "banned": set()   # Множество заблокированных user_id
}

def get_or_create_user(user_id: int, referrer_id: int = None):
    if user_id not in db["users"]:
        db["users"][user_id] = {
            "game_id": None,
            "nickname": None,
            "balance": 0,
            "referrer": referrer_id,
            "referrals_count": 0
        }
        if referrer_id and referrer_id in db["users"]:
            db["users"][referrer_id]["referrals_count"] += 1
            db["users"][referrer_id]["balance"] += 100
    return db["users"][user_id]

# -------------------------------------------------------------------
# FSM (Машина состояний)
# -------------------------------------------------------------------
class Form(StatesGroup):
    # Привязка аккаунта в 2 шага
    bind_game_id = State()
    bind_nickname = State()
    
    # Админка
    admin_give_bonus_id = State()
    admin_give_bonus_amount = State()
    admin_ban_id = State()
    admin_unban_id = State()
    admin_add_admin_id = State()
    admin_remove_admin_id = State()
    admin_broadcast_msg = State()

# -------------------------------------------------------------------
# ИНИЦИАЛИЗАЦИЯ
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# -------------------------------------------------------------------
# КЛАВИАТУРЫ
# -------------------------------------------------------------------
def get_main_keyboard(user_id: int):
    kb_buttons = [
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎮 Привязка аккаунта")],
        [KeyboardButton(text="🔗 Рефералы"), KeyboardButton(text="🏆 Топ рефералов")]
    ]
    if user_id in ADMIN_IDS:
        kb_buttons.append([KeyboardButton(text="🛠 Админ-панель")])
    
    return ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True)

def get_admin_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Выдать бонус", callback_data="admin_give_bonus")],
        [InlineKeyboardButton(text="🚫 Забанить", callback_data="admin_ban"), InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="➕ Добавить админа", callback_data="admin_add_admin"), InlineKeyboardButton(text="➖ Удалить админа", callback_data="admin_remove_admin")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])

# -------------------------------------------------------------------
# ПРОВЕРКА НА БАН
# -------------------------------------------------------------------
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

# -------------------------------------------------------------------
# ОБРАБОТЧИКИ (HANDLERS)
# -------------------------------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    
    if referrer_id == message.from_user.id:
        referrer_id = None

    get_or_create_user(message.from_user.id, referrer_id)
    
    await message.answer(
        f"👋 Добро пожаловать, {message.from_user.first_name}!\n\n"
        f"Используйте кнопки меню ниже:",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# 👤 Профиль
@dp.message(F.text == "👤 Профиль")
async def process_profile(message: types.Message):
    user = get_or_create_user(message.from_user.id)
    game_id = user["game_id"] if user["game_id"] else "Не указан"
    nickname = user["nickname"] if user["nickname"] else "Не указан"
    
    text = (
        f"👤 **Профиль игрока**\n\n"
        f"🆔 Telegram ID: `{message.from_user.id}`\n"
        f"🎮 Игровой ID: `{game_id}`\n"
        f"🏷 Никнейм: `{nickname}`\n"
        f"💰 Баланс: {user['balance']} бонусов\n"
        f"👥 Приглашено друзей: {user['referrals_count']}"
    )
    await message.answer(text, parse_mode="Markdown")

# 🎮 Привязка аккаунта (Шаг 1: Ввод ID)
@dp.message(F.text == "🎮 Привязка аккаунта")
async def process_bind_start(message: types.Message, state: FSMContext):
    await state.set_state(Form.bind_game_id)
    await message.answer("🎮 Шаг 1/2: Введите ваш **Игровой ID**:", parse_mode="Markdown")

# 🎮 Привязка аккаунта (Шаг 2: Ввод Никнейма)
@dp.message(Form.bind_game_id)
async def process_bind_game_id(message: types.Message, state: FSMContext):
    await state.update_data(game_id=message.text)
    await state.set_state(Form.bind_nickname)
    await message.answer("🏷 Шаг 2/2: Введите ваш **Игровой Никнейм**:", parse_mode="Markdown")

@dp.message(Form.bind_nickname)
async def process_bind_nickname(message: types.Message, state: FSMContext):
    data = await state.get_data()
    game_id = data["game_id"]
    nickname = message.text
    
    user = get_or_create_user(message.from_user.id)
    user["game_id"] = game_id
    user["nickname"] = nickname
    
    await state.clear()
    await message.answer(
        f"✅ Аккаунт успешно привязан!\n\n"
        f"🆔 Игровой ID: `{game_id}`\n"
        f"🏷 Никнейм: `{nickname}`",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# 🔗 Реферальная система
@dp.message(F.text == "🔗 Рефералы")
async def process_ref_system(message: types.Message):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    user = get_or_create_user(message.from_user.id)
    
    text = (
        f"🔗 **Реферальная система**\n\n"
        f"Ваша пригласительная ссылка:\n`{ref_link}`\n\n"
        f"👥 Вы пригласили: {user['referrals_count']} чел."
    )
    await message.answer(text, parse_mode="Markdown")

# 🏆 Топ рефералов
@dp.message(F.text == "🏆 Топ рефералов")
async def process_leaderboard(message: types.Message):
    sorted_users = sorted(db["users"].items(), key=lambda x: x[1]["referrals_count"], reverse=True)[:10]
    
    text = "🏆 **Топ-10 по рефералам:**\n\n"
    if not sorted_users:
        text += "Рейтинг пока пуст."
    else:
        for i, (u_id, u_data) in enumerate(sorted_users, 1):
            text += f"{i}. ID: `{u_id}` — {u_data['referrals_count']} реф.\n"

    await message.answer(text, parse_mode="Markdown")

# 🛠 Админ-панель
@dp.message(F.text == "🛠 Админ-панель")
async def process_admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет доступа!")
    
    await message.answer(
        "🛠 **Панель Администратора**",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard()
    )

# -------------------------------------------------------------------
# АДМИН-ФУНКЦИИ
# -------------------------------------------------------------------

# Выдача бонуса
@dp.callback_query(F.data == "admin_give_bonus")
async def admin_give_bonus_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_bonus_id)
    await callback.message.answer("Введите Telegram ID пользователя:")
    await callback.answer()

@dp.message(Form.admin_give_bonus_id)
async def admin_give_bonus_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("ID должен состоять только из цифр!")
    await state.update_data(target_id=int(message.text))
    await state.set_state(Form.admin_give_bonus_amount)
    await message.answer("Введите количество бонусов:")

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
    
    try:
        await bot.send_message(target_id, f"🎁 Администратор начислил вам **{amount}** бонусов!", parse_mode="Markdown")
    except Exception:
        pass

    await message.answer(f"✅ Пользователю `{target_id}` начислено {amount} бонусов.", parse_mode="Markdown")

# Бан
@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_ban_id)
    await callback.message.answer("Введите Telegram ID для забана:")
    await callback.answer()

@dp.message(Form.admin_ban_id)
async def admin_ban_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("ID должен состоять из цифр!")
    target_id = int(message.text)
    db["banned"].add(target_id)
    await state.clear()
    await message.answer(f"🚫 Пользователь `{target_id}` заблокирован.", parse_mode="Markdown")

# Разбан
@dp.callback_query(F.data == "admin_unban")
async def admin_unban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_unban_id)
    await callback.message.answer("Введите Telegram ID для разбана:")
    await callback.answer()

@dp.message(Form.admin_unban_id)
async def admin_unban_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("ID должен состоять из цифр!")
    target_id = int(message.text)
    db["banned"].discard(target_id)
    await state.clear()
    await message.answer(f"✅ Пользователь `{target_id}` разблокирован.", parse_mode="Markdown")

# Добавить админа
@dp.callback_query(F.data == "admin_add_admin")
async def admin_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_add_admin_id)
    await callback.message.answer("Введите Telegram ID нового администратора:")
    await callback.answer()

@dp.message(Form.admin_add_admin_id)
async def admin_add_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("ID должен состоять из цифр!")
    new_admin = int(message.text)
    if new_admin not in ADMIN_IDS:
        ADMIN_IDS.append(new_admin)
    await state.clear()
    await message.answer(f"👑 Пользователь `{new_admin}` добавлен в список администраторов!", parse_mode="Markdown")

# Удалить админа
@dp.callback_query(F.data == "admin_remove_admin")
async def admin_remove_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_remove_admin_id)
    await callback.message.answer("Введите Telegram ID админа для удаления:")
    await callback.answer()

@dp.message(Form.admin_remove_admin_id)
async def admin_remove_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("ID должен состоять из цифр!")
    rem_admin = int(message.text)
    if rem_admin == 6624873620:
        await state.clear()
        return await message.answer("⛔ Вы не можете удалить самого себя!")
    
    if rem_admin in ADMIN_IDS:
        ADMIN_IDS.remove(rem_admin)
        await message.answer(f"🗑 Пользователь `{rem_admin}` удален из админов.", parse_mode="Markdown")
    else:
        await message.answer("Этот пользователь не является админом.")
    await state.clear()

# Рассылка
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_broadcast_msg)
    await callback.message.answer("Введите текст сообщения для рассылки:")
    await callback.answer()

@dp.message(Form.admin_broadcast_msg)
async def admin_broadcast_process(message: types.Message, state: FSMContext):
    await state.clear()
    count = 0
    for u_id in list(db["users"].keys()):
        try:
            await bot.send_message(u_id, f"📢 **Сообщение от администрации:**\n\n{message.text}", parse_mode="Markdown")
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"📢 Рассылка завершена. Доставлено `{count}` пользователям.", parse_mode="Markdown")

# -------------------------------------------------------------------
# ВЕБ-СЕРВЕР ДЛЯ RENDER
# -------------------------------------------------------------------
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# -------------------------------------------------------------------
# ЗАПУСК
# -------------------------------------------------------------------
async def main():
    await start_web_server()
    logging.info(f"Веб-сервер запущен на порту {PORT}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
