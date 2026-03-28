    import os
import asyncio
import logging
import psutil
import time
import httpx
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
# 1. CONFIG & LOGGING
# ════════════════════════════════════════
LOG_FILE = "sherlock_osint.log"
handler = RotatingFileHandler(LOG_FILE, maxBytes=20*1024*1024, backupCount=5, encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s]: %(message)s', handlers=[handler])
logger = logging.getLogger("SHERLOCK-OSINT")

load_dotenv()
class Config:
    TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    # Порожній ключ для Google Search (якщо захочете додати пізніше)
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

if not Config.TOKEN or not Config.ADMIN_ID:
    exit("CRITICAL: Config missing!")

# ════════════════════════════════════════
# 2. OSINT LOGIC MODULE
# ════════════════════════════════════════
class OsintService:
    @staticmethod
    async def check_nickname(nickname: str) -> str:
        """Перевірка нікнейма на популярних ресурсах."""
        targets = {
            "GitHub": f"https://github.com/{nickname}",
            "Twitter": f"https://twitter.com/{nickname}",
            "Instagram": f"https://instagram.com/{nickname}",
            "Reddit": f"https://reddit.com/user/{nickname}",
            "TikTok": f"https://tiktok.com/@{nickname}",
            "Pinterest": f"https://pinterest.com/{nickname}"
        }
        
        results = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            for site, url in targets.items():
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        results.append(f"✅ **{site}**: {url}")
                except:
                    continue
        
        if not results: return "🕵️ Жодних збігів за цим нікнеймом не знайдено."
        return "🔍 **ЗНАЙДЕНІ ПРОФІЛІ:**\n\n" + "\n".join(results)

    @staticmethod
    async def ip_lookup(ip: str) -> str:
        """Геолокація та дані про IP."""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"http://ip-api.com/json/{ip}?fields=status,message,country,city,isp,org,as,query")
                data = resp.json()
                if data['status'] == 'fail': return "❌ Помилка: Невірний IP."
                return (
                    f"🌍 **IP DOSSIER: {ip}**\n"
                    f"📍 Місто: `{data.get('city')}, {data.get('country')}`\n"
                    f"📡 Провайдер: `{data.get('isp')}`\n"
                    f"🏢 Організація: `{data.get('org')}`"
                )
            except:
                return "❌ Сервіс перевірки IP недоступний."

# ════════════════════════════════════════
# 3. INTERFACE
# ════════════════════════════════════════
def main_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🕵️ OSINT: Пробити нік", callback_data="osint_nick"))
    b.row(InlineKeyboardButton(text="🌍 OSINT: Пробити IP", callback_data="osint_ip"))
    b.row(
        InlineKeyboardButton(text="📊 Сервер", callback_data="stats"),
        InlineKeyboardButton(text="📟 Shell", callback_data="shell")
    )
    b.row(InlineKeyboardButton(text="🔌 Off", callback_data="off"))
    return b.as_markup()

# ════════════════════════════════════════
# 4. HANDLERS
# ════════════════════════════════════════
bot = Bot(token=Config.TOKEN)
dp = Dispatcher()

# Захист (Тільки Командир)
@dp.message(F.from_user.id != Config.ADMIN_ID)
async def access_denied(message: Message):
    logger.warning(f"Access Denied for {message.from_user.id}")
    return

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("🦾 **S47 SHERLOCK-OSINT ACTIVATED.**\nВведіть об'єкт для аналізу.", reply_markup=main_kb())

@dp.callback_query(F.data == "osint_nick")
async def cb_nick(cb: CallbackQuery):
    await cb.message.answer("🕵️ **Введіть нікнейм для пошуку (без @):**\nПриклад: `john_doe` або просто напишіть `?нік` (напр. `?sherlock`)")
    await cb.answer()

@dp.callback_query(F.data == "osint_ip")
async def cb_ip(cb: CallbackQuery):
    await cb.message.answer("🌍 **Введіть IP-адресу для аналізу:**\nПриклад: `!8.8.8.8` (використовуйте знак оклику на початку)")
    await cb.answer()

# Обробка пробиття нікнейма через ?
@dp.message(F.text.startswith("?"))
async def search_nick(message: Message):
    nick = message.text[1:].strip()
    wait = await message.answer(f"🔎 Шерлок шукає сліди `{nick}` у мережі...")
    res = await OsintService.check_nickname(nick)
    await wait.edit_text(res, parse_mode="Markdown", disable_web_page_preview=True)

# Обробка пробиття IP через !
@dp.message(F.text.startswith("!"))
async def search_ip(message: Message):
    ip = message.text[1:].strip()
    res = await OsintService.ip_lookup(ip)
    await message.answer(res, parse_mode="Markdown")

@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    await cb.message.edit_text(f"📊 **SERVER:** CPU `{cpu}%` | RAM `{ram}%`", reply_markup=main_kb())

@dp.callback_query(F.data == "off")
async def cb_off(cb: CallbackQuery):
    await cb.message.edit_text("🔌 Offline.")
    os._exit(0)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
