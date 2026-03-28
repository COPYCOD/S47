import os
import asyncio
import logging
import psutil
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from typing import Dict

# ════════════════════════════════════════
# ВИПРАВЛЕНО: BaseMiddleware тепер імпортований
# ════════════════════════════════════════
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ════════════════════════════════════════
# 1. ЛОГУВАННЯ + КОНФІГУРАЦІЯ
# ════════════════════════════════════════
LOG_FILE = "s47_overlord.log"
log_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024,
    backupCount=5, encoding="utf-8"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[log_handler, logging.StreamHandler()]
)
logger = logging.getLogger("S47-OVERLORD")

load_dotenv()

class Config:
    TOKEN       = os.getenv("BOT_TOKEN")
    ADMIN_ID    = int(os.getenv("ADMIN_ID", 0))
    CPU_LIMIT   = int(os.getenv("CPU_THRESHOLD",  90))
    RAM_LIMIT   = int(os.getenv("RAM_THRESHOLD",  85))
    DISK_LIMIT  = int(os.getenv("DISK_THRESHOLD", 95))
    # ВИПРАВЛЕНО: NET_INTERFACE тепер читається з .env (як на скріні)
    NET_IFACE   = os.getenv("NET_INTERFACE", "eth0")
    THROTTLE_S  = float(os.getenv("THROTTLE_SEC", 0.8))
    ALERT_CD_S  = int(os.getenv("ALERT_COOLDOWN_SEC", 900))  # 15 хвилин

if not Config.TOKEN or not Config.ADMIN_ID:
    logger.critical("Відсутні BOT_TOKEN або ADMIN_ID у .env!")
    exit(1)

# ════════════════════════════════════════
# 2. MIDDLEWARE — ВИПРАВЛЕНО ВСІ БАГИ
# ════════════════════════════════════════
class SentinelAuthMiddleware(BaseMiddleware):
    """
    ВИПРАВЛЕНО:
    - user_id замість user.id (NameError у оригіналі)
    - Єдиний middleware для messages і callbacks
    """
    def __init__(self):
        self._cache: Dict[int, float] = {}
        super().__init__()

    async def __call__(self, handler, event, data):
        # Отримуємо user як з Message так і з CallbackQuery
        user = getattr(event, "from_user", None)
        if user is None or user.id != Config.ADMIN_ID:
            if user:
                logger.warning(
                    f"Несанкціонований доступ: id={user.id} @{user.username}"
                )
            return

        now = time.monotonic()  # ПОКРАЩЕНО: monotonic стабільніший за time()
        uid = user.id
        if uid in self._cache and now - self._cache[uid] < Config.THROTTLE_S:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Зачекайте секунду...", show_alert=False)
            return

        self._cache[uid] = now
        asyncio.create_task(self._evict(uid, now))
        return await handler(event, data)

    async def _evict(self, uid: int, ts: float):
        await asyncio.sleep(Config.THROTTLE_S + 1)
        if self._cache.get(uid) == ts:
            self._cache.pop(uid, None)

# ════════════════════════════════════════
# 3. SYSTEM SERVICE
# ════════════════════════════════════════
class SystemService:

    @staticmethod
    def fmt(n: float) -> str:
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.2f} {u}"
            n /= 1024
        return f"{n:.2f} PB"

    @staticmethod
    def uptime() -> str:
        s = int(time.time() - psutil.boot_time())
        d, r = divmod(s, 86400)
        h, r = divmod(r, 3600)
        m    = r // 60
        return f"{d}д {h}г {m}хв"

    @classmethod
    async def full_report(cls) -> str:
        cpu  = await asyncio.to_thread(psutil.cpu_percent, 1)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net  = psutil.net_io_counters()
        load = psutil.getloadavg()          # НОВЕ: load average (1/5/15 хв)
        temp = cls._cpu_temp()              # НОВЕ: температура (якщо доступна)

        return (
            f"🚀 **S47 OVERLORD — ПОВНИЙ ЗВІТ**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥  CPU:   `{cpu}%` (ліміт {Config.CPU_LIMIT}%){temp}\n"
            f"🧠  RAM:   `{ram.percent}%` — вільно `{cls.fmt(ram.available)}`\n"
            f"💾  Disk:  `{disk.percent}%` — вільно `{cls.fmt(disk.free)}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊  Load:  `{load[0]:.2f}` / `{load[1]:.2f}` / `{load[2]:.2f}`\n"
            f"📡  Net ↓  `{cls.fmt(net.bytes_recv)}`  ↑ `{cls.fmt(net.bytes_sent)}`\n"
            f"📦  Pkt ↓  `{net.packets_recv}`   ↑ `{net.packets_sent}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱  Uptime: `{cls.uptime()}`\n"
            f"📅  Час:   `{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}`"
        )

    @staticmethod
    def _cpu_temp() -> str:
        """НОВЕ: температура CPU якщо psutil підтримує на цій ОС."""
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return ""
            for key in ("coretemp", "cpu_thermal", "k10temp"):
                if key in temps:
                    t = temps[key][0].current
                    return f"  🌡 `{t:.0f}°C`"
        except (AttributeError, Exception):
            pass
        return ""

    @classmethod
    async def net_report(cls) -> str:
        """ВИПРАВЛЕНО: net_info callback тепер має повний хендлер."""
        net   = psutil.net_io_counters()
        iface = Config.NET_IFACE

        # Статистика конкретного інтерфейсу
        per = psutil.net_io_counters(pernic=True)
        iface_line = ""
        if iface in per:
            i = per[iface]
            iface_line = (
                f"\n📌 **{iface}:**\n"
                f"   ↓ `{cls.fmt(i.bytes_recv)}`  ↑ `{cls.fmt(i.bytes_sent)}`"
            )

        # З'єднання (потребує root на деяких ОС)
        try:
            conns = len(psutil.net_connections())
            conn_line = f"`{conns}` активних з'єднань"
        except psutil.AccessDenied:
            conn_line = "🚫 root потрібен для з'єднань"

        # Активні інтерфейси
        up = [n for n, s in psutil.net_if_stats().items() if s.isup]

        return (
            f"🌐 **МЕРЕЖЕВА СТАТИСТИКА**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 Всього отримано:  `{cls.fmt(net.bytes_recv)}`\n"
            f"📤 Всього відправлено: `{cls.fmt(net.bytes_sent)}`\n"
            f"📦 Пакети ↓/↑: `{net.packets_recv}` / `{net.packets_sent}`\n"
            f"❌ Помилки ↓/↑: `{net.errin}` / `{net.errout}`\n"
            f"🗑 Dropped ↓/↑: `{net.dropin}` / `{net.dropout}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Активні: `{', '.join(up)}`\n"
            f"🔌 З'єднань: {conn_line}"
            f"{iface_line}"
        )

    @staticmethod
    async def top_processes(n: int = 10) -> str:
        """ПОКРАЩЕНО: винесено в asyncio.to_thread, не блокує event loop."""
        def _collect():
            procs = []
            for p in psutil.process_iter(["name", "cpu_percent", "memory_percent", "pid"]):
                try:
                    if (p.info["cpu_percent"] or 0) > 0:
                        procs.append(p.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return sorted(procs, key=lambda x: x["cpu_percent"] or 0, reverse=True)[:n]

        top = await asyncio.to_thread(_collect)
        lines = ["⚙️ **ТОП ПРОЦЕСІВ (CPU)**\n━━━━━━━━━━━━━━━━━━━━"]
        for p in top:
            lines.append(
                f"`{str(p['pid']):<6}` "
                f"`{p['name'][:14]:<14}` "
                f"C:`{p['cpu_percent']:>5.1f}%` "
                f"R:`{p['memory_percent']:>4.1f}%`"
            )
        return "\n".join(lines) if len(lines) > 1 else "❌ Активних процесів не знайдено."

# ════════════════════════════════════════
# 4. КЛАВІАТУРИ
# ════════════════════════════════════════
def main_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📊 Статистика",   callback_data="stats"))
    b.row(
        InlineKeyboardButton(text="⚙️ Процеси",  callback_data="procs"),
        InlineKeyboardButton(text="🌐 Мережа",   callback_data="net"),
    )
    b.row(
        InlineKeyboardButton(text="📋 Логи",     callback_data="logs"),
        InlineKeyboardButton(text="❤️ Health",   callback_data="health"),
    )
    b.row(InlineKeyboardButton(text="🔌 Shutdown", callback_data="shutdown_confirm"))
    return b

def back_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="⬅️ Меню", callback_data="menu"))
    return b

# ════════════════════════════════════════
# 5. БОТ + ХЕНДЛЕРИ
# ════════════════════════════════════════
bot = Bot(token=Config.TOKEN)
dp  = Dispatcher()

# ВИПРАВЛЕНО: middleware застосований і до messages, і до callbacks
auth = SentinelAuthMiddleware()
dp.message.middleware(auth)
dp.callback_query.middleware(auth)

# ── /start ──────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🦾 **S47 OVERLORD ACTIVATED.**\n"
        "Пане Командире, система під повним контролем.",
        reply_markup=main_kb().as_markup(),
        parse_mode="Markdown"
    )

# ── Головне меню (кнопка «назад») ───────
@dp.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    await cb.message.edit_text(
        "🦾 **S47 OVERLORD** — Головне меню",
        reply_markup=main_kb().as_markup(),
        parse_mode="Markdown"
    )

# ── Статистика ───────────────────────────
@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    await cb.answer()
    report = await SystemService.full_report()
    try:
        await cb.message.edit_text(
            report,
            parse_mode="Markdown",
            reply_markup=back_kb().as_markup()
        )
    except Exception:
        await cb.answer("Дані не змінились", show_alert=False)

# ── Процеси ──────────────────────────────
@dp.callback_query(F.data == "procs")
async def cb_procs(cb: CallbackQuery):
    await cb.answer()
    text = await SystemService.top_processes()
    await cb.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=back_kb().as_markup()
    )

# ── Мережа (ВИПРАВЛЕНО: був відсутній хендлер) ──
@dp.callback_query(F.data == "net")
async def cb_net(cb: CallbackQuery):
    await cb.answer()
    report = await SystemService.net_report()
    await cb.message.edit_text(
        report, parse_mode="Markdown",
        reply_markup=back_kb().as_markup()
    )

# ── Логи ─────────────────────────────────
@dp.callback_query(F.data == "logs")
async def cb_logs(cb: CallbackQuery):
    await cb.answer()
    if not os.path.exists(LOG_FILE):
        return await cb.answer("Файл логів порожній", show_alert=True)
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 3000))
            text = f.read().decode("utf-8", errors="ignore")
        await cb.message.edit_text(
            f"📋 **ОСТАННІ ПОДІЇ:**\n```\n{text}\n```",
            parse_mode="Markdown",
            reply_markup=back_kb().as_markup()
        )
    except Exception as e:
        await cb.answer(f"Помилка: {e}", show_alert=True)

# ── Health ───────────────────────────────
@dp.callback_query(F.data == "health")
async def cb_health(cb: CallbackQuery):
    await cb.answer()
    cpu  = psutil.cpu_percent(interval=None)
    ram  = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent

    ok = cpu < Config.CPU_LIMIT and ram < Config.RAM_LIMIT and disk < Config.DISK_LIMIT
    icon = "🟢 OK" if ok else "🔴 УВАГА"

    await cb.message.edit_text(
        f"❤️ **HEALTHCHECK — {icon}**\n"
        f"CPU `{cpu}%` | RAM `{ram}%` | Disk `{disk}%`\n"
        f"Uptime: `{SystemService.uptime()}`",
        parse_mode="Markdown",
        reply_markup=back_kb().as_markup()
    )

# ── Shutdown ─────────────────────────────
@dp.callback_query(F.data == "shutdown_confirm")
async def cb_shutdown_confirm(cb: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="✅ ТАК, вимкнути", callback_data="shutdown_exec"))
    kb.add(InlineKeyboardButton(text="❌ Скасувати",     callback_data="menu"))
    await cb.message.edit_text(
        "⚠️ **Вимкнути S47 Overlord?**\nМоніторинг зупиниться повністю.",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "shutdown_exec")
async def cb_shutdown_exec(cb: CallbackQuery):
    await cb.message.edit_text("🔌 **S47 OVERLORD OFFLINE.** Честь маю, Командире.")
    await bot.session.close()
    os._exit(0)

# ════════════════════════════════════════
# 6. ФОНОВИЙ МОНІТОРИНГ
#    ВИПРАВЛЕНО: Disk alert тепер є (був у .env але відсутній у коді)
# ════════════════════════════════════════
async def alert_monitor():
    logger.info("Фоновий моніторинг S47-OVERLORD запущено.")
    psutil.cpu_percent(interval=None)  # прогрів
    cooldowns: Dict[str, float] = {}

    def can_alert(key: str) -> bool:
        now = time.monotonic()
        if now - cooldowns.get(key, 0) > Config.ALERT_CD_S:
            cooldowns[key] = now
            return True
        return False

    while True:
        try:
            cpu  = await asyncio.to_thread(psutil.cpu_percent, 0.5)
            ram  = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent

            checks = [
                ("cpu",  cpu,  Config.CPU_LIMIT,  f"🚨 **CPU CRITICAL:** `{cpu}%`!"),
                ("ram",  ram,  Config.RAM_LIMIT,  f"🚨 **RAM CRITICAL:** `{ram}%`! Можливий OOM!"),
                # ВИПРАВЛЕНО: disk тепер теж алертить
                ("disk", disk, Config.DISK_LIMIT, f"🚨 **DISK CRITICAL:** `{disk}%`! Місце закінчується!"),
            ]

            for key, val, limit, msg in checks:
                if val > limit and can_alert(key):
                    await bot.send_message(Config.ADMIN_ID, msg, parse_mode="Markdown")
                    logger.warning(f"ALERT відправлено: {key}={val}%")

        except Exception as e:
            logger.error(f"Monitor error: {e}")
            await asyncio.sleep(10)

        await asyncio.sleep(30)

# ════════════════════════════════════════
# 7. MAIN
# ════════════════════════════════════════
async def main():
    monitor = asyncio.create_task(alert_monitor())
    logger.info("S47 Overlord запущено та готовий.")
    try:
        await dp.start_polling(bot)
    finally:
        monitor.cancel()
        await bot.session.close()
        logger.info("S47 Overlord зупинено.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Вихід.")