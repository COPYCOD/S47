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
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, BotCommand, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ZHYVCHYK")

load_dotenv()
BOT_TOKEN   = os.getenv("BOT_TOKEN")
ADMIN_ID    = int(os.getenv("ADMIN_ID", 0))
GEMINI_KEY  = os.getenv("GEMINI_API_KEY")
REDIS_URL   = os.getenv("REDIS_URL", "")

if not BOT_TOKEN or not GEMINI_KEY:
    logger.error("Перевірте: BOT_TOKEN, GEMINI_API_KEY")
    exit(1)

# Gemini endpoints
GEMINI_BASE    = f"https://generativelanguage.googleapis.com/v1beta"
GEMINI_CHAT    = f"{GEMINI_BASE}/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
GEMINI_IMGGEN  = f"{GEMINI_BASE}/models/imagen-3.0-generate-002:predict?key={GEMINI_KEY}"

DATA_FILE   = "zhyvchyk_data.json"
MAX_HISTORY = 16

# ════════════════════════════════════════
# REDIS + JSON FALLBACK
# ════════════════════════════════════════
redis_client = None

async def init_redis():
    global redis_client
    if REDIS_AVAILABLE and REDIS_URL:
        try:
            redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
            await redis_client.ping()
            logger.info("✅ Redis підключено!")
        except Exception as e:
            logger.warning(f"Redis недоступний: {e}")
            redis_client = None

async def db_get(key: str):
    if redis_client:
        try:
            val = await redis_client.get(key)
            return json.loads(val) if val else None
        except Exception: pass
    data = _load_json()
    return data.get(key)

async def db_set(key: str, value: dict):
    if redis_client:
        try:
            await redis_client.set(key, json.dumps(value, ensure_ascii=False))
            return
        except Exception: pass
    data = _load_json()
    data[key] = value
    _save_json(data)

async def db_keys():
    if redis_client:
        try: return list(await redis_client.keys("user:*"))
        except Exception: pass
    return list(_load_json().keys())

def _load_json():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    return {}

def _save_json(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"JSON save: {e}")

# ════════════════════════════════════════
# КОРИСТУВАЧ
# ════════════════════════════════════════
_cache: dict[int, dict] = {}

def _default_user():
    return {
        "mode": "chat", "style": "default",
        "name": "", "city": "",
        "stats": {"total": 0, "photos": 0, "translates": 0,
                  "summaries": 0, "images": 0,
                  "since": datetime.now().strftime("%d.%m.%Y")},
        "history": [], "blocked": False,
    }

async def get_user(uid: int) -> dict:
    if uid in _cache: return _cache[uid]
    data = await db_get(f"user:{uid}")
    if not data: data = _default_user()
    _cache[uid] = data
    return data

async def save_user(uid: int):
    if uid in _cache:
        await db_set(f"user:{uid}", _cache[uid])

def is_admin(uid: int): return uid == ADMIN_ID

# ════════════════════════════════════════
# БЕЗПЕКА
# ════════════════════════════════════════
BANNED = ["ignore previous", "forget instructions", "system prompt",
          "jailbreak", "забудь все", "новий промпт", "<script", "drop table"]

def is_suspicious(text: str) -> bool:
    return any(p in text.lower() for p in BANNED)

import unicodedata
def clean_text(text: str) -> str:
    result = []
    for ch in text:
        name = unicodedata.name(ch, "")
        if any(x in name for x in ["CJK", "ARABIC", "HEBREW", "THAI", "MYANMAR"]):
            continue
        result.append(ch)
    return "".join(result)

# ════════════════════════════════════════
# СТИЛІ
# ════════════════════════════════════════
STYLES = {
    "default":     ("⚡ Живчик",    "дружній, енергійний, підколює але не ображає"),
    "pro":         ("🎩 Профі",     "чіткий, структурований, по-діловому"),
    "gentle":      ("🌸 Лагідний",  "теплий, підтримуючий, дуже ввічливий"),
    "funny":       ("😂 Жартівник", "жартує постійно але залишається корисним"),
    "philosopher": ("🧠 Філософ",   "глибокий, розмірковує, шукає суть"),
}

CHANGELOG = """Твоя історія: v1 S47 stalkerware→v2 DevOps монітор→v3 Sentinel Supreme→
v4 Overlord→v5-9 AI асистент Живчик на Groq→v10 Redis БД→v11 пошук SearXNG→
v12 (поточна) повний переїзд на Gemini 2.0 Flash — розумніший, краще розуміє українську, генерує зображення."""

def get_time_greeting():
    h = datetime.now().hour
    if 5 <= h < 12:  return "🌅 Ранок"
    if 12 <= h < 17: return "☀️ День"
    if 17 <= h < 22: return "🌆 Вечір"
    return "🌙 Ніч"

def get_time_hint():
    h = datetime.now().hour
    if 5 <= h < 12:  return "Зараз ранок — будь бадьорим."
    if 12 <= h < 17: return "Зараз день — відповідай чітко."
    if 17 <= h < 22: return "Зараз вечір — спілкуйся тепліше."
    return "Зараз ніч — будь спокійним."

def build_system(u: dict, mode: str) -> str:
    _, style_desc = STYLES.get(u["style"], STYLES["default"])
    name = f"Користувача звати {u['name']}. " if u.get("name") else ""
    city = f"Місто: {u['city']}. " if u.get("city") else ""

    base = f"""Ти — Живчик, розумний AI асистент. Стиль: {style_desc}.
{name}{city}{get_time_hint()}
{CHANGELOG}

ПРАВИЛА:
- Спілкуєшся українською якщо не просять інакше
- Можеш підколоти жартома але НІКОЛИ не ображаєш
- Якщо не знаєш точно — чесно кажеш, не вигадуєш
- Перед складним питанням думаєш крок за кроком
- НЕ використовуєш китайські/арабські/японські символи
- Якщо є дані з пошуку — використовуй їх"""

    if mode == "translate":
        return "Ти перекладач. Визнач мову і перекладай на українську. Якщо вже українська — на англійську. ТІЛЬКИ переклад."
    if mode == "summarize":
        return base + "\n\nЗАРАЗ: зроби підсумок.\n🎯 Головна думка\n📌 Ключові факти\n⚡ Важливі деталі"
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
        u = await get_user(uid)
        if u.get("blocked"):
            if isinstance(event, Message):
                await event.answer("🚫 Доступ заблоковано.")
            return
        now = _time.monotonic()
        if uid in self._cache and now - self._cache[uid] < 1.5:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Повільніше!")
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
        try: await msg.edit_text(FRAMES[i % len(FRAMES)])
        except: break
        i += 1
        await asyncio.sleep(0.6)

# ════════════════════════════════════════
# ПОШУК
# ════════════════════════════════════════
async def web_search(query: str) -> str:
    encoded = aiohttp.helpers.quote(query)
    for engine in [
        f"https://searx.be/search?q={encoded}&format=json",
        f"https://search.bus-hit.me/search?q={encoded}&format=json",
    ]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(engine, timeout=aiohttp.ClientTimeout(total=5),
                                  headers={"User-Agent": "Mozilla/5.0"}) as r:
                    if r.status != 200: continue
                    d = await r.json(content_type=None)
                    results = d.get("results", [])[:3]
                    lines = [f"• {res.get('title','')}: {res.get('content','')[:150]}"
                             for res in results if res.get("content")]
                    if lines: return "\n".join(lines)
        except Exception: continue
    return ""

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
        return f"⚠️ Помилка: {e}"

# ════════════════════════════════════════
# GEMINI CHAT
# ════════════════════════════════════════
SEARCH_TRIGGERS = ["що таке", "хто такий", "коли", "де ", "новини",
                   "сьогодні", "зараз", "ціна", "курс", "погода",
                   "результат", "вийшов", "виграв"]

async def ask_gemini(uid: int, text: str, image_data: bytes = None) -> str:
    if is_suspicious(text):
        return "🚫 Підозрілий запит заблоковано."

    u = await get_user(uid)
    mode = u["mode"]

    # Пошук якщо треба
    search_ctx = ""
    if mode == "chat" and any(t in text.lower() for t in SEARCH_TRIGGERS):
        search_ctx = await web_search(text)

    # Будуємо контент
    parts = []
    if image_data:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_data).decode()
            }
        })

    user_text = text
    if search_ctx:
        user_text = f"{text}\n\n[Свіжа інфо з інтернету]:\n{search_ctx}"
    parts.append({"text": user_text})

    # Будуємо історію для Gemini
    history = u["history"][-MAX_HISTORY:]
    gemini_history = []
    for msg in history:
        gemini_history.append({
            "role": msg["role"],
            "parts": [{"text": msg["content"]}]
        })
    gemini_history.append({"role": "user", "parts": parts})

    body = {
        "system_instruction": {"parts": [{"text": build_system(u, mode)}]},
        "contents": gemini_history,
        "generationConfig": {
            "temperature": 0.1 if mode == "translate" else 0.5,
            "maxOutputTokens": 2000,
            "topP": 0.9,
        }
    }

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GEMINI_CHAT, json=body,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                d = await r.json()
                if r.status != 200:
                    err = d.get("error", {}).get("message", str(d))[:200]
                    logger.error(f"Gemini error: {err}")
                    return f"⚠️ Помилка Gemini: {err}"

                reply = d["candidates"][0]["content"]["parts"][0]["text"]
                reply = clean_text(reply)

                # Зберігаємо в історію
                history.append({"role": "user", "content": text})
                history.append({"role": "model", "content": reply})
                u["history"] = history[-MAX_HISTORY:]
                u["stats"]["total"] += 1
                await save_user(uid)

                prefix = "🔍 " if search_ctx else ""
                return prefix + reply
    except Exception as e:
        logger.error(f"Gemini request error: {e}")
        return f"⚠️ Помилка: {e}"

# ════════════════════════════════════════
# ГЕНЕРАЦІЯ ЗОБРАЖЕНЬ — Imagen 3
# ════════════════════════════════════════
async def generate_image(prompt: str) -> bytes | None:
    # Спершу перекладаємо промпт на англійську
    translate_body = {
        "system_instruction": {"parts": [{"text": "Translate to English for image generation. Detailed and descriptive. Return ONLY the English prompt."}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 200}
    }
    eng_prompt = prompt
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GEMINI_CHAT, json=translate_body) as r:
                if r.status == 200:
                    d = await r.json()
                    eng_prompt = d["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception: pass

    # Генеруємо через Imagen 3
    body = {
        "instances": [{"prompt": eng_prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "1:1"}
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GEMINI_IMGGEN, json=body,
                              timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status == 200:
                    d = await r.json()
                    img_b64 = d["predictions"][0]["bytesBase64Encoded"]
                    return base64.b64decode(img_b64)
                else:
                    err = await r.json()
                    logger.error(f"Imagen error: {err}")
    except Exception as e:
        logger.error(f"Image gen error: {e}")

    # Fallback — Pollinations
    try:
        encoded = aiohttp.helpers.quote(eng_prompt[:400])
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return await r.read()
    except Exception as e:
        logger.error(f"Pollinations fallback error: {e}")
    return None

# ════════════════════════════════════════
# ПОГОДА
# ════════════════════════════════════════
async def get_weather(city: str) -> str:
    search = await web_search(f"погода {city} сьогодні температура")
    prompt = f"Погода в {city} ({datetime.now().strftime('%B %Y')})"
    if search:
        prompt += f"\n\nДані:\n{search}\n\nСкажи температуру і опис коротко українською."
    else:
        prompt += "\nДай відповідь на основі типового клімату. Коротко українською."

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 200}
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GEMINI_CHAT, json=body) as r:
                if r.status == 200:
                    d = await r.json()
                    reply = clean_text(d["candidates"][0]["content"]["parts"][0]["text"])
                    return f"🌦 *Погода в {city}:*\n\n{reply}"
    except Exception as e:
        logger.error(f"Weather: {e}")
    return "⚠️ Не вдалося отримати погоду."

# ════════════════════════════════════════
# КЛАВІАТУРИ
# ════════════════════════════════════════
def main_kb(uid: int, u: dict):
    mode = u["mode"]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=f"{'🟢' if mode=='chat' else '💬'} Чат", callback_data="mode_chat"),
        InlineKeyboardButton(text=f"{'🟢' if mode=='translate' else '🌍'} Переклад", callback_data="mode_translate"),
        InlineKeyboardButton(text=f"{'🟢' if mode=='summarize' else '📋'} Підсумок", callback_data="mode_summarize"),
    )
    b.row(
        InlineKeyboardButton(text="🌦 Погода", callback_data="weather"),
        InlineKeyboardButton(text="💱 Курс", callback_data="exchange"),
    )
    b.row(
        InlineKeyboardButton(text="🎨 Намалювати", callback_data="draw"),
        InlineKeyboardButton(text="⚙️ Налаштування", callback_data="settings"),
    )
    b.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="🗑 Очистити", callback_data="clear"),
    )
    b.row(InlineKeyboardButton(text="ℹ️ Що вмію", callback_data="help"))
    if is_admin(uid):
        b.row(InlineKeyboardButton(text="👑 Адмін", callback_data="admin"))
    return b.as_markup()

def back_kb():
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    return b.as_markup()

def settings_kb(u: dict):
    b = InlineKeyboardBuilder()
    for key, (name, _) in STYLES.items():
        mark = "✅ " if u["style"] == key else ""
        b.row(InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"style_{key}"))
    b.row(InlineKeyboardButton(text="✏️ Моє ім'я", callback_data="set_name"))
    b.row(InlineKeyboardButton(text="🏙 Моє місто", callback_data="set_city"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    return b.as_markup()

# ════════════════════════════════════════
# БОТ
# ════════════════════════════════════════
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()
sec = SecurityMiddleware()
dp.message.middleware(sec)
dp.callback_query.middleware(sec)

waiting_input: dict[int, str] = {}
waiting_draw:  set[int] = set()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    u = await get_user(uid)
    if not u["name"]:
        u["name"] = message.from_user.first_name or ""
        await save_user(uid)
    name = u["name"] or "друже"
    style_name, _ = STYLES.get(u["style"], STYLES["default"])
    db_type = "Redis 🔴" if redis_client else "JSON 📄"
    await message.answer(
        f"{get_time_greeting()}, *{name}*! ⚡\n\n"
        f"Я — *Живчик v12.0* на Gemini 2.0 🤖\n"
        f"Стиль: *{style_name}* | {db_type}\n\n"
        f"Тепер я розумніший, краще розумію українську\n"
        f"і вмію генерувати зображення 🎨\n\n"
        f"Пиши питання або кидай фото 👇",
        reply_markup=main_kb(uid, u), parse_mode="Markdown"
    )

@dp.callback_query(F.data == "weather")
async def cb_weather(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    u = await get_user(uid)
    city = u.get("city") or "Київ"
    msg = await cb.message.edit_text(f"🌦 Шукаю погоду в {city}...")
    result = await get_weather(city)
    await msg.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "exchange")
async def cb_exchange(cb: CallbackQuery):
    await cb.answer()
    result = await get_exchange()
    await cb.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "draw")
async def cb_draw(cb: CallbackQuery):
    uid = cb.from_user.id
    waiting_draw.add(uid)
    await cb.message.edit_text(
        "🎨 Що намалювати?\n\nОпиши зображення — намалюю!\n\nНаприклад: кіт в космосі, захід сонця в горах",
        reply_markup=back_kb()
    )
    await cb.answer()

@dp.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery):
    uid = cb.from_user.id
    u = await get_user(uid)
    style_name, style_desc = STYLES.get(u["style"], STYLES["default"])
    await cb.message.edit_text(
        f"⚙️ *Налаштування*\n\n"
        f"Стиль: *{style_name}* — {style_desc}\n\n"
        f"Ім'я: *{u['name'] or 'не вказано'}*\n"
        f"Місто: *{u['city'] or 'не вказано'}*",
        reply_markup=settings_kb(u), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("style_"))
async def cb_style(cb: CallbackQuery):
    uid = cb.from_user.id
    style = cb.data.replace("style_", "")
    if style not in STYLES: return await cb.answer("Невідомий стиль")
    u = await get_user(uid)
    u["style"] = style
    await save_user(uid)
    name, desc = STYLES[style]
    await cb.answer(f"✅ {name}")
    await cb.message.edit_text(
        f"✅ *{name}*\n_{desc}_",
        reply_markup=settings_kb(u), parse_mode="Markdown"
    )

@dp.callback_query(F.data == "set_name")
async def cb_set_name(cb: CallbackQuery):
    waiting_input[cb.from_user.id] = "name"
    await cb.message.edit_text("✏️ Напиши своє ім'я:", reply_markup=back_kb())
    await cb.answer()

@dp.callback_query(F.data == "set_city")
async def cb_set_city(cb: CallbackQuery):
    waiting_input[cb.from_user.id] = "city"
    await cb.message.edit_text("🏙 Напиши місто:", reply_markup=back_kb())
    await cb.answer()

@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    u = await get_user(cb.from_user.id)
    s = u["stats"]
    await cb.message.edit_text(
        f"📊 *Статистика:*\n\n"
        f"💬 Запитань: *{s['total']}*\n"
        f"🖼 Фото: *{s['photos']}*\n"
        f"🎨 Зображень: *{s.get('images', 0)}*\n"
        f"🌍 Перекладів: *{s['translates']}*\n"
        f"📋 Підсумків: *{s['summaries']}*\n"
        f"📅 З: *{s['since']}*",
        reply_markup=back_kb(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚡ *Живчик v12.0 на Gemini 2.0:*\n\n"
        "💬 Розумний чат українською\n"
        "🖼 Аналіз фото\n"
        "🎨 Генерація зображень (Imagen 3)\n"
        "🌍 Переклад будь-якої мови\n"
        "📋 Підсумок тексту\n"
        "🌦 Погода для твого міста\n"
        "💱 Курс USD/EUR/GBP\n"
        "⚙️ 5 стилів особистості\n"
        "🧠 Пам'ятає між сесіями\n"
        "🔒 Захист від злому",
        reply_markup=back_kb(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "admin")
async def cb_admin(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("🚫 Немає доступу", show_alert=True)
    keys = await db_keys()
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="👥 Список", callback_data="admin_users"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    await cb.message.edit_text(
        f"👑 *Адмін*\n\n"
        f"👥 Користувачів: *{len(keys)}*\n"
        f"🗄 {'Redis' if redis_client else 'JSON'}\n"
        f"🤖 Gemini 2.0 Flash\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        reply_markup=b.as_markup(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "admin_users")
async def cb_admin_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    keys = await db_keys()
    lines = ["👥 *Користувачі:*\n"]
    for key in keys[:20]:
        uid_str = key.replace("user:", "")
        try: uid = int(uid_str)
        except: continue
        u = await get_user(uid)
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
    u = await get_user(uid)
    style_name, _ = STYLES.get(u["style"], STYLES["default"])
    await cb.message.edit_text(
        f"⚡ *Живчик* | {get_time_greeting()} | *{style_name}*\n\nВибери режим 👇",
        reply_markup=main_kb(uid, u), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("mode_"))
async def cb_mode(cb: CallbackQuery):
    uid = cb.from_user.id
    mode = cb.data.replace("mode_", "")
    u = await get_user(uid)
    u["mode"] = mode
    await save_user(uid)
    names = {"chat": "💬 Чат", "translate": "🌍 Переклад", "summarize": "📋 Підсумок"}
    hints = {"chat": "Пиши що хочеш 🔥", "translate": "Кидай текст 🌍", "summarize": "Надішли текст 📋"}
    await cb.message.edit_text(
        f"🟢 *{names[mode]}*\n\n{hints[mode]}",
        reply_markup=main_kb(uid, u), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "clear")
async def cb_clear(cb: CallbackQuery):
    uid = cb.from_user.id
    u = await get_user(uid)
    u["history"] = []
    await save_user(uid)
    await cb.answer("✅ Пам'ять очищена!")
    await cb.message.edit_text(
        "🗑 *Пам'ять очищена* ✨",
        reply_markup=main_kb(uid, u), parse_mode="Markdown"
    )

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
        caption = message.caption or "Детально опиши що бачиш на фото. Відповідай українською."
        result = await ask_gemini(uid, caption, data.read())
        u = await get_user(uid)
        u["stats"]["photos"] += 1
        await save_user(uid)
    finally:
        stop.set(); anim.cancel()
    await thinking.edit_text(f"🖼 *Аналіз:*\n\n{result}", parse_mode="Markdown")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = message.from_user.id
    text = message.text

    # Очікування вводу (ім'я/місто)
    if uid in waiting_input:
        field = waiting_input.pop(uid)
        u = await get_user(uid)
        u[field] = text.strip()
        await save_user(uid)
        labels = {"name": "Ім'я", "city": "Місто"}
        await message.answer(
            f"✅ *{labels.get(field, field)}* збережено: *{text.strip()}*",
            reply_markup=main_kb(uid, u), parse_mode="Markdown"
        )
        return

    # Генерація зображення
    if uid in waiting_draw:
        waiting_draw.discard(uid)
        u = await get_user(uid)
        thinking = await message.answer("🎨 Малюю... це може зайняти 15-30 секунд")
        stop = asyncio.Event()
        anim = asyncio.create_task(animate(thinking, stop))
        try:
            image_bytes = await generate_image(text)
        finally:
            stop.set(); anim.cancel()
        await thinking.delete()
        if image_bytes:
            u["stats"]["images"] = u["stats"].get("images", 0) + 1
            await save_user(uid)
            await message.answer_photo(
                BufferedInputFile(image_bytes, filename="image.jpg"),
                caption="🎨 " + text,
                reply_markup=main_kb(uid, u)
            )
        else:
            await message.answer("⚠️ Не вдалося згенерувати. Imagen 3 може бути недоступний — спробуй ще раз.", reply_markup=main_kb(uid, u))
        return

    # Звичайний чат
    u = await get_user(uid)
    mode = u["mode"]
    icons = {"chat": "💬", "translate": "🌍", "summarize": "📋"}
    thinking = await message.answer(f"{icons.get(mode, '⚡')} Обробляю...")
    stop = asyncio.Event()
    anim = asyncio.create_task(animate(thinking, stop))
    try:
        reply = await ask_gemini(uid, text)
        if mode == "translate": u["stats"]["translates"] += 1
        elif mode == "summarize": u["stats"]["summaries"] += 1
        await save_user(uid)
    finally:
        stop.set(); anim.cancel()
    try:
        await thinking.edit_text(reply)
    except Exception:
        await thinking.delete()
        await message.answer(reply)

async def main():
    await init_redis()
    await bot.set_my_commands([BotCommand(command="start", description="⚡ Запустити Живчика")])
    logger.info("Живчик v12.0 на Gemini 2.0 запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Живчик зупинено.")
