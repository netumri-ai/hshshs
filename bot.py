import asyncio
import re
import os
import sqlite3
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

# --- ENV ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- DB ---
conn = sqlite3.connect("bot.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER,
    to_username TEXT,
    text TEXT,
    rep INTEGER,
    message_id INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    rep_score INTEGER DEFAULT 0
)
""")

conn.commit()

# --- PARSER ---
USER_RE = re.compile(r"@\w+")
REP_RE = re.compile(r"([+-]\s*(rep|реп))", re.IGNORECASE)

BLACKLIST = ["продам", "подпишу", "куплю", "ищу", "услуги", "в наличии"]

def is_review(text: str, has_photo: bool):
    if not has_photo:
        return False

    t = text.lower()

    if not USER_RE.search(t):
        return False

    if not REP_RE.search(t):
        return False

    if any(w in t for w in BLACKLIST):
        return False

    return True

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

# --- REP SYSTEM ---
def update_rep(username: str, value: int):
    cur.execute("""
        INSERT INTO users(username, rep_score)
        VALUES(?, ?)
        ON CONFLICT(username)
        DO UPDATE SET rep_score = rep_score + ?
    """, (username, value, value))
    conn.commit()

# --- HANDLER: REVIEW ---
@dp.message(F.photo)
async def handle_review(message: Message):
    text = message.caption or ""

    if not is_review(text, True):
        return

    user = extract_user(text)
    rep = extract_rep(text)

    if not user or rep == 0:
        return

    cur.execute("""
        INSERT INTO reviews (from_user_id, to_username, text, rep, message_id)
        VALUES (?, ?, ?, ?, ?)
    """, (
        message.from_user.id,
        user,
        text,
        rep,
        message.message_id
    ))

    conn.commit()

    update_rep(user, rep)

    await message.answer("Отзыв принят ✅")

# --- DELETE REVIEW (reply /del) ---
@dp.message(F.text == "/del")
async def delete_review(message: Message):
    if not message.reply_to_message:
        await message.answer("Ответь на отзыв ❗")
        return

    msg_id = message.reply_to_message.message_id

    cur.execute("SELECT to_username, rep FROM reviews WHERE message_id = ?", (msg_id,))
    row = cur.fetchone()

    if not row:
        await message.answer("Не найдено ❗")
        return

    username, rep = row

    update_rep(username, -rep)

    cur.execute("DELETE FROM reviews WHERE message_id = ?", (msg_id,))
    conn.commit()

    await message.answer("Удалено ❌")

# --- /rep ---
@dp.message(F.text.startswith("/rep"))
async def get_rep(message: Message):
    parts = message.text.split()

    if len(parts) < 2:
        await message.answer("Укажи @user")
        return

    username = parts[1]

    cur.execute("SELECT rep_score FROM users WHERE username = ?", (username,))
    row = cur.fetchone()

    if not row:
        await message.answer("Нет данных")
        return

    await message.answer(f"{username} репутация: {row[0]}")

# --- START ---
@dp.message(F.text == "/start")
async def start(message: Message):
    await message.answer("Hello")

# --- RUN ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
