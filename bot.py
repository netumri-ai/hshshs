import asyncio
import re
import sqlite3
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 🔥 FIX: включаем HTML (ОБЯЗАТЕЛЬНО для tg-emoji)
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
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

# --- EMOJI ---
OK = "<tg-emoji emoji-id='5870633910337015697'>✅</tg-emoji>"
ERR = "<tg-emoji emoji-id='5870657884844462243'>❌</tg-emoji>"
WARN = "<tg-emoji emoji-id='5870931487146119264'>❗️</tg-emoji>"

# --- REGEX ---
USER_RE = re.compile(r"@\w+")
REP_RE = re.compile(r"([+-]\s*(rep|реп))", re.IGNORECASE)

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
def is_probably_review(text: str):
    return bool(USER_RE.search(text) or REP_RE.search(text))

def is_full_review(text: str):
    return bool(USER_RE.search(text) and REP_RE.search(text))

# --- REP ---
def update_rep(username: str, value: int):
    cur.execute("""
        INSERT INTO users(username, rep_score)
        VALUES(?, ?)
        ON CONFLICT(username)
        DO UPDATE SET rep_score = rep_score + ?
    """, (username, value, value))
    conn.commit()

# --- HANDLER ---
@dp.message(F.photo)
async def handle_review(message: Message):
    text = message.caption or ""

    if not is_probably_review(text):
        return

    if is_full_review(text):
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

        await message.answer(f"{OK} Отзыв принят")
        return

    # почти отзыв → молчим или можно позже расширить
    return

# --- DELETE ---
@dp.message(F.text == "/del")
async def delete_review(message: Message):
    if not message.reply_to_message:
        return

    msg_id = message.reply_to_message.message_id

    cur.execute("SELECT to_username, rep FROM reviews WHERE message_id = ?", (msg_id,))
    row = cur.fetchone()

    if not row:
        return

    username, rep = row

    update_rep(username, -rep)

    cur.execute("DELETE FROM reviews WHERE message_id = ?", (msg_id,))
    conn.commit()

    await message.answer("Удалено")

# --- REP ---
@dp.message(F.text.startswith("/rep"))
async def get_rep(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        return

    username = parts[1]

    cur.execute("SELECT rep_score FROM users WHERE username = ?", (username,))
    row = cur.fetchone()

    if not row:
        await message.answer("0")
        return

    await message.answer(f"{username}: {row[0]}")

# --- START ---
@dp.message(F.text == "/start")
async def start(message: Message):
    await message.answer("Hello")

# --- RUN ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
