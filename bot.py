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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8877707155:AAGnObMOE9f7ejg6g3di2dpb-1Mrmje9o3k")
ADMIN_IDS = [6624873620]
PORT = int(os.getenv("PORT", 10000))

# -------------------------------------------------------------------
# БАЗА ДАННЫХ И НАСТРОЙКИ (In-Memory)
# -------------------------------------------------------------------
config = {
    "ref_reward": 500,       # Награда за реферала в GOLD
    "min_withdraw": 5000     # Минималка на вывод в GOLD
}

db = {
    "users": {},               # user_id: {"game_id": str, "nickname": str, "balance": int, "referrer": int|None, "referrals_count": int}
    "banned": set(),           # Множество заблокированных user_id
    "withdraw_requests": {},   # req_id: {"user_id": int, "amount": int, "photo_id": str}
    "promo_codes": {}          # code_name: {"reward": int, "activations": int, "used_by": set(user_ids)}
}

request_counter = 0

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
            db["users"][referrer_id]["balance"] += config["ref_reward"]
    return db["users"][user_id]

# -------------------------------------------------------------------
# FSM (Машина состояний)
# -------------------------------------------------------------------
class Form(StatesGroup):
    # Привязка
    bind_game_id = State()
    bind_nickname = State()
    
    # Вывод
    withdraw_amount = State()
    withdraw_photo = State()
    
    # Активация промокода
    use_promo = State()
    
    # Админка
    admin_give_bonus_id = State()
    admin_give_bonus_amount = State()
    admin_ban_id = State()
    admin_unban_id = State()
    admin_add_admin_id = State()
    admin_remove_admin_id = State()
    admin_broadcast_msg = State()
    admin_set_ref_reward = State()
    admin_set_min_withdraw = State()
    
    # Создание промокода
    admin_promo_name = State()
    admin_promo_reward = State()
    admin_promo_activations = State()

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
        [InlineKeyboardButton(text="📥 Заявки на вывод", callback_data="admin_requests")],
        [InlineKeyboardButton(text="🎟 Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="🎁 Выдать GOLD", callback_data="admin_give_bonus")],
        [InlineKeyboardButton(text="⚙️ Награда за рефа", callback_data="admin_set_ref"), InlineKeyboardButton(text="⚙️ Мин. вывод", callback_data="admin_set_min")],
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
        f"💰 Баланс: **{user['balance']} GOLD**\n"
        f"👥 Приглашено рефералов: **{user['referrals_count']}**"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести GOLD", callback_data="user_withdraw")],
        [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="user_promo")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

# 🎮 Привязка аккаунта
@dp.message(F.text == "🎮 Привязка аккаунта")
async def process_bind_start(message: types.Message, state: FSMContext):
    await state.set_state(Form.bind_game_id)
    await message.answer("🎮 Шаг 1/2: Введите ваш **Игровой ID**:", parse_mode="Markdown")

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

# 🎟 Активация промокода пользователем
@dp.callback_query(F.data == "user_promo")
async def user_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.use_promo)
    await callback.message.answer("🎟 Введите промокод:")
    await callback.answer()

@dp.message(Form.use_promo)
async def user_promo_process(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    await state.clear()

    if code not in db["promo_codes"]:
        return await message.answer("❌ Такого промокода не существует!")

    promo = db["promo_codes"][code]

    if user_id in promo["used_by"]:
        return await message.answer("⚠️ Вы уже активировали этот промокод!")

    if promo["activations"] <= 0:
        return await message.answer("❌ Активации данного промокода закончились!")

    # Применяем промокод
    promo["activations"] -= 1
    promo["used_by"].add(user_id)
    
    user = get_or_create_user(user_id)
    user["balance"] += promo["reward"]

    await message.answer(f"🎉 Промокод `{code}` успешно активирован!\nВам зачислено **{promo['reward']} GOLD**.", parse_mode="Markdown")

# 💸 Процесс Вывода средств
@dp.callback_query(F.data == "user_withdraw")
async def withdraw_start(callback: CallbackQuery, state: FSMContext):
    user = get_or_create_user(callback.from_user.id)
    
    if not user["game_id"] or not user["nickname"]:
        await callback.answer("⚠️ Сначала привяжите игровой аккаунт!", show_alert=True)
        return

    if user["balance"] < config["min_withdraw"]:
        await callback.answer(f"⚠️ Минимальная сумма вывода — {config['min_withdraw']} GOLD!", show_alert=True)
        return

    await state.set_state(Form.withdraw_amount)
    await callback.message.answer(f"💰 Укажите сумму для вывода в GOLD (Доступно: {user['balance']} GOLD, Мин: {config['min_withdraw']}):")
    await callback.answer()

@dp.message(Form.withdraw_amount)
async def withdraw_amount_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введите число!")
    
    amount = int(message.text)
    user = get_or_create_user(message.from_user.id)
    
    if amount < config["min_withdraw"]:
        return await message.answer(f"❌ Минимальная сумма вывода: {config['min_withdraw']} GOLD!")
    
    if amount > user["balance"]:
        return await message.answer("❌ У вас недостаточно GOLD на балансе!")
    
    await state.update_data(withdraw_amount=amount)
    await state.set_state(Form.withdraw_photo)
    await message.answer("📸 Сделайте скриншот выставленного скина на рынке и **отправьте картинкой** сюда:")

@dp.message(Form.withdraw_photo, F.photo)
async def withdraw_photo_process(message: types.Message, state: FSMContext):
    global request_counter
    data = await state.get_data()
    amount = data["withdraw_amount"]
    
    user = get_or_create_user(message.from_user.id)
    user["balance"] -= amount
    
    request_counter += 1
    req_id = request_counter
    photo_id = message.photo[-1].file_id
    
    db["withdraw_requests"][req_id] = {
        "user_id": message.from_user.id,
        "amount": amount,
        "photo_id": photo_id
    }
    
    await state.clear()
    await message.answer(f"✅ Заявка #{req_id} на вывод **{amount} GOLD** отправлена администраторам!", parse_mode="Markdown")

# 🔗 Рефералы
@dp.message(F.text == "🔗 Рефералы")
async def process_ref_system(message: types.Message):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    user = get_or_create_user(message.from_user.id)
    
    text = (
        f"🔗 **Реферальная система**\n\n"
        f"За каждого приглашенного друга вы получаете **{config['ref_reward']} GOLD**!\n\n"
        f"Ваша пригласительная ссылка:\n`{ref_link}`\n\n"
        f"👥 Вы пригласили: **{user['referrals_count']}** чел."
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
    
    text = (
        f"🛠 **Панель Администратора**\n\n"
        f"⚙️ Текущая награда за реферала: **{config['ref_reward']} GOLD**\n"
        f"⚙️ Минимальная сумма вывода: **{config['min_withdraw']} GOLD**"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

# -------------------------------------------------------------------
# АДМИН-ФУНКЦИИ & ПРОМОКОДЫ
# -------------------------------------------------------------------

# Создание промокода
@dp.callback_query(F.data == "admin_create_promo")
async def admin_promo_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_promo_name)
    await callback.message.answer("🎟 Введите **название** нового промокода (например: `BONUS2026`):", parse_mode="Markdown")
    await callback.answer()

@dp.message(Form.admin_promo_name)
async def admin_promo_name_process(message: types.Message, state: FSMContext):
    code = message.text.strip()
    await state.update_data(promo_name=code)
    await state.set_state(Form.admin_promo_reward)
    await message.answer(f"💰 Введите сколько **GOLD** будет давать промокод `{code}`:", parse_mode="Markdown")

@dp.message(Form.admin_promo_reward)
async def admin_promo_reward_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите число!")
    await state.update_data(promo_reward=int(message.text))
    await state.set_state(Form.admin_promo_activations)
    await message.answer("🔢 Введите **количество активаций** (скольким людям доступен промокод):")

@dp.message(Form.admin_promo_activations)
async def admin_promo_activations_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите число!")
    
    data = await state.get_data()
    code = data["promo_name"]
    reward = data["promo_reward"]
    activations = int(message.text)

    db["promo_codes"][code] = {
        "reward": reward,
        "activations": activations,
        "used_by": set()
    }
    
    await state.clear()
    await message.answer(
        f"✅ **Промокод успешно создан!**\n\n"
        f"🎟 Название: `{code}`\n"
        f"💰 Награда: **{reward} GOLD**\n"
        f"👥 Кол-во активаций: **{activations}**",
        parse_mode="Markdown"
    )

# Просмотр заявок на вывод
@dp.callback_query(F.data == "admin_requests")
async def admin_requests_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    
    if not db["withdraw_requests"]:
        await callback.answer("📥 Активных заявок на вывод нет!", show_alert=True)
        return
    
    await callback.answer()
    for req_id, req_data in list(db["withdraw_requests"].items()):
        u_id = req_data["user_id"]
        u_info = db["users"].get(u_id, {})
        
        caption = (
            f"📥 **Заявка на вывод #{req_id}**\n\n"
            f"👤 Telegram ID: `{u_id}`\n"
            f"🎮 Игровой ID: `{u_info.get('game_id', 'Не указан')}`\n"
            f"🏷 Никнейм: `{u_info.get('nickname', 'Не указан')}`\n"
            f"💰 Сумма: **{req_data['amount']} GOLD**"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Купил", callback_data=f"buy_ok_{req_id}"),
             InlineKeyboardButton(text="❌ Отказать", callback_data=f"buy_cancel_{req_id}")]
        ])
        
        await callback.message.answer_photo(photo=req_data["photo_id"], caption=caption, parse_mode="Markdown", reply_markup=kb)

# Подтвердить покупку
@dp.callback_query(F.data.startswith("buy_ok_"))
async def process_buy_ok(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    req_id = int(callback.data.split("_")[2])
    
    if req_id in db["withdraw_requests"]:
        req_data = db["withdraw_requests"].pop(req_id)
        try:
            await bot.send_message(req_data["user_id"], f"✅ Ваша заявка на вывод **{req_data['amount']} GOLD** успешно выполнена (скин куплен)!", parse_mode="Markdown")
        except Exception:
            pass
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ **СТАТУС: КУПЛЕНО**")
    await callback.answer("Успешно!")

# Отклонить покупку
@dp.callback_query(F.data.startswith("buy_cancel_"))
async def process_buy_cancel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    req_id = int(callback.data.split("_")[2])
    
    if req_id in db["withdraw_requests"]:
        req_data = db["withdraw_requests"].pop(req_id)
        db["users"][req_data["user_id"]]["balance"] += req_data["amount"]
        try:
            await bot.send_message(req_data["user_id"], f"❌ Ваша заявка на вывод **{req_data['amount']} GOLD** была отклонена. GOLD возвращены на баланс.", parse_mode="Markdown")
        except Exception:
            pass
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ **СТАТУС: ОТКЛОНЕНО (GOLD возвращены)**")
    await callback.answer("Заявка отклонена!")

# Изменение настроек
@dp.callback_query(F.data == "admin_set_ref")
async def admin_set_ref(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_set_ref_reward)
    await callback.message.answer("Укажите новую награду за 1 реферала (в GOLD):")
    await callback.answer()

@dp.message(Form.admin_set_ref_reward)
async def admin_set_ref_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите число!")
    config["ref_reward"] = int(message.text)
    await state.clear()
    await message.answer(f"✅ Награда за реферала изменена на **{config['ref_reward']} GOLD**.", parse_mode="Markdown")

@dp.callback_query(F.data == "admin_set_min")
async def admin_set_min(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_set_min_withdraw)
    await callback.message.answer("Укажите новую минимальную сумму вывода (в GOLD):")
    await callback.answer()

@dp.message(Form.admin_set_min_withdraw)
async def admin_set_min_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите число!")
    config["min_withdraw"] = int(message.text)
    await state.clear()
    await message.answer(f"✅ Минималка на вывод изменена на **{config['min_withdraw']} GOLD**.", parse_mode="Markdown")

# Выдача бонуса
@dp.callback_query(F.data == "admin_give_bonus")
async def admin_give_bonus_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_bonus_id)
    await callback.message.answer("Введите Telegram ID пользователя:")
    await callback.answer()

@dp.message(Form.admin_give_bonus_id)
async def admin_give_bonus_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("ID должен состоять только из цифр!")
    await state.update_data(target_id=int(message.text))
    await state.set_state(Form.admin_give_bonus_amount)
    await message.answer("Введите количество GOLD:")

@dp.message(Form.admin_give_bonus_amount)
async def admin_give_bonus_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Сумма должна быть числом!")
    data = await state.get_data()
    target_id = data["target_id"]
    amount = int(message.text)
    
    user = get_or_create_user(target_id)
    user["balance"] += amount
    await state.clear()
    
    try:
        await bot.send_message(target_id, f"🎁 Администратор начислил вам **{amount} GOLD**!", parse_mode="Markdown")
    except Exception:
        pass
    await message.answer(f"✅ Пользователю `{target_id}` начислено {amount} GOLD.", parse_mode="Markdown")

# Бан / Разбан / Админы / Рассылка
@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_ban_id)
    await callback.message.answer("Введите Telegram ID для забана:")
    await callback.answer()

@dp.message(Form.admin_ban_id)
async def admin_ban_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("ID должен состоять из цифр!")
    db["banned"].add(int(message.text))
    await state.clear()
    await message.answer(f"🚫 Пользователь `{message.text}` заблокирован.", parse_mode="Markdown")

@dp.callback_query(F.data == "admin_unban")
async def admin_unban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_unban_id)
    await callback.message.answer("Введите Telegram ID для разбана:")
    await callback.answer()

@dp.message(Form.admin_unban_id)
async def admin_unban_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("ID должен состоять из цифр!")
    db["banned"].discard(int(message.text))
    await state.clear()
    await message.answer(f"✅ Пользователь `{message.text}` разблокирован.", parse_mode="Markdown")

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
    if new_admin not in ADMIN_IDS: ADMIN_IDS.append(new_admin)
    await state.clear()
    await message.answer(f"👑 Пользователь `{new_admin}` добавлен в список администраторов!", parse_mode="Markdown")

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
    await state.clear()

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
