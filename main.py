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

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ZHYVCHYK")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID  = int(os.getenv("ADMIN_ID", 0))
GROQ_KEY  = os.getenv("GROQ_API_KEY")
REDIS_URL = os.getenv("REDIS_URL", "")

if not BOT_TOKEN or not GROQ_KEY:
    logger.error("Перевірте: BOT_TOKEN, GROQ_API_KEY")
    exit(1)

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_HEADERS = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
MODEL        = "llama-3.3-70b-versatile"
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DATA_FILE    = "zhyvchyk_data.json"
MAX_HISTORY  = 16  # більше контексту = розумніші відповіді

# Параметри якості відповідей
CHAT_PARAMS      = {"temperature": 0.5, "max_tokens": 1500, "top_p": 0.9, "frequency_penalty": 0.3}
TRANSLATE_PARAMS = {"temperature": 0.1, "max_tokens": 2000, "top_p": 0.95}
SUMMARIZE_PARAMS = {"temperature": 0.3, "max_tokens": 1000, "top_p": 0.9}

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
        "stats": {"total": 0, "photos": 0, "translates": 0, "summaries": 0,
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
# ПОШУК — SearXNG публічний інстанс
# ════════════════════════════════════════
SEARCH_ENGINES = [
    "https://searx.be/search?q={}&format=json",
    "https://search.bus-hit.me/search?q={}&format=json",
]

async def web_search(query: str) -> str:
    encoded = aiohttp.helpers.quote(query)
    for engine in SEARCH_ENGINES:
        try:
            url = engine.format(encoded)
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5),
                                  headers={"User-Agent": "Mozilla/5.0"}) as r:
                    if r.status != 200: continue
                    d = await r.json(content_type=None)
                    results = d.get("results", [])[:3]
                    if not results: continue
                    lines = []
                    for res in results:
                        title = res.get("title", "")
                        content = res.get("content", "")[:200]
                        if content:
                            lines.append(f"• {title}: {content}")
                    if lines:
                        return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Search engine failed: {e}")
            continue
    return ""

# ════════════════════════════════════════
# БЕЗПЕКА
# ════════════════════════════════════════
BANNED_PATTERNS = [
    "ignore previous", "forget instructions", "system prompt",
    "jailbreak", "забудь все", "новий промпт", "ти тепер інший",
    "<script", "drop table", "rm -rf"
]

# НОВЕ: фільтр чужих символів у відповіді
import unicodedata

def clean_response(text: str) -> str:
    cleaned = []
    for char in text:
        cat = unicodedata.category(char)
        name = unicodedata.name(char, "")
        # Дозволяємо: латиниця, кирилиця, пунктуація, цифри, емодзі
        if (cat.startswith("L") and ("CJK" not in name) and
            ("ARABIC" not in name) and ("HEBREW" not in name) and
            ("THAI" not in name) and ("GEORGIAN" not in name.upper())):
            cleaned.append(char)
        elif cat in ("Nd", "Po", "Ps", "Pe", "Pd", "Pc", "Zs", "Cc", "So", "Sm"):
            cleaned.append(char)
        elif ord(char) > 0x1F000:  # емодзі
            cleaned.append(char)
        elif char in "\n\r\t ":
            cleaned.append(char)
    return "".join(cleaned)

def is_suspicious(text: str) -> bool:
    return any(p in text.lower() for p in BANNED_PATTERNS)

# ════════════════════════════════════════
# СТИЛІ
# ════════════════════════════════════════
STYLES = {
    "default":     ("⚡ Живчик",    "дружній, енергійний, іноді підколює але не ображає"),
    "pro":         ("🎩 Профі",     "чіткий, структурований, по-діловому"),
    "gentle":      ("🌸 Лагідний",  "теплий, підтримуючий, дуже ввічливий"),
    "funny":       ("😂 Жартівник", "жартує постійно, але залишається корисним"),
    "philosopher": ("🧠 Філософ",   "глибокий, ставить питання, шукає суть"),
}

CHANGELOG = """Ти — Живчик, AI асистент. Знай свою історію:
v1: S47 stalkerware (3/10) → v2: DevOps монітор (7/10) → v3: Sentinel Supreme (9.5/10) →
v4: Overlord з кнопками (81/100) → v5-6: AI асистент + ім'я Живчик →
v7: погода/курс/TTS → v8: мультиюзер/стилі/адмін → v9: фікс фото → v10: Redis БД →
v11 (поточна): реальний пошук SearXNG, фільтр символів, оновлений характер.
Розповідай про версії коли питають."""

def get_time_greeting():
    h = datetime.now().hour
    if 5 <= h < 12:  return "🌅 Ранок"
    if 12 <= h < 17: return "☀️ День"
    if 17 <= h < 22: return "🌆 Вечір"
    return "🌙 Ніч"

def get_time_hint():
    h = datetime.now().hour
    if 5 <= h < 12:  return "Зараз ранок."
    if 12 <= h < 17: return "Зараз день."
    if 17 <= h < 22: return "Зараз вечір."
    return "Зараз ніч."

def build_system(u: dict, mode: str) -> str:
    _, style_desc = STYLES.get(u["style"], STYLES["default"])
    name_hint = f"Користувача звати {u['name']}. " if u.get("name") else ""
    city_hint = f"Місто: {u['city']}. " if u.get("city") else ""

    personality = f"""Ти — Живчик, розумний AI асистент з характером. Стиль: {style_desc}.
{name_hint}{city_hint}{get_time_hint()}

ТВІЙ ХАРАКТЕР:
- Спілкуєшся як справжній друг — тепло але без слащавості
- Можеш підколоти та пожартувати, але НІКОЛИ не ображаєш
- Якщо питання дурне — скажи це жартома, не зверхньо
- Якщо не знаєш точно — ЧЕСНО кажеш, не вигадуєш факти

РОЗУМНІ ВІДПОВІДІ:
- Перед відповіддю на складне питання — подумай крок за кроком
- Наводь конкретні приклади де доречно
- Структуруй відповідь якщо вона довга
- Якщо є дані з пошуку — використовуй їх як основу, вказуй що це свіжа інфо
- Розрізняй факти від припущень — позначай "мабуть", "швидше за все" де невпевнений

МОВА І СИМВОЛИ:
- Відповідай ТІЛЬКИ українською або тією мовою що питають
- ЗАБОРОНЕНО: китайські, японські, арабські та інші неєвропейські символи
- Використовуй емодзі помірно, тільки де доречно

{CHANGELOG}"""

    if mode == "translate":
        return """Ти професійний перекладач. Правила:
1. Визнач мову тексту автоматично
2. Якщо не українська — перекладай на українську
3. Якщо вже українська — перекладай на англійську
4. Зберігай стиль і тон оригіналу
5. Відповідай ТІЛЬКИ перекладом, без пояснень"""
    if mode == "summarize":
        return personality + """

ЗАРАЗ ТВОЯ ЗАДАЧА — ПІДСУМОК:
Структура відповіді:
🎯 **Головна думка** (1-2 речення)
📌 **Ключові факти** (список)
⚡ **Важливі деталі** (якщо є)
❓ **Відкриті питання** (якщо є)"""
    return personality

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
# ПОГОДА
# ════════════════════════════════════════
async def get_weather(city: str) -> str:
    # Спершу шукаємо реальні дані
    search_result = await web_search(f"погода {city} сьогодні температура")
    
    prompt = f"Яка погода в {city} зараз ({datetime.now().strftime('%B %Y')})?"
    if search_result:
        prompt += f"\n\nДані з інтернету:\n{search_result}\n\nСкажи температуру, опис, що одягти. Коротко українською."
    else:
        prompt += "\nДай відповідь на основі типового клімату. Коротко українською."

    body = {"model": MODEL, "messages": [
        {"role": "system", "content": "Відповідай ТІЛЬКИ українською. Без чужих символів."},
        {"role": "user", "content": prompt}
    ], "max_tokens": 200}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status == 200:
                    reply = clean_response(d["choices"][0]["message"]["content"])
                    return f"🌦 *Погода в {city}:*\n\n{reply}"
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
        return f"⚠️ Помилка: {e}"

# ════════════════════════════════════════
# GROQ — з реальним пошуком
# ════════════════════════════════════════
SEARCH_TRIGGERS = [
    "що таке", "хто такий", "хто така", "коли був", "де знаходиться",
    "новини", "зараз", "сьогодні", "останні", "актуальн",
    "скільки коштує", "ціна", "курс", "погода", "результат",
    "вийшов", "вийшла", "виграв", "програв", "відбувся"
]

async def ask_groq(uid: int, text: str) -> str:
    if is_suspicious(text):
        return "🚫 Підозрілий запит заблоковано."

    u = await get_user(uid)
    mode = u["mode"]

    # Визначаємо чи треба гуглити
    needs_search = any(t in text.lower() for t in SEARCH_TRIGGERS)
    search_ctx = ""
    if needs_search and mode == "chat":
        search_ctx = await web_search(text)
        if search_ctx:
            logger.info(f"Search result for '{text[:30]}': found")

    history = u["history"][-MAX_HISTORY:]
    
    user_content = text
    if search_ctx:
        user_content = f"{text}\n\n[Свіжа інфо з інтернету]:\n{search_ctx}"
    
    history.append({"role": "user", "content": user_content})
    messages = [{"role": "system", "content": build_system(u, mode)}] + history

    params = TRANSLATE_PARAMS if mode == "translate" else (SUMMARIZE_PARAMS if mode == "summarize" else CHAT_PARAMS)
    body = {"model": MODEL, "messages": messages, **params}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status != 200:
                    return f"⚠️ {d.get('error', {}).get('message', '')[:150]}"
                raw = d["choices"][0]["message"]["content"]
                reply = clean_response(raw)  # ФІЛЬТР СИМВОЛІВ
                history.append({"role": "assistant", "content": reply})
                u["history"] = history[-MAX_HISTORY:]
                u["stats"]["total"] += 1
                await save_user(uid)
                prefix = "🔍 " if search_ctx else ""
                return prefix + reply
    except Exception as e:
        return f"⚠️ Помилка: {e}"

# ════════════════════════════════════════
# VISION
# ════════════════════════════════════════
async def analyze_photo(image_data: bytes, caption: str = "") -> str:
    prompt = caption or "Детально опиши фото. Якщо є текст — прочитай його. Відповідай ТІЛЬКИ українською."
    b64 = base64.b64encode(image_data).decode()
    body = {"model": VISION_MODEL, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": prompt}
    ]}], "max_tokens": 1024}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status != 200:
                    return f"⚠️ {d.get('error', {}).get('message', '')[:150]}"
                return clean_response(d["choices"][0]["message"]["content"])
    except Exception as e:
        return f"⚠️ {e}"


# ════════════════════════════════════════
# TTS — Groq голос (англійська)
# ════════════════════════════════════════
TTS_URL = "https://api.groq.com/openai/v1/audio/speech"

async def text_to_speech(text: str) -> bytes | None:
    """Конвертуємо текст в голос через Groq TTS"""
    # Спершу перекладаємо на англійську для TTS
    translate_body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Translate the following text to English. Return ONLY the translation, nothing else."},
            {"role": "user", "content": text[:800]}
        ],
        "temperature": 0.1, "max_tokens": 1000
    }
    english_text = text  # fallback
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=translate_body) as r:
                if r.status == 200:
                    d = await r.json()
                    english_text = d["choices"][0]["message"]["content"]
    except Exception:
        pass

    tts_body = {"model": "playai-tts", "input": english_text[:500], "voice": "Fritz-PlayAI", "response_format": "mp3"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(TTS_URL, headers=GROQ_HEADERS, json=tts_body) as r:
                if r.status == 200:
                    return await r.read()
                logger.warning(f"TTS error: {r.status}")
    except Exception as e:
        logger.error(f"TTS: {e}")
    return None

# ════════════════════════════════════════
# ГЕНЕРАЦІЯ ЗОБРАЖЕНЬ — Pollinations.ai
# ════════════════════════════════════════
async def generate_image(prompt: str) -> str | None:
    """Генеруємо зображення через Pollinations.ai — безкоштовно"""
    # Перекладаємо промпт на англійську для кращого результату
    translate_body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Translate to English for image generation. Make it descriptive and detailed. Return ONLY the English prompt."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3, "max_tokens": 200
    }
    eng_prompt = prompt
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=translate_body) as r:
                if r.status == 200:
                    d = await r.json()
                    eng_prompt = d["choices"][0]["message"]["content"].strip()
    except Exception:
        pass

    encoded = aiohttp.helpers.quote(eng_prompt[:500])
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&enhance=true"
    return url  # Pollinations повертає URL напряму

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
        InlineKeyboardButton(text="⚙️ Налаштування", callback_data="settings"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
    )
    b.row(
        InlineKeyboardButton(text="🎨 Намалювати", callback_data="draw"),
        InlineKeyboardButton(text="🗑 Очистити", callback_data="clear"),
    )
    b.row(
        InlineKeyboardButton(text="ℹ️ Що вмію", callback_data="help"),
    )
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
last_reply: dict[int, str] = {}   # для TTS
waiting_draw: set[int] = set()    # для генерації зображень

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
        f"Я — *Живчик v11.0* 🤖\n"
        f"Стиль: *{style_name}* | {db_type}\n\n"
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
        f"🌍 Перекладів: *{s['translates']}*\n"
        f"📋 Підсумків: *{s['summaries']}*\n"
        f"📅 З: *{s['since']}*",
        reply_markup=back_kb(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚡ *Живчик v11.0:*\n\n"
        "💬 Чат з характером і підколками\n"
        "🔍 Реальний пошук в інтернеті\n"
        "🌍 Переклад будь-якої мови\n"
        "📋 Підсумок тексту\n"
        "🖼 Аналіз фото\n"
        "🌦 Погода для твого міста\n"
        "💱 Курс USD/EUR/GBP\n"
        "⚙️ 5 стилів особистості\n"
        "🧠 Пам'ятає між сесіями (Redis)\n"
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
    thinking = await message.answer("🔍 Аналізую...")
    stop = asyncio.Event()
    anim = asyncio.create_task(animate(thinking, stop))
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        data = await bot.download_file(file.file_path)
        result = await analyze_photo(data.read(), message.caption or "")
        u = await get_user(uid)
        u["stats"]["photos"] += 1
        u["stats"]["total"] += 1
        await save_user(uid)
    finally:
        stop.set(); anim.cancel()
    await thinking.edit_text(f"🖼 *Аналіз:*\n\n{result}", parse_mode="Markdown")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = message.from_user.id
    text = message.text

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

    # Обробка запиту на малювання
    if uid in waiting_draw:
        waiting_draw.discard(uid)
        u = await get_user(uid)
        thinking = await message.answer("🎨 Малюю... (може зайняти 10-20 сек)")
        image_url = await generate_image(text)
        await thinking.delete()
        if image_url:
            caption_text = "🎨 Ось твоє зображення!\n\n" + text
            await message.answer_photo(
                photo=image_url,
                caption=caption_text,
                reply_markup=main_kb(uid, u)
            )
        else:
            await message.answer("⚠️ Не вдалося згенерувати. Спробуй ще раз.", reply_markup=main_kb(uid, u))
        return

    u = await get_user(uid)
    mode = u["mode"]
    icons = {"chat": "💬", "translate": "🌍", "summarize": "📋"}

    thinking = await message.answer(f"{icons.get(mode, '⚡')} Обробляю...")
    stop = asyncio.Event()
    anim = asyncio.create_task(animate(thinking, stop))

    try:
        reply = await ask_groq(uid, text)
        if mode == "translate": u["stats"]["translates"] += 1
        elif mode == "summarize": u["stats"]["summaries"] += 1
        await save_user(uid)
    finally:
        stop.set(); anim.cancel()

    last_reply[uid] = reply  # зберігаємо для TTS
    from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
    voice_kb = IKB()
    voice_kb.row(InlineKeyboardButton(text="🎤 Озвучити", callback_data="tts_last"))
    try:
        await thinking.edit_text(reply, reply_markup=voice_kb.as_markup())
    except Exception:
        await thinking.delete()
        await message.answer(reply, reply_markup=voice_kb.as_markup())


@dp.callback_query(F.data == "tts_last")
async def cb_tts(cb: CallbackQuery):
    uid = cb.from_user.id
    text = last_reply.get(uid, "")
    if not text:
        return await cb.answer("Нема що озвучити", show_alert=True)
    await cb.answer("🎤 Генерую голос...")
    thinking = await cb.message.answer("🎤 Озвучую...")
    audio = await text_to_speech(text)
    await thinking.delete()
    if audio:
        from aiogram.types import BufferedInputFile
        await cb.message.answer_voice(
            BufferedInputFile(audio, filename="voice.mp3"),
            caption="🎤 Голосова відповідь"
        )
    else:
        await cb.message.answer("⚠️ TTS недоступний зараз. Groq може тимчасово не підтримувати.")

@dp.callback_query(F.data == "draw")
async def cb_draw(cb: CallbackQuery):
    uid = cb.from_user.id
    waiting_draw.add(uid)
    await cb.message.edit_text(
        "🎨 Що намалювати?\n\nОпиши зображення українською — я перекладу і намалюю!\n\nНаприклад: кіт в космосі, захід сонця над горами, футуристичне місто",
        reply_markup=back_kb()
    )
    await cb.answer()

async def main():
    await init_redis()
    await bot.set_my_commands([BotCommand(command="start", description="⚡ Запустити Живчика")])
    logger.info("Живчик v11.0 запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Живчик зупинено.")
