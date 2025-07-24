import os
import re
import asyncio
from collections import defaultdict, deque, Counter
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InputFile
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import openai

# Загрузка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
TOPIC_ID = int(os.getenv("TOPIC_ID"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Константы
SHOP_NAMES = ["хайп", "янтарь", "полка"]
STATE_SYNONYMS = {
    "нет": "нету",
    "не осталось": "нету",
    "закончился": "нету",
    "закончились": "нету"
}
KEYWORDS = list(STATE_SYNONYMS.keys()) + ["мало", "нету"]

STATE_COLORS = {
    "мало": "\u26A0\ufe0f",   # ⚠️ жёлтый
    "нету": "\u274C",          # ❌ красный
}

USER_CONTEXT_SIZE = 5

# Храним последние сообщения (текст + фото) пользователя в топике
user_messages = defaultdict(lambda: deque(maxlen=USER_CONTEXT_SIZE))

# Храним статистику за каждый день: stats_by_day[date][shop][(name, state)] = count
stats_by_day = defaultdict(lambda: defaultdict(Counter))


def normalize_state(state):
    return STATE_SYNONYMS.get(state, state)


def parse_ai_response(text: str):
    """
    Пример простой обработки ответа ИИ.
    Предполагаем, что ИИ вернёт список строк:
    Магазин: хайп
    - мало: лабубу энерджи
    - нету: стичи
    ...
    Возвращаем (shop, [(state, name), ...])
    """
    result = []
    current_shop = None
    lines = text.lower().split("\n")
    for line in lines:
        line = line.strip()
        if any(shop in line for shop in SHOP_NAMES):
            # Найти магазин в строке
            for shop in SHOP_NAMES:
                if shop in line:
                    current_shop = shop
                    break
        elif current_shop and line.startswith("-"):
            # Формат "- мало: товар1, товар2"
            m = re.match(r"-\s*(\w+):\s*(.+)", line)
            if m:
                state = normalize_state(m.group(1))
                items = [item.strip() for item in re.split(r",| и |;", m.group(2))]
                for item in items:
                    if item:
                        result.append((current_shop, (state, item)))
    return result


async def ai_parse_messages(text: str):
    prompt = f"""
Ты — помощник для разбора сообщений в телеграм-группе магазина.
Нужно из текста выделить магазин (из списка: хайп, янтарь, полка),
товары и их состояние: мало, нету, закончился (все синонимы нормализовать к "мало" или "нету").
Верни результат в формате (магазин, состояние, товар), по одному на строку, например:
хайп мало лабубу энерджи
янтарь нету стичи

Текст для анализа:
{text}
"""
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0
        )
        answer = response.choices[0].message.content
        return parse_ai_response(answer)
    except Exception as e:
        print(f"OpenAI error: {e}")
        return []


def extract_data(text: str):
    """
    Быстрая локальная попытка парсинга (резерв)
    """
    found_shop = None
    text = text.lower()
    for shop in SHOP_NAMES:
        if shop in text:
            found_shop = shop
            break
    if not found_shop:
        return []

    results = []
    for keyword in KEYWORDS:
        pattern = rf"{keyword}\s+([\w\s\-\d\+]+)"
        matches = re.findall(pattern, text)
        for item in matches:
            norm = normalize_state(keyword)
            results.append((found_shop, (norm, item.strip())))

        pattern2 = rf"([\w\s\-\d\+]+)\s+{keyword}"
        matches2 = re.findall(pattern2, text)
        for item in matches2:
            norm = normalize_state(keyword)
            results.append((found_shop, (norm, item.strip())))

    return results


def format_item(name, state, count):
    icon = STATE_COLORS.get(state, "")
    return f"{icon} <b>{name}</b> — {state} ({count})"


async def format_stat():
    if not stats_by_day:
        return "Пока нет данных."

    lines = [f"\U0001F4CA <b>Актуальная статистика</b> на {datetime.now().strftime('%d.%m %H:%M')}\n"]
    today = datetime.now().strftime("%d.%m.%Y")
    for shop, items in stats_by_day[today].items():
        lines.append(f"<u>{shop.capitalize()}</u>:")
        for (name, state), count in items.most_common():
            lines.append(format_item(_
