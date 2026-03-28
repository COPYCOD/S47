import os
import asyncio
import logging
import psutil
import time
import httpx # Переконайтеся, що зробили: pip install httpx
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Dict, List
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, 
    InlineKeyboardButton, InlineKeyboardMarkup
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ════════════════════════════════════════
# 1. СИСТЕМНЕ ЛОГУВАННЯ (ДЛЯ ДІАГНОСТИКИ)
# ════════════════════════════════════════
LOG_FILE = "sherlock_osint.log"
handler = RotatingFileHandler(LOG_FILE, maxBytes=20*1024*1024, backupCount=5, encoding='utf-8')
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s]: %(message)s', 
    handlers=[handler, logging.StreamHandler()]
)
logger = logging.getLogger("SHERLOCK-REPAIR")

load_dotenv()

class Config:
    TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

if not Config.TOKEN or not Config.ADMIN_ID:
    logger.critical("Відсутній TOKEN або ADMIN_ID у файлі .env!")
    exit(1)

# ════════════════════════════════════════
# 2. OSINT LOGIC (STABLE EDITION)
# ════════════════════════════════════════
class OsintService:
    @staticmethod
    async def check_nickname(nickname: str) -> str:
        """Перевірка нікнейма (стабільна версія)."""
        targets = {
            "GitHub": f"https://github.com/{nickname}",
            "Twitter": f"https://twitter.com/{nickname}",
            "Instagram": f"https://instagram.com/{nickname}",
            "Reddit": f"https://reddit.com/user/{nickname}",
            "TikTok": f"https://tiktok.com/@{nickname}"
        }
        
        results = []
        # Використовуємо ліміт з'єднань, щоб не 'вішати' систему
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        async with httpx.AsyncClient(timeout=10.0, limits=limits, follow_redirects=True) as client:
            for site, url in targets.items():
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        results.append(f"✅ **{site}**: [Посилання]({url})")
                except Exception as e:
                    logger.error(f"Помилка при перевірці {site}: {e}")
                    continue
        
        if not results: return "🕵️ Жодних цифрових слідів не знайдено."
        return "🔍 **РЕЗУЛЬТАТИ РОЗШУКУ:**\n\n" + "\n".join(results)

    @staticmethod
    async def ip_lookup(ip: str) -> str:
        """Геолокація IP."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.get(f"http://ip-api.com/json/{ip}")
                data = resp.json()
                if data.get('status') == 'fail': return "❌ Об'єкт не знайдено (невірний IP)."
                return (
                    f"🌍 **IP DOSSIER: {ip}**\n"
                    f"📍 Локація: `{data.get('city')}, {data.get('country')}`\n"
                    f"📡 Провайдер: `{data.get('isp')}`\n"
                    f"🏢 Org: `{data.get('org')}`"
                )
            except Exception as e:
                return f"❌ Помилка сервісу: {str(e)}"

# ════════════════════════════════════════
# 3. ІНТЕРФЕЙС ТА ХЕНДЛЕРИ
# ════════════════════════════════════════
bot = Bot(token=Config.TOKEN)
dp = Dispatcher()

def main_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🕵️ Пробити нік", callback_data="osint_nick"))
    b.row(InlineKeyboardButton(text="🌍 Пробити IP", callback_data="osint_ip"))
    b.row(InlineKeyboardButton(text="📊 Статус сервера", callback_data="stats"))
    b.row(InlineKeyboardButton(text="🔌 Off", callback_data="off"))
    return b.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id != Config.ADMIN_ID: return
    await message.answer("🦾 **S47 SHERLOCK-REPAIR ACTIVATED.**\nСистему відновлено. Чекаю на вказівки.", reply_markup=main_kb())

@dp.callback_query(F.data == "osint_nick")
async def cb_nick(cb: CallbackQuery):
    await cb.message.answer("🕵️ Надішліть нікнейм з префіксом `?` (наприклад: `?ivan_crypto`)")
    await cb.answer()

@dp.callback_query(F.data == "osint_ip")
async def cb_ip(cb: CallbackQuery):
    await cb.message.answer("🌍 Надішліть IP з префіксом `!` (наприклад: `!1.1.1.1`)")
    await cb.answer()

@dp.message(F.text.startswith("?"))
async def handle_nick(message: Message):
    if message.from_user.id != Config.ADMIN_ID: return
    nick = message.text[1:].strip()
    wait = await message.answer(f"🔎 Шерлок аналізує сліди `{nick}`...")
    res = await OsintService.check_nickname(nick)
    await wait.edit_text(res, parse_mode="Markdown", disable_web_page_preview=True)

@dp.message(F.text.startswith("!"))
async def handle_ip(message: Message):
    if message.from_user.id != Config.ADMIN_ID: return
    ip = message.text[1:].strip()
    res = await OsintService.ip_lookup(ip)
    await message.answer(res, parse_mode="Markdown")

@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    await cb.message.edit_text(f"📊 **SERVER STATUS:**\nCPU: `{cpu}%` | RAM: `{ram}%`", reply_markup=main_kb())

@dp.callback_query(F.data == "off")
async def cb_off(cb: CallbackQuery):
    await cb.message.edit_text("🔌 Offline.")
    os._exit(0)

# ════════════════════════════════════════
# 4. STARTUP
# ════════════════════════════════════════
async def main():
    logger.info("Bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
