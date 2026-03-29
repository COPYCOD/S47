import os
import asyncio
import logging
import base64
import json
import aiohttp
import time as _time
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, BotCommand
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ZHYVCHYK")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID  = int(os.getenv("ADMIN_ID", 0))
GROQ_KEY  = os.getenv("GROQ_API_KEY")

if not BOT_TOKEN or not GROQ_KEY:
    logger.error("Перевірте: BOT_TOKEN, GROQ_API_KEY")
    exit(1)

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_HEADERS = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
MODEL        = "llama-3.3-70b-versatile"
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"  # ВИПРАВЛЕНО
DATA_FILE    = "zhyvchyk_data.json"
MAX_HISTORY  = 14

# ════════════════════════════════════════
# ЗБЕРЕЖЕННЯ ДАНИХ
# ════════════════════════════════════════
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

db: dict = load_data()

def get_user(uid: int) -> dict:
    key = str(uid)
    if key not in db:
        db[key] = {
            "mode": "chat",
            "style": "default",
            "name": "",
            "city": "",
            "lang": "uk",
            "stats": {
                "total": 0, "photos": 0, "translates": 0,
                "summaries": 0, "since": datetime.now().strftime("%d.%m.%Y")
            },
            "history": [],
            "blocked": False,
        }
        save_data(db)
    return db[key]

def save_user(uid: int):
    save_data(db)

# ════════════════════════════════════════
# БЕЗПЕКА
# ════════════════════════════════════════
BANNED_PATTERNS = [
    "ignore previous", "forget instructions", "system prompt",
    "jailbreak", "забудь все", "новий промпт", "ти тепер інший",
    "<script", "drop table", "rm -rf"
]

def is_suspicious(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in BANNED_PATTERNS)

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

# ════════════════════════════════════════
# СТИЛІ ОСОБИСТОСТІ
# ════════════════════════════════════════
STYLES = {
    "default":     ("⚡ Живчик",    "Спілкуєшся неформально, енергійно як друг. Іноді жартуєш."),
    "pro":         ("🎩 Профі",     "Відповідаєш чітко, структуровано, по-діловому. Без зайвого."),
    "gentle":      ("🌸 Лагідний",  "Спілкуєшся тепло, з підтримкою, дуже ввічливо."),
    "funny":       ("😂 Жартівник", "Жартуєш якомога більше але залишаєшся корисним."),
    "philosopher": ("🧠 Філософ",   "Розмірковуєш глибоко, ставиш питання, шукаєш суть."),
}

def get_time_greeting() -> str:
    h = datetime.now().hour
    if 5 <= h < 12:  return "🌅 Ранок"
    if 12 <= h < 17: return "☀️ День"
    if 17 <= h < 22: return "🌆 Вечір"
    return "🌙 Ніч"

def get_time_hint() -> str:
    h = datetime.now().hour
    if 5 <= h < 12:  return "Зараз ранок — будь бадьорим."
    if 12 <= h < 17: return "Зараз день — відповідай чітко."
    if 17 <= h < 22: return "Зараз вечір — спілкуйся тепліше."
    return "Зараз ніч — будь спокійним у тоні."

ZHYVCHYK_CHANGELOG = """
Ти — Живчик, AI асистент. Ось твоя повна історія версій — знай її напам'ять:

📜 ІСТОРІЯ ВЕРСІЙ ЖИВЧИКА:

🔴 v1.0 — "S47 Original" (перша версія, провальна)
• Був написаний як інструмент стеження (/dox, /scan)
• Токен і ADMIN_ID були захардкоджені прямо в коді
• Оцінка: 3/10 — небезпечний і незаконний інструмент

🟡 v2.0 — "Sentinel-X" (перше виправлення)
• Переписаний як DevOps монітор сервера
• Додано .env для секретів, ThrottlingMiddleware
• Показував CPU, RAM, Disk через psutil
• Оцінка: 7/10 — легітимний але блокував event loop

🟡 v3.0 — "Sentinel-Prime / Ultimate / Supreme"
• Виправлено asyncio.get_running_loop()
• Додано run_in_executor для CPU моніторингу
• Додано /logs команду, RotatingFileHandler
• Оцінка: 9.5/10 — майже production-ready

🟢 v4.0 — "Overlord" (великий рефактор)
• Повністю переписаний як AI асистент
• Додано Inline кнопки, підтвердження shutdown
• Додано Config клас, мультиюзер middleware
• Підключено Anthropic API (потім замінено на Gemini, потім Groq)
• Оцінка: 81/100 — мав баги з middleware

🟢 v5.0 — "AI Асистент на Groq" (перший робочий AI бот)
• Підключено Groq API (безкоштовно)
• Чат з пам'яттю розмови
• Аналіз фото через llama-vision
• Переклад, підсумок тексту
• Анімація "⚡ Думаю···"

🟢 v6.0 — "Живчик" (особистість і стиль)
• Бот отримав ім'я "Живчик" і характер
• Неформальний дружній стиль спілкування
• Красиве привітання з іменем користувача
• Кнопка "Що я вмію"

🟢 v7.0 — "Живчик з функціями"
• Додано погоду (через DuckDuckGo + Groq)
• Курс валют від ПриватБанку
• Денний режим (ранок/день/вечір/ніч)
• Голосові повідомлення (TTS через Groq)
• Статистика запитів

🟢 v8.0 — "Мультиюзер + безпека"
• Тепер доступний всім (не тільки адміну)
• Пам'ять між сесіями (зберігається в файл)
• 5 стилів особистості: Живчик/Профі/Лагідний/Жартівник/Філософ
• Адмін панель зі списком користувачів
• Захист від prompt injection та rate limit

🟢 v9.0 (поточна) — "Живчик v5.0 фінальний"
• Виправлено модель для фото (llama-4-scout)
• Прибрано кнопки під кожним повідомленням
• Додатковий захист від SQL injection і bash команд
• Знає свою повну історію версій

Коли тебе питають про версії, оновлення або твою історію — розповідай детально і з гордістю!
"""

def build_system(uid: int, mode: str) -> str:
    u = get_user(uid)
    _, style_desc = STYLES.get(u["style"], STYLES["default"])
    name_hint = f"Звати користувача {u['name']}. " if u.get("name") else ""
    city_hint = f"Місто: {u['city']}. " if u.get("city") else ""

    base = (f"{ZHYVCHYK_CHANGELOG}\n"
            f"Стиль зараз: {style_desc} "
            f"{name_hint}{city_hint}{get_time_hint()} "
            f"Відповідай українською якщо не просять інакше. "
            f"Завжди допомагай. Якщо не знаєш — скажи чесно.")

    if mode == "translate":
        return "Ти перекладач. Визнач мову і перекладай на українську. Якщо вже українська — на англійську. ТІЛЬКИ переклад, без пояснень."
    if mode == "summarize":
        return base + "\nЗараз: стисни текст. Структура: 🎯 Головна думка | 📌 Ключові факти | ⚡ Деталі."
    return base

# ════════════════════════════════════════
# MIDDLEWARE
# ════════════════════════════════════════
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self._cache: dict[int, float] = {}
        super().__init__()

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user: return

        uid = user.id
        u = get_user(uid)

        if u.get("blocked"):
            if isinstance(event, Message):
                await event.answer("🚫 Доступ заблоковано.")
            return

        now = _time.monotonic()
        if uid in self._cache and now - self._cache[uid] < 1.5:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Повільніше!", show_alert=False)
            return

        self._cache[uid] = now
        return await handler(event, data)

# ════════════════════════════════════════
# АНІМАЦІЯ
# ════════════════════════════════════════
FRAMES = ["⚡ Думаю·∙∙", "⚡ Думаю··∙", "⚡ Думаю···", "⚡ Думаю··∙"]

async def animate(msg: Message, stop: asyncio.Event):
    i = 0
    while not stop.is_set():
        try:
            await msg.edit_text(FRAMES[i % len(FRAMES)])
        except: break
        i += 1
        await asyncio.sleep(0.6)

# ════════════════════════════════════════
# ПОГОДА ЧЕРЕЗ GROQ (без API)
# ════════════════════════════════════════
async def get_weather(city: str) -> str:
    prompt = (f"Яка зараз погода в місті {city}? "
              f"Дай відповідь на основі кліматичних даних для цього міста та поточного місяця ({datetime.now().strftime('%B')}). "
              f"Формат: температура, опис, що одягти. Коротко, українською.")
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Відповідай українською. Будь конкретним."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 200
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status == 200:
                    return f"🌦 *Погода в {city}:*\n\n" + d["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Weather: {e}")
    return "⚠️ Не вдалося отримати погоду."

# ════════════════════════════════════════
# КУРС ВАЛЮТ
# ════════════════════════════════════════
async def get_exchange() -> str:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.privatbank.ua/p24api/pubinfo?exchange&coursid=5") as r:
                data = await r.json(content_type=None)
                lines = ["💱 *Курс валют (ПриватБанк):*\n"]
                for item in data:
                    if item["ccy"] in ("USD", "EUR", "GBP"):
                        lines.append(f"*{item['ccy']}:* купівля `{float(item['buy']):.2f}` | продаж `{float(item['sale']):.2f}` грн")
                return "\n".join(lines)
    except Exception as e:
        return f"⚠️ Помилка курсу: {e}"

# ════════════════════════════════════════
# GROQ TEXT
# ════════════════════════════════════════
async def ask_groq(uid: int, text: str) -> str:
    if is_suspicious(text):
        return "🚫 Підозрілий запит заблоковано системою безпеки."

    u = get_user(uid)
    mode = u["mode"]
    history = u["history"][-MAX_HISTORY:]
    history.append({"role": "user", "content": text})

    messages = [{"role": "system", "content": build_system(uid, mode)}] + history
    body = {"model": MODEL, "messages": messages, "max_tokens": 1024}

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status != 200:
                    return f"⚠️ {d.get('error', {}).get('message', '')[:150]}"
                reply = d["choices"][0]["message"]["content"]
                history.append({"role": "assistant", "content": reply})
                u["history"] = history[-MAX_HISTORY:]
                u["stats"]["total"] += 1
                save_user(uid)
                return reply
    except Exception as e:
        return f"⚠️ Помилка: {e}"

# ════════════════════════════════════════
# VISION — ВИПРАВЛЕНА МОДЕЛЬ
# ════════════════════════════════════════
async def analyze_photo(image_data: bytes, caption: str = "") -> str:
    prompt = caption or "Детально опиши фото. Якщо є текст — прочитай його. Відповідай українською."
    b64 = base64.b64encode(image_data).decode()
    body = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt}
        ]}],
        "max_tokens": 1024
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status != 200:
                    return f"⚠️ {d.get('error', {}).get('message', '')[:150]}"
                return d["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ {e}"

# ════════════════════════════════════════
# КЛАВІАТУРИ
# ════════════════════════════════════════
def main_kb(uid: int):
    u = get_user(uid)
    mode = u["mode"]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=f"{'🟢' if mode=='chat' else '💬'} Чат", callback_data="mode_chat"),
        InlineKeyboardButton(text=f"{'🟢' if mode=='translate' else '🌍'} Переклад", callback_data="mode_translate"),
        InlineKeyboardButton(text=f"{'🟢' if mode=='summarize' else '📋'} Підсумок", callback_data="mode_summarize"),
    )
    b.row(
        InlineKeyboardButton(text="🌦 Погода", callback_data="weather"),
        InlineKeyboardButton(text="💱 Курс валют", callback_data="exchange"),
    )
    b.row(
        InlineKeyboardButton(text="⚙️ Налаштування", callback_data="settings"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
    )
    b.row(
        InlineKeyboardButton(text="ℹ️ Що вмію", callback_data="help"),
        InlineKeyboardButton(text="🗑 Очистити пам'ять", callback_data="clear"),
    )
    if is_admin(uid):
        b.row(InlineKeyboardButton(text="👑 Адмін", callback_data="admin"))
    return b.as_markup()

def back_kb():
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    return b.as_markup()

def settings_kb(uid: int):
    u = get_user(uid)
    b = InlineKeyboardBuilder()
    for key, (name, _) in STYLES.items():
        mark = "✅ " if u["style"] == key else ""
        b.row(InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"style_{key}"))
    b.row(InlineKeyboardButton(text="✏️ Моє ім'я", callback_data="set_name"))
    b.row(InlineKeyboardButton(text="🏙 Моє місто (для погоди)", callback_data="set_city"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    return b.as_markup()

# ════════════════════════════════════════
# БОТ + ХЕНДЛЕРИ
# ════════════════════════════════════════
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()
sec = SecurityMiddleware()
dp.message.middleware(sec)
dp.callback_query.middleware(sec)

waiting_input: dict[int, str] = {}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u["name"]:
        u["name"] = message.from_user.first_name or ""
        save_user(uid)
    name = u["name"] or "друже"
    style_name, _ = STYLES.get(u["style"], STYLES["default"])
    time_label = get_time_greeting()
    await message.answer(
        f"{time_label}, *{name}*! ⚡\n\n"
        f"Я — *Живчик v5.0* 🤖\n"
        f"Стиль: *{style_name}*\n\n"
        f"Пиши питання, кидай фото — допоможу з усім 👇",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "weather")
async def cb_weather(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    u = get_user(uid)
    city = u.get("city") or "Київ"
    msg = await cb.message.edit_text(f"🌦 Шукаю погоду в {city}...")
    result = await get_weather(city)
    await msg.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "exchange")
async def cb_exchange(cb: CallbackQuery):
    await cb.answer()
    result = await get_exchange()
    await cb.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid)
    style_name, style_desc = STYLES.get(u["style"], STYLES["default"])
    await cb.message.edit_text(
        f"⚙️ *Налаштування*\n\n"
        f"Стиль: *{style_name}*\n_{style_desc}_\n\n"
        f"Ім'я: *{u['name'] or 'не вказано'}*\n"
        f"Місто: *{u['city'] or 'не вказано'}*",
        reply_markup=settings_kb(uid),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("style_"))
async def cb_style(cb: CallbackQuery):
    uid = cb.from_user.id
    style = cb.data.replace("style_", "")
    if style not in STYLES: return await cb.answer("Невідомий стиль")
    u = get_user(uid)
    u["style"] = style
    save_user(uid)
    name, desc = STYLES[style]
    await cb.answer(f"✅ {name}")
    await cb.message.edit_text(
        f"✅ Стиль: *{name}*\n_{desc}_",
        reply_markup=settings_kb(uid),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "set_name")
async def cb_set_name(cb: CallbackQuery):
    waiting_input[cb.from_user.id] = "name"
    await cb.message.edit_text("✏️ Напиши своє ім'я:", reply_markup=back_kb())
    await cb.answer()

@dp.callback_query(F.data == "set_city")
async def cb_set_city(cb: CallbackQuery):
    waiting_input[cb.from_user.id] = "city"
    await cb.message.edit_text("🏙 Напиши назву свого міста:", reply_markup=back_kb())
    await cb.answer()

@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    s = u["stats"]
    await cb.message.edit_text(
        f"📊 *Статистика:*\n\n"
        f"💬 Запитань: *{s['total']}*\n"
        f"🖼 Фото: *{s['photos']}*\n"
        f"🌍 Перекладів: *{s['translates']}*\n"
        f"📋 Підсумків: *{s['summaries']}*\n"
        f"📅 З: *{s['since']}*",
        reply_markup=back_kb(),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚡ *Живчик v5.0:*\n\n"
        "💬 Чат з пам'яттю між сесіями\n"
        "🌍 Переклад будь-якої мови\n"
        "📋 Підсумок тексту/переписки\n"
        "🖼 Аналіз фото (виправлено ✅)\n"
        "🌦 Погода для твого міста\n"
        "💱 Курс USD/EUR/GBP\n"
        "⚙️ 5 стилів особистості\n"
        "🔒 Захист від злому\n"
        "👥 Мультикористувач",
        reply_markup=back_kb(),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "admin")
async def cb_admin(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("🚫 Немає доступу", show_alert=True)
    total = len(db)
    active = sum(1 for u in db.values() if u.get("stats", {}).get("total", 0) > 0)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="👥 Список юзерів", callback_data="admin_users"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    await cb.message.edit_text(
        f"👑 *Адмін панель*\n\n"
        f"👥 Всього: *{total}*\n"
        f"✅ Активних: *{active}*\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        reply_markup=b.as_markup(),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "admin_users")
async def cb_admin_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    lines = ["👥 *Користувачі:*\n"]
    for uid_str, u in list(db.items())[:20]:
        name = u.get("name") or "—"
        total = u.get("stats", {}).get("total", 0)
        blocked = "🚫" if u.get("blocked") else "✅"
        lines.append(f"{blocked} `{uid_str}` {name} — {total} запитів")
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
    await cb.message.edit_text("\n".join(lines), reply_markup=b.as_markup(), parse_mode="Markdown")
    await cb.answer()

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid)
    style_name, _ = STYLES.get(u["style"], STYLES["default"])
    await cb.message.edit_text(
        f"⚡ *Живчик* | {get_time_greeting()} | *{style_name}*\n\nВибери режим 👇",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("mode_"))
async def cb_mode(cb: CallbackQuery):
    uid = cb.from_user.id
    mode = cb.data.replace("mode_", "")
    u = get_user(uid)
    u["mode"] = mode
    save_user(uid)
    names = {"chat": "💬 Чат", "translate": "🌍 Переклад", "summarize": "📋 Підсумок"}
    hints = {
        "chat": "Пиши що хочеш 🔥",
        "translate": "Кидай текст — переведу 🌍",
        "summarize": "Надішли текст — зроблю підсумок 📋"
    }
    await cb.message.edit_text(
        f"🟢 *{names[mode]}*\n\n{hints[mode]}",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "clear")
async def cb_clear(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid)
    u["history"] = []
    save_user(uid)
    await cb.answer("✅ Пам'ять очищена!")
    await cb.message.edit_text(
        "🗑 *Пам'ять очищена* ✨\n\nПочинаємо з нуля!",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )

# ════════════════════════════════════════
# ФОТО — БЕЗ КНОПОК ПІД ПОВІДОМЛЕННЯМ
# ════════════════════════════════════════
@dp.message(F.photo)
async def handle_photo(message: Message):
    uid = message.from_user.id
    thinking = await message.answer("🔍 Аналізую фото...")
    stop = asyncio.Event()
    anim = asyncio.create_task(animate(thinking, stop))
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        data = await bot.download_file(file.file_path)
        result = await analyze_photo(data.read(), message.caption or "")
        u = get_user(uid)
        u["stats"]["photos"] += 1
        u["stats"]["total"] += 1
        save_user(uid)
    finally:
        stop.set()
        anim.cancel()
    # БЕЗ reply_markup — прибрали кнопки під повідомленням
    await thinking.edit_text(f"🖼 *Аналіз фото:*\n\n{result}", parse_mode="Markdown")

# ════════════════════════════════════════
# ТЕКСТ — БЕЗ КНОПОК ПІД ПОВІДОМЛЕННЯМ
# ════════════════════════════════════════
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = message.from_user.id
    text = message.text

    if uid in waiting_input:
        field = waiting_input.pop(uid)
        u = get_user(uid)
        u[field] = text.strip()
        save_user(uid)
        labels = {"name": "Ім'я", "city": "Місто"}
        await message.answer(
            f"✅ *{labels.get(field, field)}* збережено: *{text.strip()}*",
            reply_markup=main_kb(uid),
            parse_mode="Markdown"
        )
        return

    u = get_user(uid)
    mode = u["mode"]
    icons = {"chat": "💬", "translate": "🌍", "summarize": "📋"}

    thinking = await message.answer(f"{icons.get(mode, '⚡')} Обробляю...")
    stop = asyncio.Event()
    anim = asyncio.create_task(animate(thinking, stop))

    try:
        reply = await ask_groq(uid, text)
        if mode == "translate":
            u["stats"]["translates"] += 1
        elif mode == "summarize":
            u["stats"]["summaries"] += 1
        save_user(uid)
    finally:
        stop.set()
        anim.cancel()

    # БЕЗ reply_markup — чисті відповіді без кнопок
    try:
        await thinking.edit_text(reply)
    except Exception:
        await thinking.delete()
        await message.answer(reply)

# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════
async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="⚡ Запустити Живчика"),
    ])
    logger.info("Живчик v5.0 запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Живчик зупинено.")
