import os
import asyncio
import logging
import base64
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ════════════════════════════════════════
# 1. КОНФІГУРАЦІЯ
# ════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("AI-BOT")

load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_ID      = int(os.getenv("ADMIN_ID", 0))
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

if not BOT_TOKEN or not ADMIN_ID or not ANTHROPIC_KEY:
    logger.error("Перевірте .env: BOT_TOKEN, ADMIN_ID, ANTHROPIC_API_KEY")
    exit(1)

claude = AsyncAnthropic(api_key=ANTHROPIC_KEY)

# ════════════════════════════════════════
# 2. СТАН КОРИСТУВАЧА (пам'ять чату)
# ════════════════════════════════════════
# Зберігаємо режим і історію розмови
user_mode: dict[int, str] = {}      # chat / translate / summarize
user_history: dict[int, list] = {}  # історія повідомлень для контексту

MAX_HISTORY = 10  # максимум повідомлень в пам'яті

def get_mode(uid: int) -> str:
    return user_mode.get(uid, "chat")

def add_to_history(uid: int, role: str, content):
    if uid not in user_history:
        user_history[uid] = []
    user_history[uid].append({"role": role, "content": content})
    # Обрізаємо якщо занадто довга
    if len(user_history[uid]) > MAX_HISTORY:
        user_history[uid] = user_history[uid][-MAX_HISTORY:]

def clear_history(uid: int):
    user_history[uid] = []

# ════════════════════════════════════════
# 3. MIDDLEWARE
# ════════════════════════════════════════
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        uid = getattr(event, "from_user", None)
        if uid is None or uid.id != ADMIN_ID:
            return
        return await handler(event, data)

# ════════════════════════════════════════
# 4. CLAUDE API ВИКЛИКИ
# ════════════════════════════════════════
SYSTEM_PROMPTS = {
    "chat": (
        "Ти корисний AI асистент. Відповідай українською мовою якщо не просять інакше. "
        "Будь лаконічним але повним у відповідях."
    ),
    "translate": (
        "Ти перекладач. Твоя єдина задача — перекласти наданий текст. "
        "Визнач мову тексту автоматично і перекладай на українську. "
        "Якщо текст вже українською — переклади на англійську. "
        "Відповідай ТІЛЬКИ перекладом, без пояснень."
    ),
    "summarize": (
        "Ти експерт з аналізу тексту. Твоя задача — стиснути наданий текст або переписку. "
        "Виділи: головну думку, ключові факти, важливі деталі. "
        "Відповідай українською мовою, структуровано."
    ),
}

async def ask_claude(uid: int, text: str) -> str:
    mode = get_mode(uid)
    system = SYSTEM_PROMPTS[mode]

    add_to_history(uid, "user", text)

    try:
        response = await claude.messages.create(
            model="claude-haiku-4-5-20251001",  # найшвидша і найдешевша модель
            max_tokens=1024,
            system=system,
            messages=user_history[uid]
        )
        reply = response.content[0].text
        add_to_history(uid, "assistant", reply)
        return reply
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"⚠️ Помилка API: {str(e)[:100]}"

async def analyze_photo(uid: int, image_data: bytes, mime: str, caption: str = "") -> str:
    prompt = caption if caption else "Детально опиши що зображено на цьому фото. Якщо є текст — прочитай його."
    
    try:
        response = await claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system="Ти AI асистент що аналізує зображення. Відповідай українською мовою.",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.standard_b64encode(image_data).decode("utf-8")
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Photo analysis error: {e}")
        return f"⚠️ Помилка аналізу фото: {str(e)[:100]}"

# ════════════════════════════════════════
# 5. КЛАВІАТУРИ
# ════════════════════════════════════════
def main_kb(uid: int):
    mode = get_mode(uid)
    b = InlineKeyboardBuilder()
    
    # Кнопки режимів з позначкою активного
    b.row(
        InlineKeyboardButton(
            text=f"{'✅' if mode == 'chat' else '💬'} Чат",
            callback_data="mode_chat"
        ),
        InlineKeyboardButton(
            text=f"{'✅' if mode == 'translate' else '🌐'} Переклад",
            callback_data="mode_translate"
        ),
        InlineKeyboardButton(
            text=f"{'✅' if mode == 'summarize' else '📝'} Підсумок",
            callback_data="mode_summarize"
        )
    )
    b.row(InlineKeyboardButton(text="🗑 Очистити пам'ять", callback_data="clear_history"))
    return b.as_markup()

# ════════════════════════════════════════
# 6. БОТ + ХЕНДЛЕРИ
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
        "Просто пиши мені будь-яке питання або надсилай фото.\n\n"
        "**Режими:**\n"
        "💬 Чат — звичайна розмова з AI\n"
        "🌐 Переклад — перекладає текст\n"
        "📝 Підсумок — стискає довгий текст\n\n"
        "Вибери режим або просто пиши:",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    uid = message.from_user.id
    mode = get_mode(uid)
    mode_names = {"chat": "💬 Чат", "translate": "🌐 Переклад", "summarize": "📝 Підсумок"}
    await message.answer(
        f"⚙️ **Меню**\nПоточний режим: {mode_names[mode]}",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )

# ── Зміна режиму ─────────────────────────
@dp.callback_query(F.data.startswith("mode_"))
async def cb_mode(cb: CallbackQuery):
    uid = cb.from_user.id
    mode = cb.data.replace("mode_", "")
    user_mode[uid] = mode
    
    mode_names = {"chat": "💬 Чат", "translate": "🌐 Переклад", "summarize": "📝 Підсумок"}
    mode_hints = {
        "chat": "Просто пиши своє питання — я відповім.",
        "translate": "Надішли текст — перекладу на українську (або з української на англійську).",
        "summarize": "Надішли довгий текст або переписку — зроблю короткий підсумок."
    }
    
    await cb.message.edit_text(
        f"✅ Режим змінено: **{mode_names[mode]}**\n\n{mode_hints[mode]}",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )
    await cb.answer()

# ── Очищення пам'яті ─────────────────────
@dp.callback_query(F.data == "clear_history")
async def cb_clear(cb: CallbackQuery):
    clear_history(cb.from_user.id)
    await cb.answer("✅ Пам'ять очищена", show_alert=False)
    await cb.message.edit_text(
        "🗑 **Пам'ять очищена.**\nПочинаємо розмову з нуля.",
        reply_markup=main_kb(cb.from_user.id),
        parse_mode="Markdown"
    )

# ── Фото ─────────────────────────────────
@dp.message(F.photo)
async def handle_photo(message: Message):
    uid = message.from_user.id
    thinking = await message.answer("🔍 Аналізую фото...")
    
    try:
        # Беремо найкраще фото
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        image_data = file_bytes.read()
        
        caption = message.caption or ""
        result = await analyze_photo(uid, image_data, "image/jpeg", caption)
        
        await thinking.delete()
        await message.answer(f"🖼 **Аналіз фото:**\n\n{result}", parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Помилка: {e}")

# ── Текстові повідомлення ─────────────────
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = message.from_user.id
    text = message.text
    
    mode = get_mode(uid)
    mode_icons = {"chat": "💬", "translate": "🌐", "summarize": "📝"}
    
    thinking = await message.answer(f"{mode_icons[mode]} Обробляю...")
    
    reply = await ask_claude(uid, text)
    
    try:
        await thinking.edit_text(reply)
    except Exception:
        await thinking.delete()
        await message.answer(reply)

# ════════════════════════════════════════
# 7. MAIN
# ════════════════════════════════════════
async def main():
    logger.info("AI Бот запущено.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот зупинено.")
        
