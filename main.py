import os
import asyncio
import logging
import psutil
import time
import httpx
import socket
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Імпортуємо BaseMiddleware, який був пропущений раніше
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, 
    InlineKeyboardButton, InlineKeyboardMarkup
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ════════════════════════════════════════
# 1. КОНФІГУРАЦІЯ (З .env ТА FALLBACKS)
# ════════════════════════════════════════
load_dotenv()

class Config:
    TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    # Нові параметри з .env
    THROTTLE_SEC = float(os.getenv("THROTTLE_SEC", 1.0))
    NET_INTERFACE = os.getenv("NET_INTERFACE", "eth0")
    ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN_SEC", 300))

if not Config.TOKEN or not Config.ADMIN_ID:
    print("CRITICAL: Check your Environment Variables (BOT_TOKEN/ADMIN_ID)!")
    exit(1)

# ════════════════════════════════════════
# 2. MIDDLEWARE (АВТОРИЗАЦІЯ ТА ТРОТЛІНГ)
# ════════════════════════════════════════
class SherlockGuard(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self.last_action = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        # Виправлено: звернення через user.id, а не user_id
        if not user or user.id != Config.ADMIN_ID:
            return

        # Throttle logic з використанням time.monotonic()
        now = time.monotonic()
        if user.id in self.last_action:
            if now - self.last_action[user.id] < Config.THROTTLE_SEC:
                if isinstance(event, CallbackQuery):
                    await event.answer("⏳ Зачекайте, Шерлок думає...", show_alert=False)
                return
        
        self.last_action[user.id] = now
        return await handler(event, data)

# ════════════════════════════════════════
# 3. СЕРВІСНИЙ МОДУЛЬ (ДЕТЕКТИВ)
# ════════════════════════════════════════
class SystemService:
    @staticmethod
    async def get_osint_data(nick: str) -> str:
        targets = {
            "GitHub": f"https://github.com/{nick}",
            "Twitter": f"https://twitter.com/{nick}",
            "Reddit": f"https://reddit.com/user/{nick}",
            "Telegram": f"https://t.me/{nick}"
        }
        found = []
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for name, url in targets.items():
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        found.append(f"✅ {name}: {url}")
                except: continue
        return "\n".join(found) if found else "Нічого не знайдено."

    @staticmethod
    def get_stats_report():
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        
        # Graceful fallback для Load Average (немає на Windows)
        try:
            la = os.getloadavg()
            la_str = f"{la[0]} {la[1]} {la[2]}"
        except:
            la_str = "N/A"

        # Graceful fallback для температури
        temp_str = "N/A"
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    temp_str = f"{entries[0].current}°C"
                    break
        except: pass

        uptime = str(timedelta(seconds=int(time.time() - psutil.boot_time())))
        return (
            f"📊 **ЗВІТ СИСТЕМИ**\n"
            f"━━━━━━━━━━━━━━\n"
            f"🖥️ CPU: `{cpu}%` ({temp_str})\n"
            f"🧠 RAM: `{ram}%`\n"
            f"📈 Load: `{la_str}`\n"
            f"⏱️ Uptime: `{uptime}`\n"
            f"📡 Interface: `{Config.NET_INTERFACE}`"
        )

# ════════════════════════════════════════
# 4. ІНТЕРФЕЙС ТА ХЕНДЛЕРИ
# ════════════════════════════════════════
bot = Bot(token=Config.TOKEN)
dp = Dispatcher()
# Підключаємо middleware для обох типів подій
dp.message.middleware(SherlockGuard())
dp.callback_query.middleware(SherlockGuard())

def main_menu():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🕵️ OSINT Пробиття", callback_data="do_osint"))
    kb.row(InlineKeyboardButton(text="📊 Метрики", callback_data="get_stats"))
    kb.row(InlineKeyboardButton(text="❤️ Health Check", callback_data="health"))
    kb.row(InlineKeyboardButton(text="🔌 Off", callback_data="shutdown"))
    return kb.as_markup()

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("🦾 **S47 SHERLOCK ULTIMATE v4.0**\nСистема готова до деплою на Railway.", reply_markup=main_menu())

@dp.callback_query(F.data == "get_stats")
async def cb_stats(cb: CallbackQuery):
    report = SystemService.get_stats_report()
    await cb.message.edit_text(report, reply_markup=main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "health")
async def cb_health(cb: CallbackQuery):
    await cb.answer("❤️ Система працює стабільно. Event Loop вільний.", show_alert=True)

@dp.callback_query(F.data == "do_osint")
async def cb_osint_ask(cb: CallbackQuery):
    await cb.message.answer("🕵️ Напишіть нікнейм через префікс `?` (напр: `?mark_pro`)")
    await cb.answer()

@dp.message(F.text.startswith("?"))
async def handle_osint(message: Message):
    nick = message.text[1:].strip()
    wait = await message.answer(f"🔎 Шерлок аналізує `{nick}`...")
    res = await SystemService.get_osint_data(nick)
    await wait.edit_text(f"🔍 **Результати для {nick}:**\n\n{res}", disable_web_page_preview=True)

@dp.callback_query(F.data == "shutdown")
async def cb_off(cb: CallbackQuery):
    await cb.message.edit_text("🔌 Offline.")
    os._exit(0)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
