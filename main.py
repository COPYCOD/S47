import os
import asyncio
import logging
import base64
import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ════════════════════════════════════════
# 1. КОНФІГУРАЦІЯ
# ════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("AI-BOT")

load_dotenv()
BOT_TOKEN   = os.getenv("BOT_TOKEN")
ADMIN_ID    = int(os.getenv("ADMIN_ID", 0))
GEMINI_KEY  = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not ADMIN_ID or not GEMINI_KEY:
    logger.error("Перевірте змінні: BOT_TOKEN, ADMIN_ID, GEMINI_API_KEY")
    exit(1)

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

# ════════════════════════════════════════
# 2. СТАН КОРИСТУВАЧА
# ════════════════════════════════════════
user_mode: dict[int, str] = {}
user_history: dict[int, list] = {}
MAX_HISTORY = 10

def get_mode(uid: int) -> str:
    return user_mode.get(uid, "chat")

def clear_history(uid: int):
    user_history[uid] = []

def add_to_history(uid: int, role: str, text: str):
    if uid not in user_history:
        user_history[uid] = []
    user_history[uid].append({"role": role, "parts": [{"text": text}]})
    if len(user_history[uid]) > MAX_HISTORY:
        user_history[uid] = user_history[uid][-MAX_HISTORY:]

# ════════════════════════════════════════
# 3. MIDDLEWARE
# ════════════════════════════════════════
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or user.id != ADMIN_ID:
            return
        return await handler(event, data)

# ════════════════════════════════════════
# 4. GEMINI API
# ════════════════════════════════════════
SYSTEM_PROMPTS = {
    "chat": "Ти корисний AI асистент. Відповідай українською мовою якщо не просять інакше. Будь лаконічним але повним.",
    "translate": "Ти перекладач. Визнач мову тексту автоматично і перекладай на українську. Якщо текст вже українською — переклади на англійську. Відповідай ТІЛЬКИ перекладом, без пояснень.",
    "summarize": "Ти експерт з аналізу тексту. Стисни наданий текст або переписку. Виділи: головну думку, ключові факти. Відповідай українською мовою, структуровано.",
}

async def ask_gemini(uid: int, text: str) -> str:
    mode = get_mode(uid)
    system = SYSTEM_PROMPTS[mode]

    add_to_history(uid, "user", text)

    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": user_history[uid]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GEMINI_URL, json=body) as r:
                data = await r.json()
                if r.status != 200:
                    return f"⚠️ Помилка API: {data.get('error', {}).get('message', str(data))}"
                reply = data["candidates"][0]["content"]["parts"][0]["text"]
                add_to_history(uid, "model", reply)
                return reply
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return f"⚠️ Помилка з'єднання: {str(e)[:100]}"

async def analyze_photo_gemini(image_data: bytes, caption: str = "") -> str:
    prompt = caption if caption else "Детально опиши що зображено на цьому фото. Якщо є текст — прочитай його."
    b64 = base64.b64encode(image_data).decode("utf-8")

    body = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                {"text": prompt}
            ]
        }],
        "system_instruction": {"parts": [{"text": "Відповідай українською мовою."}]}
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GEMINI_URL, json=body) as r:
                data = await r.json()
                if r.status != 200:
                    return f"⚠️ Помилка: {data.get('error', {}).get('message', str(data))}"
                return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"⚠️ Помилка аналізу: {str(e)[:100]}"

# ════════════════════════════════════════
# 5. КЛАВІАТУРА
# ════════════════════════════════════════
def main_kb(uid: int):
    mode = get_mode(uid)
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=f"{'✅' if mode=='chat' else '💬'} Чат", callback_data="mode_chat"),
        InlineKeyboardButton(text=f"{'✅' if mode=='translate' else '🌐'} Переклад", callback_data="mode_translate"),
        InlineKeyboardButton(text=f"{'✅' if mode=='summarize' else '📝'} Підсумок", callback_data="mode_summarize"),
    )
    b.row(InlineKeyboardButton(text="🗑 Очистити пам'ять", callback_data="clear"))
    return b.as_markup()

# ════════════════════════════════════════
# 6. ХЕНДЛЕРИ
# ════════════════════════════════════════
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()
auth = AuthMiddleware()
dp.message.middleware(auth)
dp.callback_query.middleware(auth)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    clear_history(uid)
    await message.answer(
        "🤖 **AI Асистент активовано**\n\n"
        "Просто пиши питання або надсилай фото.\n\n"
        "💬 Чат — розмова з AI\n"
        "🌐 Переклад — перекладає текст\n"
        "📝 Підсумок — стискає довгий текст\n\n"
        "Вибери режим або просто пиши:",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("mode_"))
async def cb_mode(cb: CallbackQuery):
    uid = cb.from_user.id
    mode = cb.data.replace("mode_", "")
    user_mode[uid] = mode
    hints = {
        "chat": "Просто пиши своє питання.",
        "translate": "Надішли текст — перекладу.",
        "summarize": "Надішли довгий текст — зроблю підсумок."
    }
    names = {"chat": "💬 Чат", "translate": "🌐 Переклад", "summarize": "📝 Підсумок"}
    await cb.message.edit_text(
        f"✅ Режим: **{names[mode]}**\n\n{hints[mode]}",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "clear")
async def cb_clear(cb: CallbackQuery):
    clear_history(cb.from_user.id)
    await cb.answer("✅ Пам'ять очищена")
    await cb.message.edit_text(
        "🗑 Пам'ять очищена. Починаємо з нуля.",
        reply_markup=main_kb(cb.from_user.id),
        parse_mode="Markdown"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    thinking = await message.answer("🔍 Аналізую фото...")
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        image_data = file_bytes.read()
        result = await analyze_photo_gemini(image_data, message.caption or "")
        await thinking.edit_text(f"🖼 **Аналіз фото:**\n\n{result}", parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Помилка: {e}")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = message.from_user.id
    icons = {"chat": "💬", "translate": "🌐", "summarize": "📝"}
    thinking = await message.answer(f"{icons[get_mode(uid)]} Обробляю...")
    reply = await ask_gemini(uid, message.text)
    try:
        await thinking.edit_text(reply)
    except Exception:
        await thinking.delete()
        await message.answer(reply)

# ════════════════════════════════════════
# 7. MAIN
# ════════════════════════════════════════
async def main():
    logger.info("AI Бот на Gemini запущено.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот зупинено.")
