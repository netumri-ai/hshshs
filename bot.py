import asyncio
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
import asyncpg

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = set(map(int, os.getenv("ADMIN_IDS", "").split(",")))

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

pool: asyncpg.Pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                from_user_id BIGINT NOT NULL,
                to_username TEXT NOT NULL,
                text TEXT,
                rep INTEGER NOT NULL,
                message_id BIGINT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                rep_score INTEGER DEFAULT 0
            )
        """)

OK = "<tg-emoji emoji-id='5870633910337015697'>✅</tg-emoji>"
ERR = "<tg-emoji emoji-id='5870657884844462243'>❌</tg-emoji>"
WARN = "<tg-emoji emoji-id='5870931487146119264'>❗️</tg-emoji>"

USER_RE = re.compile(r"@(\w+)")
REP_RE = re.compile(r"([+-]\s*(rep|реп))", re.IGNORECASE)

def extract_user(text: str) -> str | None:
    m = USER_RE.search(text)
    return m.group(1).lower() if m else None

def extract_rep(text: str) -> int:
    t = text.lower()
    if "+rep" in t or "+ реп" in t:
        return 1
    if "-rep" in t or "- реп" in t:
        return -1
    return 0

def is_forward(message: Message) -> bool:
    return message.forward_from is not None or message.forward_sender_name is not None

def is_self_review(message: Message, text: str) -> bool:
    user = extract_user(text)
    if not user:
        return False
    if message.from_user.username:
        return user == message.from_user.username.lower()
    return False

def is_probably_review(text: str) -> bool:
    return bool(USER_RE.search(text) or REP_RE.search(text))

def is_full_review(text: str) -> bool:
    return bool(USER_RE.search(text) and REP_RE.search(text))

async def update_rep(username: str, value: int):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users(username, rep_score)
            VALUES($1, $2)
            ON CONFLICT(username)
            DO UPDATE SET rep_score = users.rep_score + $2
        """, username, value)

@dp.message(F.photo)
async def handle_photo_review(message: Message):
    text = message.caption or ""

    if is_forward(message):
        return

    if is_self_review(message, text):
        return

    if not is_probably_review(text):
        return

    if is_full_review(text):
        await process_full_review(message, text)
        return

    await explain_missing(message, text)

@dp.message(F.text)
async def handle_text_review(message: Message):
    text = message.text or ""

    if is_forward(message):
        return

    if is_self_review(message, text):
        return

    if is_probably_review(text):
        if is_full_review(text):
            await message.reply(f"{WARN} Добавьте фото к отзыву")
        else:
            await explain_missing(message, text)

async def process_full_review(message: Message, text: str):
    user = extract_user(text)
    rep = extract_rep(text)

    if not user or rep == 0:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO reviews (from_user_id, to_username, text, rep, message_id) VALUES ($1, $2, $3, $4, $5)",
            message.from_user.id, user, text, rep, message.message_id
        )
        await update_rep(user, rep)
        await message.answer(f"{OK} Отзыв принят")

async def explain_missing(message: Message, text: str):
    has_user = USER_RE.search(text) is not None
    has_rep = REP_RE.search(text) is not None

    if has_user and not has_rep:
        await message.reply(f"{ERR} Укажите репутацию (+rep или -rep)")
    elif has_rep and not has_user:
        await message.reply(f"{ERR} Укажите пользователя (@username)")
    else:
        await message.reply(f"{WARN} Для отзыва нужно фото, @username и +/-rep")

@dp.message(F.text == "/del")
async def delete_review(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply(f"{ERR} Недостаточно прав")
        return

    if not message.reply_to_message:
        await message.reply(f"{ERR} Ответьте на сообщение с отзывом")
        return

    msg_id = message.reply_to_message.message_id

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT to_username, rep FROM reviews WHERE message_id = $1", msg_id
        )

        if not row:
            await message.reply(f"{ERR} Отзыв не найден")
            return

        username, rep = row["to_username"], row["rep"]

        await update_rep(username, -rep)
        await conn.execute("DELETE FROM reviews WHERE message_id = $1", msg_id)

    await message.answer("Удалено")

@dp.message(F.text == "/start")
async def start(message: Message):
    await message.answer(
        "Бот для отзывов с репутацией.\n\n"
        "Отправьте фото с подписью:\n"
        "@username +rep или -rep\n\n"
        "Команды:\n"
        "/del (ответ на отзыв) — удалить (админ)"
    )

async def main():
    await init_db()
    try:
        await dp.start_polling(bot)
    finally:
        await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
