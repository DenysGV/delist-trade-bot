import asyncio
import re
import os
import time
from telethon import TelegramClient, events
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Импортируем нашу функцию торговли
from client import trade_execution

# --- КОНФИГУРАЦИЯ ---
API_ID = 39164577
API_HASH = 'c10feba2abf93687ac5a169051528ab4'
CHANNEL_USERNAME = 'testdelist'
MESSAGE_TRIGGER = 'delisted from Binance futures'
SESSION_NAME = 'my_account'

BOT_TOKEN = '8328891618:AAHx5B4uPzJhoDsON3JyfzBbjWgSpoiltrw' # Получить у @BotFather
ADMIN_IDS = [630682516] # Замените на ваши Telegram ID

# --- ГЛОБАЛЬНОЕ СОСТОЯНИЕ (In-Memory) ---
config = {
    "is_active": True,
    "leverage": 5,
    "margin": 10,
    "stop_loss": 5
}

# Для защиты от дублей: { "COIN_NAME": timestamp }
processed_signals = {}
COOLDOWN_SECONDS = 60 

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
telethon_client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Состояния для смены параметров
class SettingsStates(StatesGroup):
    waiting_for_leverage = State()
    waiting_for_margin = State()
    waiting_for_stop_loss = State()

# ==========================================
# ЛОГИКА AIOGRAM (БОТ УПРАВЛЕНИЯ)
# ==========================================

def get_main_keyboard():
    status_emoji = "🟢 ВКЛ" if config["is_active"] else "🔴 ВЫКЛ"
    kb = [
        [InlineKeyboardButton(text=f"Статус: {status_emoji}", callback_data="toggle_work")],
        [
            InlineKeyboardButton(text="⚙️ Плечо", callback_data="set_leverage"),
            InlineKeyboardButton(text="💵 Маржа", callback_data="set_margin"),
            InlineKeyboardButton(text="🛑 Стоп", callback_data="set_stop")
        ],
        [InlineKeyboardButton(text="📊 Текущие настройки", callback_data="show_config")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_back_keyboard():
    kb = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="to_main")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def check_admin(user_id: int):
    return user_id in ADMIN_IDS

# Хендлер отмены (должен быть выше других обработчиков сообщений со стейтами)
@dp.message(Command("cancel"))
@dp.message(F.text.casefold() == "отмена")
async def cmd_cancel(message: types.Message, state: FSMContext):
    if not check_admin(message.from_user.id): return
    
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены.", reply_markup=get_main_keyboard())
        return

    await state.clear()
    await message.answer(
        "🚫 **Действие отменено.** Возврат в меню.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not check_admin(message.from_user.id): return
    await message.answer(
        "👋 **Добро пожаловать в панель управления трейд-ботом!**\n\n"
        "Здесь вы можете контролировать работу алгоритма делистинга.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "to_main")
async def to_main(callback: types.CallbackQuery):
    if not check_admin(callback.from_user.id): return
    await callback.message.edit_text(
        "👋 **Добро пожаловать в панель управления трейд-ботом!**\n\n"
        "Здесь вы можете контролировать работу алгоритма делистинга.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "show_config")
async def show_config(callback: types.CallbackQuery):
    if not check_admin(callback.from_user.id): return
    text = (
        "📜 **Текущие параметры торговли:**\n\n"
        f"🤖 Статус: {'✅ Активен' if config['is_active'] else '❌ Остановлен'}\n"
        f"🎯 Плечо: `{config['leverage']}x`\n"
        f"💰 Маржа: `{config['margin']} USDT`\n"
        f"🛡 Стоп-лосс: `{config['stop_loss']}%`"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "toggle_work")
async def toggle_work(callback: types.CallbackQuery):
    if not check_admin(callback.from_user.id): return
    config["is_active"] = not config["is_active"]
    await callback.message.edit_reply_markup(reply_markup=get_main_keyboard())

# Обработка ввода параметров (FSM)
@dp.callback_query(F.data == "set_leverage")
async def ask_leverage(callback: types.CallbackQuery, state: FSMContext):
    if not check_admin(callback.from_user.id): return
    await callback.message.answer("Введите новое значение плеча (число) или напишите /cancel:")
    await state.set_state(SettingsStates.waiting_for_leverage)

@dp.message(SettingsStates.waiting_for_leverage)
async def process_leverage(message: types.Message, state: FSMContext):
    if not check_admin(message.from_user.id): return
    if message.text.isdigit():
        config["leverage"] = int(message.text)
        await message.answer(f"✅ Плечо установлено на {config['leverage']}x", reply_markup=get_main_keyboard())
        await state.clear()
    else:
        await message.answer("Пожалуйста, введите целое число или /cancel для отмены.")

@dp.callback_query(F.data == "set_margin")
async def ask_margin(callback: types.CallbackQuery, state: FSMContext):
    if not check_admin(callback.from_user.id): return
    await callback.message.answer("Введите сумму маржи в USDT или напишите /cancel:")
    await state.set_state(SettingsStates.waiting_for_margin)

@dp.message(SettingsStates.waiting_for_margin)
async def process_margin(message: types.Message, state: FSMContext):
    if not check_admin(message.from_user.id): return
    try:
        config["margin"] = float(message.text)
        await message.answer(f"✅ Маржа установлена на {config['margin']} USDT", reply_markup=get_main_keyboard())
        await state.clear()
    except:
        await message.answer("Введите корректное число или /cancel для отмены.")

@dp.callback_query(F.data == "set_stop")
async def ask_stop(callback: types.CallbackQuery, state: FSMContext):
    if not check_admin(callback.from_user.id): return
    await callback.message.answer("Введите процент стоп-лосса или напишите /cancel:")
    await state.set_state(SettingsStates.waiting_for_stop_loss)

@dp.message(SettingsStates.waiting_for_stop_loss)
async def process_stop(message: types.Message, state: FSMContext):
    if not check_admin(message.from_user.id): return
    try:
        config["stop_loss"] = float(message.text)
        await message.answer(f"✅ Стоп-лосс установлен на {config['stop_loss']}%", reply_markup=get_main_keyboard())
        await state.clear()
    except:
        await message.answer("Введите корректное число или /cancel для отмены.")

# ==========================================
# ЛОГИКА TELETHON (МОНИТОРИНГ)
# ==========================================

@telethon_client.on(events.NewMessage(chats=CHANNEL_USERNAME))
async def telethon_handler(event):
    try:
        if not config["is_active"]:
            return

        message = event.message.message
        if not message or not MESSAGE_TRIGGER in message:
            return

        found_coins = re.findall(r'\$([A-Z0-9]+)', message)
        coins = list(set(found_coins)) 

        if coins:
            current_time = time.time()
            to_process = []

            for coin in coins:
                last_time = processed_signals.get(coin, 0)
                if current_time - last_time > COOLDOWN_SECONDS:
                    processed_signals[coin] = current_time
                    to_process.append(coin)
                else:
                    print(f"⚠️ Пропуск дубликата {coin} (cooldown)")

            if not to_process:
                return

            for admin in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin, 
                        f"🚀 **Обнаружен сигнал!**\nМонеты: {', '.join(to_process)}\nЗапуск сделок...",
                        parse_mode="Markdown"
                    )
                except:
                    pass
            
            for coin in to_process:
                asyncio.create_task(trade_execution(coin, config.copy()))
    except Exception as e:
        print(f"Ошибка Telethon: {e}")

# ==========================================
# ЗАПУСК
# ==========================================

async def main():
    await telethon_client.start()
    print("Telethon запущен.")
    print("Aiogram бот запущен.")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено.")