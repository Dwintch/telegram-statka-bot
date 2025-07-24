import asyncio
import os
import re
from collections import defaultdict, Counter, deque
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ContentType
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.markdown import hbold
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import openai

# Загрузка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
TOPIC_ID = int(os.getenv("TOPIC_ID"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

# Инициализация
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Конфиги
SHOP_NAMES = ["хайп", "янтарь", "полка"]
KEYWORDS = ["мало", "нету", "нет", "закончился", "закончились", "не осталось"]

STATE_COLORS = {
    "мало": "⚠️",
    "нету": "❌",
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

# Статистика: магазин -> Counter((название товара, состояние) -> количество)
stats = defaultdict(Counter)

# Хранение последних сообщений пользователя в теме, для анализа ИИ
# Ключ: user_id, Значение: deque из текстов (лимит 5)
user_messages = defaultdict(lambda: deque(maxlen=5))

# Хранение сообщений с фото для связи с пред. сообщением
# Ключ: message_id на которое отвечают, значение True (нет в наличии)
photo_reply_map = {}

async def analyze_messages_with_ai(texts: list[str]) -> list[tuple[str, str]]:
    """
    Использует OpenAI для анализа нескольких сообщений пользователя
    и возвращает список (состояние, товар).
    """

    prompt = (
        "Ты помогаешь распарсить сообщения о нехватках товаров по магазинам.\n"
        "Магазины: хайп, янтарь, полка.\n"
        "Состояния: мало, нету.\n"
        "Пример сообщений:\n"
        "Хайп мало лабубу энерджи\n"
        "В полке нету стичей\n"
        "Выведи результат в формате: состояние товар\n"
        "Вот сообщения:\n"
        + "\n".join(texts) +
        "\n\nВыведи только результат, по одной паре на строку."
    )

    try:
        response = await asyncio.to_thread(
            lambda: openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=150,
            )
        )
        result_text = response.choices[0].message.content.strip()
        # Парсим ответ
        pairs = []
        for line in result_text.split('\n'):
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                state, item = parts
                state = normalize_state(state.lower())
                pairs.append((state, item.strip()))
        return pairs
    except Exception as e:
        print("OpenAI API error:", e)
        return []

def update_stats(shop: str, pairs: list[tuple[str, str]]):
    for state, item in pairs:
        stats[shop][(item, state)] += 1

def find_shop_in_text(text: str) -> str | None:
    text_lower = text.lower()
    for shop in SHOP_NAMES:
        if shop in text_lower:
            return shop
    return None

@dp.message(F.is_topic_message & (F.chat.id == GROUP_CHAT_ID) & (F.message_thread_id == TOPIC_ID))
async def handle_topic_message(message: Message):
    # Сохраняем сообщение пользователя
    user_messages[message.from_user.id].append(message.text or "")

    # Проверяем есть ли фото, если да — связываем с ответом
    if message.reply_to_message and message.content_type == ContentType.PHOTO:
        photo_reply_map[message.reply_to_message.message_id] = True

    # Если сообщение не текстовое — пропускаем
    if not message.text:
        return

    # Проверяем магазин в сообщении
    shop = find_shop_in_text(message.text)
    if not shop:
        return

    # Берём последние 5 сообщений пользователя
    recent_texts = list(user_messages[message.from_user.id])

    # Анализируем с помощью ИИ
    pairs = await analyze_messages_with_ai(recent_texts)

    # Проверяем есть ли товар с пометкой "нет", если это ответ на сообщение с фото — добавляем
    if message.message_id in photo_reply_map:
        # добавляем "нету" для всего что в последнем сообщении
        for state, item in pairs:
            if state != "нету":
                pairs.append(("нету", item))

    if pairs:
        update_stats(shop, pairs)
        print(f"Updated stats for {shop}: {pairs}")

@dp.message(F.text.startswith("/статка"))
async def cmd_statka(message: Message):
    text = await format_stat()
    await message.reply(text)

async def format_stat():
    if not stats:
        return "Пока нет данных."

    lines = [f"📊 <b>Актуальная статка на {datetime.now().strftime('%d.%m %H:%M')}</b>\n"]
    for shop, counter in stats.items():
        lines.append(f"<u>{shop.capitalize()}</u>:")
        for (item, state), count in counter.most_common():
            icon = STATE_COLORS.get(state, "")
            lines.append(f"{icon} <b>{item}</b> — {state} ({count})")
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    print("Бот запущен...")
    asyncio.run(main())
