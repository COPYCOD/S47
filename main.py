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

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, 
    InlineKeyboardButton, InlineKeyboardMarkup
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ════════════════════════════════════════
# 1. СУВОРИЙ КОНФІГ ТА ДІАГНОСТИКА (100/100)
# ════════════════════════════════════════
load_dotenv()

class Config:
    TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    # Тайм-аути для запобігання флуду
    THROTTLE_SEC = 2
    # Список файлів для швидкого доступу
    SENSITIVE_DOCS = ["/etc/passwd", "/etc/hosts", "/proc/version"]

LOG_FILE = "sherlock_ultimate.log"
handler = RotatingFileHandler(LOG_FILE, maxBytes=15*1024*1024, backupCount=3, encoding='utf-8')
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s]: %(message)s', 
    handlers=[handler, logging.StreamHandler()]
)
logger = logging.getLogger("SHERLOCK-ULTIMATE")

if not Config.TOKEN or not Config.ADMIN_ID:
    logger.critical("КРИТИЧНО: Перевірте змінні оточення (TOKEN/ADMIN_ID)!")
    exit(1)

# ════════════════════════════════════════
# 2. MIDDLEWARE ДЛЯ АВТОРИЗАЦІЇ ТА ТРОТЛІНГУ
# ════════════════════════════════════════
class SherlockGuard(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self.last_action = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user or user.id != Config.ADMIN_ID:
            logger.warning(f"⛔️ Відмовлено в доступі: {user.id if user else 'Unknown'}")
            return

        # Захист від спаму кнопками
        now = time.monotonic()
        if user.id in self.last_action and now - self.last_action[user.id] < 0.5:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Зачекайте...", show_alert=False)
            return
        
        self.last_action[user.id] = now
        return await handler(event, data)

# ════════════════════════════════════════
# 3. ІНТЕЛЕКТУАЛЬНІ МОДУЛІ (OSINT + SYS)
# ════════════════════════════════════════
class IntelModule:
    @staticmethod
    async def get_osint_dossier(nickname: str) -> str:
        targets = {
            "GitHub": f"https://github.com/{nickname}",
            "Twitter": f"https://twitter.com/{nickname}",
            "Instagram": f"https://instagram.com/{nickname}",
            "Reddit": f"https://reddit.com/user/{nickname}",
            "Telegram": f"https://t.me/{nickname}"
        }
        found = []
        async with httpx.AsyncClient(timeout=7.0, follow_redirects=True) as client:
            tasks = [client.get(url) for url in targets.values()]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            for (name, url), resp in zip(targets.items(), responses):
                if not isinstance(resp, Exception) and resp.status_code == 200:
                    found.append(f"✅ **{name}**: [Link]({url})")
        
        return "🔍 **OSINT ЗВІТ:**\n\n" + ("\n".join(found) if found else "Нічого не знайдено.")

    @staticmethod
    def get_system_stats():
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        try:
            load = os.getloadavg()
        except:
            load = (0, 0, 0)
        
        uptime = str(timedelta(seconds=int(time.time() - psutil.boot_time())))
        return (
            f"📊 **СТАТУС СИСТЕМИ:**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ CPU: `{cpu}%` | RAM: `{ram}%`\n"
            f"📈 Load: `{load[0]} {load[1]}`\n"
            f"⏱️ Uptime: `{uptime}`\n"
            f"🧬 Host: `{socket.gethostname()}`"
        )

# ════════════════════════════════════════
# 4. ГОЛОВНИЙ ІНТЕРФЕЙС
# ════════════════════════════════════════
def get_main_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🕵️ Пробити людину", callback_data="ask_nick"))
    b.row(InlineKeyboardButton(text="🌍 Пробити IP", callback_data="ask_ip"))
    b.row(
        InlineKeyboardButton(text="📊 Метрики", callback_data="stats"),
        InlineKeyboardButton(text="🛡️ Безпека", callback_data="sec_check")
    )
    b.row(InlineKeyboardButton(text="📟 Terminal Mode", callback_data="term_help"))
    b.row(InlineKeyboardButton(text="🔌 Вимкнути", callback_data="kill"))
    return b.as_markup()

# ════════════════════════════════════════
# 5. ХЕНДЛЕРИ
# ════════════════════════════════════════
bot = Bot(token=Config.TOKEN)
dp = Dispatcher()
dp.message.middleware(SherlockGuard())
dp.callback_query.middleware(SherlockGuard())

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("🦾 **S47 SHERLOCK ULTIMATE v3.0**\nВсі модулі активовані. Railway Deploy Stable.", reply_markup=get_main_kb())

@dp.callback_query(F.data == "ask_nick")
async def cb_ask_nick(cb: CallbackQuery):
    await cb.message.answer("🕵️ Введіть нікнейм для розшуку з префіксом `?` (напр: `?elonmusk`)")
    await cb.answer()

@dp.callback_query(F.data == "ask_ip")
async def cb_ask_ip(cb: CallbackQuery):
    await cb.message.answer("🌍 Введіть IP для аналізу з префіксом `!` (напр: `!8.8.8.8`)")
    await cb.answer()

@dp.message(F.text.startswith("?"))
async def handle_osint(message: Message):
    nick = message.text[1:].strip()
    wait = await message.answer(f"🔎 Шерлок аналізує сліди `{nick}`...")
    report = await IntelModule.get_osint_dossier(nick)
    await wait.edit_text(report, parse_mode="Markdown", disable_web_page_preview=True)

@dp.message(F.text.startswith("!"))
async def handle_ip(message: Message):
    ip = message.text[1:].strip()
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"http://ip-api.com/json/{ip}")
            d = r.json()
            res = f"📍 **IP {ip}:** `{d.get('city')}, {d.get('country')}`\n📡 ISP: `{d.get('isp')}`"
            await message.answer(res)
        except:
            await message.answer("❌ Помилка сервісу.")

@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    text = IntelModule.get_system_stats()
    await cb.message.edit_text(text, reply_markup=get_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "term_help")
async def cb_term(cb: CallbackQuery):
    await cb.message.answer("📟 Напишіть команду через `$` (напр: `$ ls -la`)")
    await cb.answer()

@dp.message(F.text.startswith("$"))
async def handle_shell(message: Message):
    cmd = message.text[1:].strip()
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    res = (stdout + stderr).decode().strip() or "Виконано."
    await message.answer(f"📝 **Вивід:**\n
