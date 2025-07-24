import asyncio
import os
import re
from collections import defaultdict, Counter, deque
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import openai
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
TOPIC_ID = int(os.getenv("TOPIC_ID"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Константы и переменные
SHOP_NAMES = ["хайп", "янтарь", "полка"]
KEYWORDS = ["мало", "нету", "нет", "закончился", "закончились", "не осталось"]

STATE_COLORS = {
    "мало": "⚠️",   # желтый
    "нету": "❌",   # красный
    "нет": "❌",
    "закончился": "❌",
    "закончились": "❌",
    "не осталось": "❌"
}

SYNONYMS = {
    "нет": "нету",
    "не осталось": "нету",
    "закончился": "нету",
    "закончились": "нету"
}

def normalize_state(state):
    return SYNONYMS.get(state, state)

# Статистика: { магазин: Counter((товар, состояние) : count) }
stats = defaultdict(Counter)

# Для анализа сообщений: { user_id: deque[(message_id, text, datetime)] }
user_message_history = defaultdict(lambda: deque(maxlen=10))

def extract_data(text: str):
    """
    Извлекает магазин, товары и их состояние (мало/нету) из текста.
    Возвращает (shop, [(state, item), ...]) или None.
    """
    text_lower = text.lower()
    found_shop = None
    for shop in SHOP_NAMES:
        if shop in text_lower:
            found_shop = shop
            break
    if not found_shop:
        return None

    results = []
    # ищем конструкции "мало товар" или "товар мало"
    for keyword in KEYWORDS:
        norm_state = normalize_state(keyword)
        # pattern "keyword item"
        pattern1 = rf"{keyword}\s+([\w\s\-\d\+]+)"
        matches1 = re.findall(pattern1, text_lower)
        for item in matches1:
            item = item.strip()
            if item:
                results.append((norm_state, item))

        # pattern "item keyword"
        pattern2 = rf"([\w\s\-\d\+]+)\s+{keyword}"
        matches2 = re.findall(pattern2, text_lower)
        for item in matches2:
            item = item.strip()
            if item:
                results.append((norm_state, item))

    if not results:
        return None

    return found_shop, results

async def analyze_messages_with_openai(texts: list[str]) -> dict:
    """
    Использует OpenAI для анализа текста списка сообщений, чтобы понять товары и их состояния.
    Возвращает структуру вида:
    {
        "shop": "хайп",
        "items": [
            {"state": "мало", "name": "лабубу энерджи"},
            {"state": "нету", "name": "стичей"},
            ...
        ]
    }
    Если не удалось распарсить — возвращает None.
    """
    prompt = (
        "Ты — помощник, который анализирует сообщения о наличии товаров в магазинах.\n"
        "Магазины: хайп, янтарь, полка.\n"
        "Товары могут быть в состоянии 'мало' или 'нету'.\n"
        "Вот сообщения:\n"
        + "\n".join(f"- {t}" for t in texts) +
        "\n\nВыведи JSON с магазином и списком товаров с состояниями. "
        "Пример:\n"
        '{ "shop": "хайп", "items": [ {"state": "мало", "name": "лабубу энерджи"}, {"state": "нету", "name": "стичей"} ] }'
    )

    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = response.choices[0].message.content.strip()
        import json
        parsed = json.loads(content)
        # базовая валидация
        if "shop" in parsed and "items" in parsed:
            return parsed
        return None
    except Exception as e:
        print(f"OpenAI parsing error: {e}")
        return None

async def update_stats_from_user_messages(user_id: int):
    """
    Анализирует последние сообщения пользователя и обновляет глобальную статистику.
    """
    messages = [msg for _, msg, dt in user_message_history[user_id] if (datetime.now() - dt).total_seconds() < 86400]
    if not messages:
        return

    parsed = await analyze_messages_with_openai(messages)
    if not parsed:
        return

    shop = parsed["shop"].lower()
    if shop not in SHOP_NAMES:
        return

    for item in parsed["items"]:
        state = normalize_state(item.get("state", ""))
        name = item.get("name", "").strip()
        if state and name:
            stats[shop][(name, state)] += 1

@dp.message(F.is_topic_message & (F.chat.id == GROUP_CHAT_ID) & (F.message_thread_id == TOPIC_ID))
async def handle_topic_message(message: Message):
    # Добавляем в историю
    user_id = message.from_user.id
    user_message_history[user_id].append((message.message_id, message.text or "", datetime.now()))

    # Обрабатываем базово для быстрого сбора
    parsed = extract_data(message.text or "")
    if parsed:
        shop, items = parsed
        for state, name in items:
            stats[shop][(name, state)] += 1
        print(f"[{shop.upper()}] {items}")

    # Также запускаем расширенный анализ нескольких сообщений с ИИ (асинхронно)
    asyncio.create_task(update_stats_from_user_messages(user_id))

@dp.message(F.text.startswith("/статка"))
async def send_statka(message: Message):
    text = await format_stat()
    await message.reply(text)

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

async def send_daily_stat():
    text = await format_stat()
    await bot.send_message(chat_id=GROUP_CHAT_ID, message_thread_id=TOPIC_ID, text=text)

def setup_scheduler():
    scheduler.add_job(send_daily_stat, "cron", hour=0, minute=0)
    scheduler.start()

async def main():
    setup_scheduler()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
