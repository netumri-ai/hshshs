import asyncio
import re
import sqlite3
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

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

# --- REGEX ---
USER_RE = re.compile(r"@\w+")
REP_RE = re.compile(r"([+-]\s*(rep|реп))", re.IGNORECASE)

# --- EMOJI ---
OK = "✅"
ERR = "❌"
WARN = "❗️"

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

# --- LOGIC CHECKS ---
def is_full_review(text: str, has_photo: bool):
    return has_photo and USER_RE.search(text) and REP_RE.search(text)

def is_probably_review(text: str, has_photo: bool):
    score = 0
    if has_photo:
        score += 1
    if USER_RE.search(text):
        score += 1
    if REP_RE.search(text):
        score += 1
    return score >= 2

def get_error(text: str, has_photo: bool):
    if not has_photo:
        return f"{WARN} Добавьте фото"

    if not USER_RE.search(text):
        return f"{ERR} Укажите @username"

    if not REP_RE.search(text):
        return f"{ERR} Добавьте +rep или -rep"

    return None

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

    has_photo = True

    # 1. МУСОР → МОЛЧИМ
    if not is_probably_review(text, has_photo):
        return

    # 2. ПОЛНЫЙ ОТЗЫВ → ЗАСЧИТЫВАЕМ
    if is_full_review(text, has_photo):
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

    # 3. ПОЧТИ ОТЗЫВ → ОБЪЯСНЯЕМ ОШИБКУ
    err = get_error(text, has_photo)
    if err:
        await message.reply(err)
        return

# --- DELETE REVIEW ---
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
