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

# Загрузка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
TOPIC_ID = int(os.getenv("TOPIC_ID"))

# Инициализация
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# Хранилище статистики
stats = defaultdict(lambda: Counter())

# Магазины и ключи
SHOP_NAMES = ["хайп", "янтарь", "полка"]
KEYWORDS = ["мало", "нету", "нет", "закончился", "закончились", "не осталось"]

# Цветовая маркировка
STATE_COLORS = {
    "мало": "\u26A0\ufe0f",   # ⚠️ жёлтый
    "нету": "\u274C",          # ❌ красный
    "нет": "\u274C",
    "закончился": "\u274C",
    "закончились": "\u274C",
    "не осталось": "\u274C"
}

SYNONYMS = {
    "нет": "нету",
    "не осталось": "нету",
    "закончился": "нету",
    "закончились": "нету"
}

def normalize_state(state):
    return SYNONYMS.get(state, state)

def extract_data(text: str):
    text = text.lower()
    found_shop = None
    for shop in SHOP_NAMES:
        if shop in text:
            found_shop = shop
            break
    if not found_shop:
        return None

    results = []
    for keyword in KEYWORDS:
        if keyword in text:
            # Ищем формат "ключевое_слово товар"
            pattern = rf"{keyword}\s+([\w\-+]+)"
            matches = re.findall(pattern, text)
            for item in matches:
                norm = normalize_state(keyword)
                results.append((norm, item))

            # Ищем обратный формат "товар ключевое_слово"
            pattern2 = rf"([\w\-+]+)\s+{keyword}"
            matches2 = re.findall(pattern2, text)
            for item in matches2:
                norm = normalize_state(keyword)
                results.append((norm, item))
    return found_shop, results if results else None

@dp.message()
async def handle_any_message(message: Message):
    print(f"Получено сообщение в чате {message.chat.id}, теме {message.message_thread_id}: {message.text}")
    parsed = extract_data(message.text or "")
    if not parsed:
        print("Не распарсили сообщение.")
        return
    shop, items = parsed
    for state, name in items:
        stats[shop][(name, state)] += 1
    print(f"[{shop.upper()}] Добавлены данные: {items}")

@dp.message(F.text.startswith("/статка"))
async def send_statka(message: Message):
    print("Запрошена статистика:")
    print(stats)
    await message.reply(await format_stat())

def format_item(name, state, count):
    icon = STATE_COLORS.get(state, "")
    return f"{icon} <b>{name}</b> — {state} ({count})"

async def format_stat():
    if not stats:
        return "Пока нет данных."

    lines = [f"\U0001F4CA <b>Актуальная статистика</b> на {datetime.now().strftime('%d.%m %H:%M')}\n"]
    for shop, items in stats.items():
        lines.append(f"<u>{shop.capitalize()}</u>:")
        for (name, state), count in items.most_common():
            lines.append(format_item(name, state, count))
        lines.append("")
    return "\n".join(lines)

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
