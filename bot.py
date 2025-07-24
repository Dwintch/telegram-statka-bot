import asyncio
import os
import re
from collections import defaultdict, Counter
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.markdown import hbold
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Загрузка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
TOPIC_ID = int(os.getenv("TOPIC_ID"))

# Инициализация
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Хранилище статистики
stats = defaultdict(lambda: Counter())

# Магазины и ключи
SHOP_NAMES = ["хайп", "янтарь", "полка"]
KEYWORDS = ["мало", "нету"]

# Цветовая маркировка
STATE_COLORS = {
    "мало": "\u26A0\ufe0f",   # ⚠️ жёлтый
    "нету": "\u274C"          # ❌ красный
}

def extract_data(text: str):
    """
    Извлекает магазин, товары и их состояние (мало/нету)
    """
    found_shop = None
    for shop in SHOP_NAMES:
        if shop in text.lower():
            found_shop = shop
            break
    
    if not found_shop:
        return None

    results = []
    for keyword in KEYWORDS:
        if keyword in text.lower():
            pattern = rf"{keyword} ([\w\s\-\d\+]+)"
            matches = re.findall(pattern, text.lower())
            for item in matches:
                results.append((keyword, item.strip()))

    return found_shop, results

@dp.message(F.chat.id == GROUP_CHAT_ID)
async def handle_message(message: Message):
    parsed = extract_data(message.text)
    if not parsed:
        return

    shop, items = parsed
    for state, name in items:
        stats[shop][(name, state)] += 1
    print(f"[{shop.upper()}] {items}")

@dp.message(F.text.startswith("/статка"))
async def send_statka(message: Message):
    await message.reply(await format_stat())

def format_item(name, state, count):
    icon = STATE_COLORS.get(state, "")
    return f"{icon} <b>{name}</b> — {state} ({count})"

async def format_stat():
    if not stats:
        return "Пока нет данных."
    
    lines = [f"\uD83D\uDCCA <b>Актуальная статка</b> на {datetime.now().strftime('%d.%m %H:%M')}\n"]
    for shop, items in stats.items():
        lines.append(f"<u>{shop.capitalize()}</u>:")
        for (name, state), count in items.most_common():
            lines.append(format_item(name, state, count))
        lines.append("")
    return "\n".join(lines)

def setup_scheduler():
    scheduler.add_job(send_daily_stat, "cron", hour=0, minute=0)
    scheduler.start()

async def send_daily_stat():
    text = await format_stat()
    await bot.send_message(chat_id=GROUP_CHAT_ID, message_thread_id=TOPIC_ID, text=text)

async def main():
    setup_scheduler()
    await dp.start_polling(bot)

if __name__ == "__main__":
    print("Бот запущен...")
    asyncio.run(main())


