import asyncio
import re
import os
import asyncpg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_IDS = set(
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# --- EMOJI ---
OK = "<tg-emoji emoji-id='5870633910337015697'>✅</tg-emoji>"
ERR = "<tg-emoji emoji-id='5870657884844462243'>❌</tg-emoji>"
WARN = "<tg-emoji emoji-id='5870931487146119264'>❗️</tg-emoji>"

# --- REGEX ---
USER_RE = re.compile(r"@\w+")
REP_RE = re.compile(r"([+-]\s*(rep|реп))", re.IGNORECASE)

# --- DB ---
pool: asyncpg.Pool = None


async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id SERIAL PRIMARY KEY,
            from_user_id BIGINT,
            to_username TEXT,
            text TEXT,
            rep INT,
            message_id BIGINT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            rep_score INT DEFAULT 0
        );
        """)

# --- PARSE ---
def extract_user(text: str):
    m = USER_RE.search(text)
    return m.group(0) if m else None

def extract_rep(text: str):
    t = text.lower()
    if "+rep" in t or "+ реп" in t:
        return 1
    if "-rep" in t or "- реп" in t:
        return -1
    return 0

# --- CHECKS ---
def is_full_review(text: str):
    return bool(USER_RE.search(text) and REP_RE.search(text))

def is_probably_review(text: str):
    return bool(USER_RE.search(text) or REP_RE.search(text))

# --- REP UPDATE ---
async def update_rep(username: str, value: int):
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users(username, rep_score)
        VALUES($1, $2)
        ON CONFLICT(username)
        DO UPDATE SET rep_score = users.rep_score + $3
        """, username, value, value)

# --- HANDLER ---
@dp.message(F.photo)
async def handle_review(message: Message):
    text = message.caption or ""

    # мусор → молчим
    if not is_probably_review(text):
        return

    # полный отзыв
    if is_full_review(text):
        user = extract_user(text)
        rep = extract_rep(text)

        if not user or rep == 0:
            return

        async with pool.acquire() as conn:
            await conn.execute("""
            INSERT INTO reviews (from_user_id, to_username, text, rep, message_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            message.from_user.id,
            user,
            text,
            rep,
            message.message_id
            )

        await update_rep(user, rep)

        await message.answer(f"{OK} Отзыв принят")
        return

    # почти отзыв → объяснения
    if USER_RE.search(text) and not REP_RE.search(text):
        await message.reply(f"{ERR} Добавьте +rep или -rep")
        return

    if REP_RE.search(text) and not USER_RE.search(text):
        await message.reply(f"{ERR} Укажите @username")
        return

    if not message.photo:
        await message.reply(f"{WARN} Добавьте фото")
        return


# --- ADMIN DELETE ---
@dp.message(F.text == "/del")
async def delete_review(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.reply_to_message:
        return

    msg_id = message.reply_to_message.message_id

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT to_username, rep FROM reviews WHERE message_id=$1",
            msg_id
        )

        if not row:
            return

        username = row["to_username"]
        rep = row["rep"]

        await conn.execute(
            "DELETE FROM reviews WHERE message_id=$1",
            msg_id
        )

    await update_rep(username, -rep)

    await message.answer("Удалено")


# --- REP ---
@dp.message(F.text.startswith("/rep"))
async def get_rep(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        return

    username = parts[1]

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rep_score FROM users WHERE username=$1",
            username
        )

    if not row:
        await message.answer("0")
        return

    await message.answer(f"{username}: {row['rep_score']}")


# --- START ---
@dp.message(F.text == "/start")
async def start(message: Message):
    await message.answer("Hello")


# --- RUN ---
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
