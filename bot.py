import os
import re
import json
from collections import defaultdict, Counter
from datetime import datetime

import asyncio
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import openai
from openai import AsyncOpenAI

# --- Загрузка переменных окружения ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
TOPIC_ID = int(os.getenv("TOPIC_ID"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Например https://yourdomain.com/webhook

if not all([BOT_TOKEN, GROUP_CHAT_ID, TOPIC_ID, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Проверьте, что все переменные окружения (BOT_TOKEN, GROUP_CHAT_ID, TOPIC_ID, OPENAI_API_KEY, WEBHOOK_URL) заданы!")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
scheduler = AsyncIOScheduler()

# --- Статистика и кеш сообщений ---
stats = defaultdict(Counter)  # stats[shop][(item,state)] = count
recent_user_msgs = defaultdict(list)  # user_id -> list[str]

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
    """Попытка парсить вручную из текста магазин и товары со статусом"""
    found_shop = None
    text_lower = text.lower()
    for shop in SHOP_NAMES:
        if shop in text_lower:
            found_shop = shop
            break
    if not found_shop:
        return None

    results = []
    for keyword in KEYWORDS:
        # Ищем ключевое слово рядом с названием товара
        pattern1 = rf"{keyword}\s+([\w\s\-\d\+]+)"
        matches1 = re.findall(pattern1, text_lower)
        for item in matches1:
            results.append((normalize_state(keyword), item.strip()))
        pattern2 = rf"([\w\s\-\d\+]+)\s+{keyword}"
        matches2 = re.findall(pattern2, text_lower)
        for item in matches2:
            results.append((normalize_state(keyword), item.strip()))

    if not results:
        return None
    return found_shop, results

async def extract_with_openai(text: str):
    """Использовать OpenAI, чтобы распарсить сообщение (если ручной парсинг не сработал)"""
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

@router.message(F.chat.id == GROUP_CHAT_ID)
async def handle_any_message(message: Message):
    """
    Обрабатываем только сообщения в группе (где мы считаем статистику),
    но учитываем только сообщения в нужном топике.
    """
    if message.message_thread_id != TOPIC_ID:
        return  # Игнорируем сообщения вне нужного топика

    user_id = message.from_user.id
    text = message.text or ""
    recent_user_msgs[user_id].append(text)
    # Храним максимум последние 3 сообщения пользователя
    if len(recent_user_msgs[user_id]) > 3:
        recent_user_msgs[user_id].pop(0)

    context_text = " ".join(recent_user_msgs[user_id])
    print(f"📩 Контекст для user {user_id}: {context_text}")

    parsed = extract_data(context_text)
    if not parsed:
        parsed = await extract_with_openai(context_text)
        print(f"🤖 OpenAI распарсил: {parsed}")

    if not parsed:
        print("⛔️ Не удалось распарсить данные из сообщения.")
        return

    shop, items = parsed
    for state, name in items:
        stats[shop][(name, state)] += 1
    print(f"✅ Статистика обновлена: {shop} -> {items}")

@router.message(F.text.startswith("/статка"))
async def send_statka(message: Message):
    """Отправляем актуальную статистику"""
    text = await format_stat()
    await message.reply(text)

def format_item(name, state, count):
    icon = STATE_COLORS.get(state, "")
    return f"{icon} <b>{name}</b> — {state} ({count})"

async def format_stat():
    if not stats:
        return "Пока нет данных."
    lines = [f"📊 <b>Актуальная статистика</b> на {datetime.now().strftime('%d.%m %H:%M')}\n"]
    for shop, items in stats.items():
        lines.append(f"<u>{shop.capitalize()}</u>:")
        for (name, state), count in items.most_common():
            lines.append(format_item(name, state, count))
        lines.append("")
    return "\n".join(lines)

@router.message(F.text == "/топик")
async def show_topic_id(message: Message):
    """Команда для получения ID топика, где написана команда"""
    topic_id = message.message_thread_id
    print(f"Команда /топик от {message.from_user.id}, thread_id={topic_id}")
    if topic_id:
        await message.reply(f"ID этого топика: {topic_id}")
    else:
        await message.reply("Это сообщение не в топике.")

def setup_scheduler():
    scheduler.add_job(send_daily_stat, "cron", hour=0, minute=0)
    scheduler.start()

async def send_daily_stat():
    text = await format_stat()
    await bot.send_message(chat_id=GROUP_CHAT_ID, message_thread_id=TOPIC_ID, text=text)

async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)
    print("🔗 Webhook установлен")

async def main():
    dp.include_router(router)
    setup_scheduler()
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    return app

if __name__ == "__main__":
    print("🚀 Запуск веб-сервиса...")
    web.run_app(main())
