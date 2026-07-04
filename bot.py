import asyncio
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
import asyncpg

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = set()
if admin_ids_raw:
    for aid in admin_ids_raw.split(","):
        aid = aid.strip()
        if aid:
            ADMIN_IDS.add(int(aid))

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
CLOCK = "<tg-emoji emoji-id='5870496192210669260'>⏲</tg-emoji>"
SELF = "<tg-emoji emoji-id='5870450390679425417'>🗒</tg-emoji>"

USER_RE = re.compile(r"@(\w+)")
REP_RE = re.compile(r"(?<!\w)([+-])\s*(rep|реп)\b", re.IGNORECASE)
AD_RE = re.compile(r"(t\.me|telegram\.me|http|www\.|@\w+bot|купить|продам|заработ|слив|прайс)", re.IGNORECASE)
URL_RE = re.compile(r"https?://", re.IGNORECASE)

FORBIDDEN_USERS = {"all", "everyone", "admin", "admins", "group", "channel", "bot"}

def parse_review(text: str) -> tuple[str | None, int]:
    user_match = USER_RE.search(text)
    if not user_match:
        return None, 0

    username = user_match.group(1).lower()

    if username in FORBIDDEN_USERS:
        return None, 0

    rep_match = REP_RE.search(text)
    if not rep_match:
        return None, 0

    sign = rep_match.group(1)
    return username, 1 if sign == "+" else -1

def count_mentions(text: str) -> int:
    return len(USER_RE.findall(text))

def is_forward_hidden(message: Message) -> bool:
    return message.forward_sender_name is not None and message.forward_from is None

def is_forward_open(message: Message) -> bool:
    return message.forward_from is not None

def is_self_review(message: Message, text: str) -> bool:
    user, _ = parse_review(text)
    if not user:
        return False
    if message.from_user.username:
        return user == message.from_user.username.lower()
    return False

def is_ad(text: str) -> bool:
    return bool(AD_RE.search(text)) or bool(URL_RE.search(text))

def is_full_review(text: str) -> bool:
    user, rep = parse_review(text)
    return user is not None and rep != 0

def is_too_many_mentions(text: str) -> bool:
    return count_mentions(text) > 3

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

    if is_forward_hidden(message):
        await message.reply(f"{CLOCK} Отзыв переслан от скрытого профиля. Не засчитано")
        return

    if is_ad(text):
        return

    if not is_forward_open(message):
        if is_self_review(message, text):
            await message.reply(f"{SELF} Нельзя оставлять отзывы самому себе. Не засчитано")
            return

    if is_too_many_mentions(text):
        return

    if not is_full_review(text):
        return

    await process_full_review(message, text)

@dp.message(F.text)
async def handle_text_review(message: Message):
    if message.text.startswith("/"):
        return

    text = message.text

    if is_forward_hidden(message):
        await message.reply(f"{CLOCK} Отзыв переслан от скрытого профиля. Не засчитано")
        return

    if is_ad(text):
        return

    if not is_forward_open(message):
        if is_self_review(message, text):
            await message.reply(f"{SELF} Нельзя оставлять отзывы самому себе. Не засчитано")
            return

    if is_too_many_mentions(text):
        return

    if not is_full_review(text):
        return

    await message.reply(f"{WARN} Добавь фото к отзыву")

async def process_full_review(message: Message, text: str):
    user, rep = parse_review(text)

    if not user or rep == 0:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO reviews (from_user_id, to_username, text, rep, message_id) VALUES ($1, $2, $3, $4, $5)",
            message.from_user.id, user, text, rep, message.message_id
        )
        await update_rep(user, rep)
        await message.answer(f"{OK} Отзыв принят, репутация обновлена")

@dp.message(F.text == "/del")
async def delete_review(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply(f"{ERR} Нет доступа. Твой ID: {message.from_user.id}")
        return

    if not message.reply_to_message:
        await message.reply(f"{ERR} Ответь командой /del на сообщение которое нужно удалить")
        return

    msg_id = message.reply_to_message.message_id

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT to_username, rep FROM reviews WHERE message_id = $1", msg_id
        )

        if not row:
            await message.reply(f"{ERR} Отзыв не найден в базе данных")
            return

        username, rep = row["to_username"], row["rep"]

        await update_rep(username, -rep)
        await conn.execute("DELETE FROM reviews WHERE message_id = $1", msg_id)

    await message.answer(f"{OK} Отзыв удалён, репутация пересчитана")

@dp.message(F.text == "/start", F.chat.type == "private")
async def start(message: Message):
    await message.answer("Hello")

async def main():
    await init_db()
    try:
        await dp.start_polling(bot)
    finally:
        await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
