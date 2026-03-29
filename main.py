import os
import asyncio
import logging
import base64
import json
import aiohttp
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, BotCommand, BufferedInputFile
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
VISION_MODEL = "llama-3.2-11b-vision-preview"
TTS_URL      = "https://api.groq.com/openai/v1/audio/speech"
DATA_FILE    = "zhyvchyk_data.json"
MAX_HISTORY  = 14

# ════════════════════════════════════════
# ЗБЕРЕЖЕННЯ ДАНИХ (пам'ять між сесіями)
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
            "stats": {"total": 0, "photos": 0, "translates": 0, "summaries": 0, "voice": 0, "since": datetime.now().strftime("%d.%m.%Y")},
            "history": [],
            "notes": [],
            "blocked": False,
        }
        save_data(db)
    return db[key]

def save_user(uid: int):
    save_data(db)

# ════════════════════════════════════════
# БЕЗПЕКА
# ════════════════════════════════════════
BANNED_WORDS = ["<script", "ignore previous", "forget instructions", "system prompt", "jailbreak", "ти тепер", "забудь все", "новий промпт"]

def is_suspicious(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in BANNED_WORDS)

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

# ════════════════════════════════════════
# ОСОБИСТІСТЬ ПО СТИЛЮ
# ════════════════════════════════════════
STYLES = {
    "default":     ("⚡ Живчик",      "Спілкуєшся неформально, енергійно, як друг. Жартуєш іноді."),
    "pro":         ("🎩 Профі",       "Відповідаєш чітко, структуровано, по-діловому. Без зайвих слів."),
    "gentle":      ("🌸 Лагідний",    "Спілкуєшся тепло, з підтримкою, дуже ввічливо."),
    "funny":       ("😂 Жартівник",   "Жартуєш якомога більше, але залишаєшся корисним."),
    "philosopher": ("🧠 Філософ",     "Розмірковуєш глибоко, ставиш питання, шукаєш суть."),
}

def get_time_greeting() -> str:
    h = datetime.now().hour
    if 5 <= h < 12:  return "🌅 Доброго ранку"
    if 12 <= h < 17: return "☀️ Добрий день"
    if 17 <= h < 22: return "🌆 Добрий вечір"
    return "🌙 Добрич ніч"

def get_time_hint() -> str:
    h = datetime.now().hour
    if 5 <= h < 12:  return "Зараз ранок — будь бадьорим."
    if 12 <= h < 17: return "Зараз день — відповідай чітко."
    if 17 <= h < 22: return "Зараз вечір — спілкуйся тепліше."
    return "Зараз ніч — будь спокійним у тоні."

def build_system(uid: int, mode: str) -> str:
    u = get_user(uid)
    style_name, style_desc = STYLES.get(u["style"], STYLES["default"])
    user_name = f"Звати користувача: {u['name']}. " if u.get("name") else ""
    user_city = f"Місто користувача: {u['city']}. " if u.get("city") else ""

    base = (f"Ти — Живчик, AI асистент. Стиль зараз: {style_name}. {style_desc} "
            f"{user_name}{user_city}{get_time_hint()} "
            f"Відповідай українською якщо не просять інакше. Ніколи не відмовляй допомогти.")

    if mode == "translate":
        return "Ти перекладач. Визнач мову і перекладай на українську. Якщо вже українська — на англійську. Відповідай ТІЛЬКИ перекладом."
    if mode == "summarize":
        return base + " Зараз твоя задача: стисни текст. Структура: 🎯 Головна думка, 📌 Ключові факти, ⚡ Деталі."
    return base

# ════════════════════════════════════════
# MIDDLEWARE — rate limit для всіх
# ════════════════════════════════════════
import time as _time

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
                await event.answer("🚫 Тебе заблоковано адміністратором.")
            return

        # Rate limit: 1 запит на 1.5 секунди
        now = _time.monotonic()
        if uid in self._cache and now - self._cache[uid] < 1.5:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Не так швидко!", show_alert=False)
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
# ПОГОДА ЧЕРЕЗ ПОШУК (без API ключа)
# ════════════════════════════════════════
async def get_weather_search(city: str) -> str:
    query = f"weather {city} today temperature"
    url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                d = await r.json(content_type=None)
                abstract = d.get("AbstractText", "")
                if abstract:
                    # Просимо Groq красиво відформатувати
                    prompt = f"На основі цього тексту про погоду в {city} дай коротку відповідь українською з температурою та описом: {abstract[:300]}"
                    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 200}
                    async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as gr:
                        gd = await gr.json()
                        if gr.status == 200:
                            return f"🌦 *Погода в {city}:*\n\n" + gd["choices"][0]["message"]["content"]
                # Fallback — просто питаємо Groq
                body = {"model": MODEL, "messages": [
                    {"role": "system", "content": "Відповідай українською коротко."},
                    {"role": "user", "content": f"Яка зараз приблизна погода в {city}? Дай загальну відповідь на основі кліматичних даних для цього міста та поточного місяця."}
                ], "max_tokens": 200}
                async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as gr:
                    gd = await gr.json()
                    if gr.status == 200:
                        return f"🌦 *Погода в {city}:*\n\n" + gd["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Weather error: {e}")
    return "⚠️ Не вдалося отримати погоду. Спробуй пізніше."

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
                        lines.append(f"*{item['ccy']}*: купівля `{float(item['buy']):.2f}` | продаж `{float(item['sale']):.2f}` грн")
                return "\n".join(lines)
    except Exception as e:
        return f"⚠️ Помилка курсу: {e}"

# ════════════════════════════════════════
# GROQ TEXT
# ════════════════════════════════════════
async def ask_groq(uid: int, text: str) -> str:
    u = get_user(uid)
    mode = u["mode"]

    # Захист від ін'єкцій
    if is_suspicious(text):
        return "🚫 Підозрілий запит заблоковано."

    history = u["history"][-MAX_HISTORY:]
    history.append({"role": "user", "content": text})

    messages = [{"role": "system", "content": build_system(uid, mode)}] + history
    body = {"model": MODEL, "messages": messages, "max_tokens": 1024}

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status != 200:
                    return f"⚠️ {d.get('error',{}).get('message','')[:150]}"
                reply = d["choices"][0]["message"]["content"]
                # Зберігаємо в пам'ять
                history.append({"role": "assistant", "content": reply})
                u["history"] = history[-MAX_HISTORY:]
                u["stats"]["total"] += 1
                save_user(uid)
                return reply
    except Exception as e:
        return f"⚠️ Помилка: {e}"

# ════════════════════════════════════════
# TTS
# ════════════════════════════════════════
async def text_to_speech(text: str) -> bytes | None:
    body = {"model": "playai-tts", "input": text[:500], "voice": "Fritz-PlayAI"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(TTS_URL, headers=GROQ_HEADERS, json=body) as r:
                if r.status == 200:
                    return await r.read()
    except Exception as e:
        logger.error(f"TTS: {e}")
    return None

# ════════════════════════════════════════
# VISION
# ════════════════════════════════════════
async def analyze_photo(image_data: bytes, caption: str = "") -> str:
    prompt = caption or "Детально опиши фото. Якщо є текст — прочитай його. Відповідай українською."
    b64 = base64.b64encode(image_data).decode()
    body = {"model": VISION_MODEL, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": prompt}
    ]}], "max_tokens": 1024}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                d = await r.json()
                if r.status != 200: return f"⚠️ {d.get('error',{}).get('message','')[:100]}"
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
        InlineKeyboardButton(text="💱 Курс", callback_data="exchange"),
    )
    b.row(
        InlineKeyboardButton(text="⚙️ Налаштування", callback_data="settings"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
    )
    b.row(
        InlineKeyboardButton(text="ℹ️ Що вмію", callback_data="help"),
        InlineKeyboardButton(text="🗑 Очистити", callback_data="clear"),
    )
    if is_admin(uid):
        b.row(InlineKeyboardButton(text="👑 Адмін панель", callback_data="admin"))
    return b.as_markup()

def back_kb():
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    return b.as_markup()

def voice_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🎤 Озвучити", callback_data="tts_last"))
    b.row(InlineKeyboardButton(text="⬅️ Меню", callback_data="back_main"))
    return b.as_markup()

def settings_kb(uid: int):
    u = get_user(uid)
    b = InlineKeyboardBuilder()
    for key, (name, _) in STYLES.items():
        mark = "✅ " if u["style"] == key else ""
        b.row(InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"style_{key}"))
    b.row(InlineKeyboardButton(text="✏️ Моє ім'я", callback_data="set_name"))
    b.row(InlineKeyboardButton(text="🏙 Моє місто", callback_data="set_city"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    return b.as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="👥 Користувачі", callback_data="admin_users"))
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

last_reply: dict[int, str] = {}
waiting_input: dict[int, str] = {}  # uid -> "name" | "city" | "weather"

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u["name"]:
        u["name"] = message.from_user.first_name or ""
        save_user(uid)
    greeting = get_time_greeting()
    name = u["name"] or message.from_user.first_name or "друже"
    style_name, _ = STYLES.get(u["style"], STYLES["default"])
    await message.answer(
        f"{greeting}, *{name}*! ⚡\n\n"
        f"Я — *Живчик v4.0* 🤖\n"
        f"Стиль: *{style_name}*\n\n"
        f"🆕 Що нового:\n"
        f"🧠 Пам'ятаю тебе між сесіями\n"
        f"⚙️ Можеш налаштувати мій стиль\n"
        f"🌦 Погода без API ключа\n"
        f"👥 Тепер доступний всім\n"
        f"🔒 Захист від зломів\n\n"
        f"Вибери режим 👇",
        reply_markup=main_kb(uid), parse_mode="Markdown"
    )

@dp.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid)
    style_name, style_desc = STYLES.get(u["style"], STYLES["default"])
    await cb.message.edit_text(
        f"⚙️ *Налаштування*\n\n"
        f"Поточний стиль: *{style_name}*\n"
        f"_{style_desc}_\n\n"
        f"Ім'я: *{u['name'] or 'не вказано'}*\n"
        f"Місто: *{u['city'] or 'не вказано'}*\n\n"
        f"Вибери стиль або зміни дані 👇",
        reply_markup=settings_kb(uid), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("style_"))
async def cb_style(cb: CallbackQuery):
    uid = cb.from_user.id
    style = cb.data.replace("style_", "")
    if style not in STYLES:
        return await cb.answer("Невідомий стиль")
    u = get_user(uid)
    u["style"] = style
    save_user(uid)
    name, desc = STYLES[style]
    await cb.answer(f"✅ Стиль змінено: {name}")
    await cb.message.edit_text(
        f"✅ Стиль: *{name}*\n_{desc}_\n\nВибери ще або повертайся 👇",
        reply_markup=settings_kb(uid), parse_mode="Markdown"
    )

@dp.callback_query(F.data == "set_name")
async def cb_set_name(cb: CallbackQuery):
    waiting_input[cb.from_user.id] = "name"
    await cb.message.edit_text("✏️ Напиши своє ім'я:", reply_markup=back_kb())
    await cb.answer()

@dp.callback_query(F.data == "set_city")
async def cb_set_city(cb: CallbackQuery):
    waiting_input[cb.from_user.id] = "city"
    await cb.message.edit_text("🏙 Напиши своє місто (для погоди):", reply_markup=back_kb())
    await cb.answer()

@dp.callback_query(F.data == "weather")
async def cb_weather(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    u = get_user(uid)
    city = u.get("city") or "Київ"
    thinking = await cb.message.edit_text(f"🌦 Шукаю погоду в {city}...")
    result = await get_weather_search(city)
    await thinking.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "exchange")
async def cb_exchange(cb: CallbackQuery):
    await cb.answer()
    result = await get_exchange()
    await cb.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "tts_last")
async def cb_tts(cb: CallbackQuery):
    uid = cb.from_user.id
    text = last_reply.get(uid, "")
    if not text:
        return await cb.answer("Нема що озвучити", show_alert=True)
    await cb.answer("🎤 Генерую...")
    audio = await text_to_speech(text)
    if audio:
        await cb.message.answer_voice(BufferedInputFile(audio, filename="voice.mp3"))
        get_user(uid)["stats"]["voice"] += 1
        save_user(uid)
    else:
        await cb.message.answer("⚠️ TTS недоступний зараз.")

@dp.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    s = u["stats"]
    await cb.message.edit_text(
        f"📊 *Твоя статистика:*\n\n"
        f"💬 Всього запитань: *{s['total']}*\n"
        f"🖼 Фото: *{s['photos']}*\n"
        f"🌍 Перекладів: *{s['translates']}*\n"
        f"📋 Підсумків: *{s['summaries']}*\n"
        f"🎤 Озвучено: *{s['voice']}*\n\n"
        f"📅 З: *{s['since']}*",
        reply_markup=back_kb(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚡ *Живчик v4.0 — що вмію:*\n\n"
        "💬 Чат з пам'яттю між сесіями\n"
        "🌍 Переклад будь-якої мови\n"
        "📋 Підсумок тексту/переписки\n"
        "🖼 Аналіз фото\n"
        "🎤 Озвучення відповідей\n"
        "🌦 Погода через пошук\n"
        "💱 Курс USD/EUR/GBP\n"
        "⚙️ 5 стилів особистості\n"
        "🔒 Захист від злому\n"
        "👥 Мультикористувач\n"
        "🌙 Стиль по часу доби",
        reply_markup=back_kb(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "admin")
async def cb_admin(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("🚫 Немає доступу", show_alert=True)
    total = len(db)
    await cb.message.edit_text(
        f"👑 *Адмін панель*\n\n"
        f"👥 Всього користувачів: *{total}*\n"
        f"📅 Дата: *{datetime.now().strftime('%d.%m.%Y %H:%M')}*",
        reply_markup=admin_kb(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "admin_users")
async def cb_admin_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    lines = [f"👥 *Список користувачів:*\n"]
    for uid_str, u in db.items():
        name = u.get("name", "Без імені")
        total = u.get("stats", {}).get("total", 0)
        blocked = "🚫" if u.get("blocked") else "✅"
        lines.append(f"{blocked} `{uid_str}` — {name} ({total} запитів)")
    await cb.message.edit_text("\n".join(lines[:20]), reply_markup=admin_kb(), parse_mode="Markdown")
    await cb.answer()

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid)
    style_name, _ = STYLES.get(u["style"], STYLES["default"])
    await cb.message.edit_text(
        f"⚡ *Живчик* на зв'язку! {get_time_greeting()}\n"
        f"Стиль: *{style_name}* | Вибери режим 👇",
        reply_markup=main_kb(uid), parse_mode="Markdown"
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
    hints = {"chat": "Пиши що хочеш 🔥", "translate": "Кидай текст — переведу 🌍", "summarize": "Давай текст — зроблю підсумок 📋"}
    await cb.message.edit_text(
        f"🟢 *{names[mode]}*\n\n{hints[mode]}",
        reply_markup=main_kb(uid), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "clear")
async def cb_clear(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid)
    u["history"] = []
    save_user(uid)
    await cb.answer("✅ Пам'ять очищена!")
    await cb.message.edit_text("🗑 *Пам'ять очищена* ✨", reply_markup=main_kb(uid), parse_mode="Markdown")

@dp.message(F.photo)
async def handle_photo(message: Message):
    uid = message.from_user.id
    thinking = await message.answer("🔍 Дивлюсь...")
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
        last_reply[uid] = result
    finally:
        stop.set(); anim.cancel()
    await thinking.edit_text(f"🖼 *Ось що бачу:*\n\n{result}", parse_mode="Markdown", reply_markup=voice_kb())

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = message.from_user.id
    text = message.text

    # Обробка очікуваного вводу (ім'я/місто)
    if uid in waiting_input:
        field = waiting_input.pop(uid)
        u = get_user(uid)
        u[field] = text.strip()
        save_user(uid)
        labels = {"name": "Ім'я", "city": "Місто"}
        await message.answer(f"✅ *{labels[field]}* збережено: *{text.strip()}*", reply_markup=main_kb(uid), parse_mode="Markdown")
        return

    u = get_user(uid)
    mode = u["mode"]
    icons = {"chat": "💬", "translate": "🌍", "summarize": "📋"}

    thinking = await message.answer(f"{icons.get(mode,'⚡')} Обробляю...")
    stop = asyncio.Event()
    anim = asyncio.create_task(animate(thinking, stop))

    try:
        reply = await ask_groq(uid, text)
        if mode == "translate": u["stats"]["translates"] += 1
        elif mode == "summarize": u["stats"]["summaries"] += 1
        save_user(uid)
        last_reply[uid] = reply
    finally:
        stop.set(); anim.cancel()

    try:
        await thinking.edit_text(reply, reply_markup=voice_kb())
    except Exception:
        await thinking.delete()
        await message.answer(reply, reply_markup=voice_kb())

async def main():
    await bot.set_my_commands([BotCommand(command="start", description="⚡ Запустити Живчика")])
    logger.info("Живчик v4.0 запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Живчик зупинено.")
