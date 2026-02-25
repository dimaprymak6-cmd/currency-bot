import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import os

BOT_TOKEN = os.getenv("8586861556:AAEYOaKID0k_Bv-mlZig5Yp3kMEbS0eVEZQ")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

def get_currency():
    url = "https://www.deghest.md/curscentru"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=10)

    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.find_all("tr")

    result = "💱 Курс валют (Deghest):\n\n"

    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 3:
            currency = cols[0].text.strip()
            buy = cols[1].text.strip()
            sell = cols[2].text.strip()

            if currency and buy and sell:
                result += f"💵 {currency}\nПокупка: {buy}\nПродажа: {sell}\n\n"

    if len(result) < 20:
        return "Не удалось получить курс валют 😔"

    return result


async def send_currency(chat_id):
    text = get_currency()
    await bot.send_message(
        chat_id,
        f"{text}\n📅 {datetime.now().strftime('%d.%m.%Y')}"
    )


def schedule_user(chat_id, hour, minute):
    scheduler.add_job(
        send_currency,
        "cron",
        hour=hour,
        minute=minute,
        args=[chat_id],
        id=str(chat_id),
        replace_existing=True
    )


@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "🤖 Бот курса валют запущен!\n\n"
        "Команды:\n"
        "/time 09:30 — установить время\n"
        "/now — показать курс сейчас"
    )


@dp.message(Command("now"))
async def now_handler(message: Message):
    await send_currency(message.chat.id)


@dp.message(Command("time"))
async def time_handler(message: Message):
    try:
        time_text = message.text.split()[1]
        hour, minute = map(int, time_text.split(":"))

        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError

        schedule_user(message.chat.id, hour, minute)

        await message.answer(f"✅ Время установлено: {hour:02d}:{minute:02d}")

    except:
        await message.answer("❌ Используй формат: /time 09:30")


async def main():
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
