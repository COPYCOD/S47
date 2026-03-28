import os
import asyncio
import logging
import base64
import aiohttp
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

if not BOT_TOKEN or not ADMIN_ID or not GROQ_KEY:
    logger.error("Перевірте змінні: BOT_TOKEN, ADMIN_ID, GROQ_API_KEY")
    exit(1)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_HEADERS = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
MODEL = "llama-3.3-70b-versatile"

user_mode: dict[int, str] = {}
user_history: dict[int, list] = {}
MAX_HISTORY = 10

def get_mode(uid): return user_mode.get(uid, "chat")
def clear_history(uid): user_history[uid] = []

def add_to_history(uid, role, text):
    if uid not in user_history: user_history[uid] = []
    user_history[uid].append({"role": role, "content": text})
    if len(user_history[uid]) > MAX_HISTORY:
        user_history[uid] = user_history[uid][-MAX_HISTORY:]

SYSTEM_PROMPTS = {
    "chat": "Ти Живчик — дружній AI асистент. Спілкуйся неформально, по-дружньому. Відповідай українською мовою. Будь енергійним і позитивним.",
    "translate": "Ти перекладач. Визнач мову і перекладай на українську. Якщо вже українська — на англійську. Відповідай ТІЛЬКИ перекладом.",
    "summarize": "Стисни текст або переписку. Виділи головну думку і ключові факти. Відповідай українською коротко і чітко.",
}

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or user.id != ADMIN_ID: return
        return await handler(event, data)

async def ask_groq(uid, text):
    mode = get_mode(uid)
    add_to_history(uid, "user", text)
    messages = [{"role": "system", "content": SYSTEM_PROMPTS[mode]}] + user_history[uid]
    body = {"model": MODEL, "messages": messages, "max_tokens": 1024}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                data = await r.json()
                if r.status != 200:
                    return f"⚠️ Помилка: {data.get('error',{}).get('message', str(data))[:150]}"
                reply = data["choices"][0]["message"]["content"]
                add_to_history(uid, "assistant", reply)
                return reply
    except Exception as e:
        return f"⚠️ Помилка: {str(e)[:100]}"

async def analyze_photo(image_data, caption=""):
    prompt = caption if caption else "Детально опиши фото. Якщо є текст — прочитай його. Відповідай українською."
    b64 = base64.b64encode(image_data).decode("utf-8")
    body = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt}
        ]}],
        "max_tokens": 1024
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers=GROQ_HEADERS, json=body) as r:
                data = await r.json()
                if r.status != 200:
                    return f"⚠️ Помилка: {data.get('error',{}).get('message', str(data))[:150]}"
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ Помилка: {str(e)[:100]}"

def main_kb(uid):
    mode = get_mode(uid)
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=f"{'🟢' if mode=='chat' else '💬'} Чат", callback_data="mode_chat"),
        InlineKeyboardButton(text=f"{'🟢' if mode=='translate' else '🌍'} Переклад", callback_data="mode_translate"),
        InlineKeyboardButton(text=f"{'🟢' if mode=='summarize' else '📋'} Підсумок", callback_data="mode_summarize"),
    )
    b.row(InlineKeyboardButton(text="🗑 Очистити пам'ять", callback_data="clear"))
    b.row(InlineKeyboardButton(text="ℹ️ Що я вмію?", callback_data="help"))
    return b.as_markup()

def back_kb():
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    return b.as_markup()

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()
auth = AuthMiddleware()
dp.message.middleware(auth)
dp.callback_query.middleware(auth)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    clear_history(uid)
    name = message.from_user.first_name or "друже"
    await message.answer(
        f"Йоу, {name}! ⚡\n\n"
        f"Я — *Живчик*, твій особистий AI асистент 🤖\n\n"
        f"Кидай будь-що — запитання, фото, текст для перекладу чи довгу переписку — розберемось разом 🔥\n\n"
        f"Вибери режим 👇",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚡ *Що вміє Живчик:*\n\n"
        "💬 *Чат* — просто пиши, відповім на все\n"
        "🌍 *Переклад* — кидай текст, переведу\n"
        "📋 *Підсумок* — довга переписка? Стисну до суті\n"
        "🖼 *Фото* — надішли фото, опишу або прочитаю текст\n\n"
        "🧠 Пам'ятаю контекст розмови\n"
        "🗑 Кнопка очищення — починаємо з нуля",
        reply_markup=back_kb(),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    await cb.message.edit_text(
        "⚡ *Живчик* на зв'язку!\n\nВибери режим 👇",
        reply_markup=main_kb(cb.from_user.id),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("mode_"))
async def cb_mode(cb: CallbackQuery):
    uid = cb.from_user.id
    mode = cb.data.replace("mode_", "")
    user_mode[uid] = mode
    names = {"chat": "💬 Чат", "translate": "🌍 Переклад", "summarize": "📋 Підсумок"}
    hints = {
        "chat": "Погнали! Пиши що хочеш 🔥",
        "translate": "Кидай текст — переведу миттєво 🌍",
        "summarize": "Давай переписку або текст — зроблю коротко 📋"
    }
    await cb.message.edit_text(
        f"🟢 Режим: *{names[mode]}*\n\n{hints[mode]}",
        reply_markup=main_kb(uid),
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "clear")
async def cb_clear(cb: CallbackQuery):
    clear_history(cb.from_user.id)
    await cb.answer("✅ Пам'ять очищена!")
    await cb.message.edit_text(
        "🗑 *Пам'ять очищена*\n\nПочинаємо з чистого аркуша ✨",
        reply_markup=main_kb(cb.from_user.id),
        parse_mode="Markdown"
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    thinking = await message.answer("🔍 Дивлюсь на фото...")
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        data = await bot.download_file(file.file_path)
        result = await analyze_photo(data.read(), message.caption or "")
        await thinking.edit_text(f"🖼 *Ось що бачу:*\n\n{result}", parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Щось пішло не так: {e}")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = message.from_user.id
    icons = {"chat": "💬", "translate": "🌍", "summarize": "📋"}
    thinking = await message.answer(f"{icons[get_mode(uid)]} Обробляю...")
    reply = await ask_groq(uid, message.text)
    try:
        await thinking.edit_text(reply)
    except Exception:
        await thinking.delete()
        await message.answer(reply)

async def main():
    # Встановлюємо команди меню
    await bot.set_my_commands([
        BotCommand(command="start", description="⚡ Запустити Живчика"),
    ])
    logger.info("Живчик запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Живчик зупинено.")
    
