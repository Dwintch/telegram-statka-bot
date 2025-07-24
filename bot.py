import os
import re
import json
from collections import defaultdict, Counter
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import asyncio
import openai
from openai import AsyncOpenAI

# Загрузка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
TOPIC_ID = int(os.getenv("TOPIC_ID"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# Статистика
stats = defaultdict(lambda: Counter())
recent_user_msgs = defaultdict(list)  # user_id -> [messages]

SHOP_NAMES = ["хайп", "янтарь", "полка"]
KEYWORDS = ["мало", "нету", "нет", "закончился", "закончились", "не осталось"]

STATE_COLORS = {
    "мало": "\u26A0\ufe0f",
    "нету": "\u274C",
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
    found_shop = None
    for shop in SHOP_NAMES:
        if shop in text.lower():
            found_shop = shop
            break
    if not found_shop:
        return None

    results = []
    for keyword in KEYWORDS:
        pattern = rf"{keyword}\s+([\w\s\-\d\+]+)"
        matches = re.findall(pattern, text.lower())
        for item in matches:
            norm = normalize_state(keyword)
            results.append((norm, item.strip()))

        pattern2 = rf"([\w\s\-\d\+]+)\s+{keyword}"
        matches2 = re.findall(pattern2, text.lower())
        for item in matches2:
            norm = normalize_state(keyword)
            results.append((norm, item.strip()))

    return found_shop, results

async def extract_with_openai(text: str):
    prompt = (
        "Проанализируй сообщение из чата и выдели:\n"
        "1. Название магазина (из: хайп, янтарь, полка)\n"
        "2. Товары и состояние (мало/нету/нет/закончился/не осталось)\n"
        "Верни в формате JSON: {\"shop\": \"...\", \"items\": [[\"мало\", \"пиво\"], [\"нету\", \"спрайт\"]]}\n"
        f"Сообщение: \"{text}\""
    )

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)
        shop = parsed.get("shop")
        items = parsed.get("items", [])
        if shop and items:
            return shop, [(normalize_state(state), name) for state, name in items]
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
    return None

@dp.message(F.is_topic_message & (F.chat.id == GROUP_CHAT_ID) & (F.message_thread_id == TOPIC_ID))
async def handle_topic_message(message: Message):
    user_id = message.from_user.id
    recent_user_msgs[user_id].append(message.text or "")
    context_text = " ".join(recent_user_msgs[user_id][-3:])  # последние 3 сообщения
    print(f"📩 Контекст: {context_text}")

    parsed = extract_data(context_text)
    if not parsed:
        parsed = await extract_with_openai(context_text)
        print(f"🤖 OpenAI дал: {parsed}")

    if not parsed:
        print("⛔️ Не удалось распарсить сообщение.")
        return

    shop, items = parsed
    for state, name in items:
        stats[shop][(name, state)] += 1
    print(f"✅ Статка обновлена: {shop} -> {items}")

@dp.message(F.text.startswith("/статка"))
async def send_statka(message: Message):
    await message.reply(await format_stat())

@dp.message(F.text.startswith("/топик"))
async def send_topic_id(message: Message):
    if message.is_topic_message:
        await message.reply(f"ID топика: {message.message_thread_id}")
    else:
        await message.reply("Это сообщение не в топике.")

def format_item(name, state, count):
    icon = STATE_COLORS.get(state, "")
    return f"{icon} <b>{name}</b> — {state} ({count})"

async def format_stat():
    if not stats:
        return "Пока нет данных."
    lines = [f"📊 <b>Актуальная статка</b> на {datetime.now().strftime('%d.%m %H:%M')}\n"]
    for shop, items in stats.items():
        lines.append(f"<u>{shop.capitalize()}</u>:")
        for (name, state), count in items.most_common():
            lines.append(format_item(name, state, count))
        lines.append("")
    return "\n".join(lines)

async def main():
    print("🚀 Запуск бота на polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
