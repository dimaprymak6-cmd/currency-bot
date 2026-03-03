import json
import logging
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))

# Два URL поиска — по-молдавски и по-русски
SEARCH_URLS = [
    "https://999.md/ru/list/all?q=mere",
    "https://999.md/ru/list/all?q=%D1%8F%D0%B1%D0%BB%D0%BE%D0%BA%D0%B8",
]

KEYWORDS = ["яблок", "яблоко", "mere", "apple", "mar"]
SEEN_IDS_FILE = "/tmp/seen_ids.json"
STATUS_FILE = "/tmp/status.json"

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ro-MD,ro;q=0.9,ru;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def load_seen_ids() -> set:
    if os.path.exists(SEEN_IDS_FILE):
        try:
            with open(SEEN_IDS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_ids(ids: set):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)


def load_status() -> dict:
    default = {"monitoring": False, "last_check": "Ещё не проверялось", "total_found": 0, "check_count": 0}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                return {**default, **json.load(f)}
        except Exception:
            return default
    return default


def save_status(status: dict):
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, ensure_ascii=False)


def parse_listings(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Метод 1: стандартные li карточки
    for item in soup.select("li.ads-list-photo-item"):
        link = item.select_one("a.ads-list-photo-item-title") or item.select_one("a[href*='/ru/view/']")
        if not link:
            continue
        href = link.get("href", "")
        title = link.get_text(strip=True)
        if not title or not any(kw in title.lower() for kw in KEYWORDS):
            continue
        m = re.search(r"/(\d+)(?:\?|$)", href)
        if not m:
            continue
        price_tag = item.select_one(".ads-list-photo-item-price-wrapper") or item.select_one("[class*='price']")
        price = price_tag.get_text(strip=True) if price_tag else "Договорная"
        url = f"https://999.md{href}" if href.startswith("/") else href
        listings.append({"id": m.group(1), "title": title, "price": price, "url": url})

    if listings:
        return listings

    # Метод 2: ищем все ссылки на объявления напрямую
    seen = set()
    for link in soup.select("a[href*='/ru/view/']"):
        href = link.get("href", "")
        if href in seen:
            continue
        seen.add(href)
        title = link.get_text(strip=True)
        if not title:
            p = link.find_parent()
            title = p.get_text(strip=True)[:80] if p else ""
        if not title or not any(kw in title.lower() for kw in KEYWORDS):
            continue
        m = re.search(r"/(\d+)(?:\?|$)", href)
        if not m:
            continue
        p = link.find_parent()
        price = "Договорная"
        if p:
            pt = p.find(class_=re.compile(r"price"))
            if pt:
                price = pt.get_text(strip=True)
        url = f"https://999.md{href}" if href.startswith("/") else href
        listings.append({"id": m.group(1), "title": title, "price": price, "url": url})

    return listings


def fetch_listings():
    all_listings = {}
    network_error = True

    for url in SEARCH_URLS:
        try:
            session = requests.Session()
            session.get("https://999.md/", headers=HEADERS, timeout=10)
            resp = session.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                network_error = False
                logger.info(f"OK: {url} ({len(resp.text)} байт)")
                for l in parse_listings(resp.text):
                    all_listings[l["id"]] = l
            else:
                logger.warning(f"Статус {resp.status_code}: {url}")
        except Exception as e:
            logger.warning(f"Ошибка {url}: {e}")

    if network_error:
        return None
    return list(all_listings.values())


def escape_md(text: str) -> str:
    return re.sub(r"([\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])", r"\\\1", text)


def main_keyboard(mon: bool) -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton("⏸ Остановить мониторинг", callback_data="stop") if mon else InlineKeyboardButton("▶️ Запустить мониторинг", callback_data="start")
    return InlineKeyboardMarkup([
        [btn],
        [InlineKeyboardButton("🔍 Проверить сейчас", callback_data="check_now"), InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("🗑 Сбросить кэш", callback_data="clear_cache"), InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]])


async def send_listing_notification(bot: Bot, listing: dict):
    title, price, url = escape_md(listing["title"]), escape_md(listing["price"]), listing["url"]
    time_str = escape_md(datetime.now().strftime("%d.%m.%Y %H:%M"))
    text = f"🍎 *Новое объявление\\!*\n━━━━━━━━━━━━━━━━━━━━\n\n📌 *{title}*\n\n💰 Цена: {price}\n🕐 {time_str}\n\n[👁 Открыть объявление]({url})"
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👁 Смотреть на 999.md", url=url)]]))
    except Exception as e:
        logger.error(f"Ошибка: {e}")


async def send_check_report(bot: Bot, new_count: int, total: int, error: bool = False):
    now, interval = escape_md(datetime.now().strftime("%H:%M")), escape_md(str(CHECK_INTERVAL // 60))
    if error:
        text = f"⚠️ `{now}` — Не удалось подключиться к 999\\.md\\. Следующая попытка через {interval} мин\\."
    elif new_count > 0:
        text = f"🍎 `{now}` — Найдено новых: *{new_count}* шт\\. Уведомления выше 👆"
    else:
        text = f"🔄 `{now}` — Проверка ок\\. Объявлений с яблоками: {escape_md(str(total))}\\. Новых нет\\. Следующая через {interval} мин\\."
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="MarkdownV2", disable_notification=True)
    except Exception as e:
        logger.error(f"Ошибка: {e}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = load_status()
    mon = status.get("monitoring", False)
    text = f"🍎 *Бот мониторинга яблок на 999\\.md*\n\n📡 Статус: {'🟢 Активен' if mon else '🔴 Остановлен'}\n⏱ Интервал: каждые {CHECK_INTERVAL // 60} мин\\.\n\nНажмите кнопку для управления:"
    await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=main_keyboard(mon))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data, status = query.data, load_status()

    if data == "start":
        if status.get("monitoring"):
            await query.answer("Уже запущен!", show_alert=True); return
        status["monitoring"] = True; save_status(status)
        if not context.job_queue.get_jobs_by_name("monitor"):
            context.job_queue.run_repeating(auto_check_job, interval=CHECK_INTERVAL, first=5, name="monitor")
        await query.edit_message_text(f"✅ *Мониторинг запущен\\!*\n\nПроверяю каждые *{CHECK_INTERVAL // 60} мин*\\.\n📋 Тихое сообщение после каждой проверки\\.\n🔔 Громкое при новом объявлении\\!", parse_mode="MarkdownV2", reply_markup=main_keyboard(True))

    elif data == "stop":
        status["monitoring"] = False; save_status(status)
        for job in context.job_queue.get_jobs_by_name("monitor"): job.schedule_removal()
        await query.edit_message_text("⏸ *Мониторинг остановлен*\n\nНажмите *Запустить мониторинг*, чтобы возобновить\\.", parse_mode="MarkdownV2", reply_markup=main_keyboard(False))

    elif data == "check_now":
        await query.edit_message_text("🔍 *Проверяю объявления\\.\\.\\.*", parse_mode="MarkdownV2")
        seen_ids, listings = load_seen_ids(), fetch_listings()
        if listings is None:
            result = "❌ *Не удалось подключиться к 999\\.md*\n\nПопробуйте позже\\."
        else:
            new = [l for l in listings if l["id"] not in seen_ids]
            if new:
                for l in new:
                    await send_listing_notification(context.bot, l); seen_ids.add(l["id"])
                save_seen_ids(seen_ids); status["total_found"] = status.get("total_found", 0) + len(new)
                result = f"✅ *Найдено новых: {len(new)}*\n\n📤 Уведомления выше\\!"
            else:
                result = f"✅ *Проверка завершена\\!*\n\n📭 Новых нет\\.\nВсего объявлений с яблоками: *{len(listings)}* шт\\."
            status["last_check"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            status["check_count"] = status.get("check_count", 0) + 1; save_status(status)
        await query.edit_message_text(result, parse_mode="MarkdownV2", reply_markup=main_keyboard(status.get("monitoring", False)))

    elif data == "status":
        mon = status.get("monitoring", False)
        text = (f"📊 *Статус мониторинга*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📡 Состояние: {'🟢 Активен' if mon else '🔴 Остановлен'}\n"
                f"⏱ Интервал: каждые {CHECK_INTERVAL // 60} мин\\.\n"
                f"🕐 Последняя проверка: {escape_md(status.get('last_check', 'Ещё не проверялось'))}\n"
                f"🔁 Всего проверок: {status.get('check_count', 0)}\n"
                f"📦 В кэше: {len(load_seen_ids())} объявлений\n"
                f"📬 Новых найдено за всё время: {status.get('total_found', 0)}")
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=back_keyboard())

    elif data == "clear_cache":
        old = len(load_seen_ids()); save_seen_ids(set())
        await query.edit_message_text(f"🗑 *Кэш сброшен\\!*\n\nУдалено: *{old}* записей\\.\nПри следующей проверке все объявления покажутся как новые\\.", parse_mode="MarkdownV2", reply_markup=back_keyboard())

    elif data == "help":
        text = (f"ℹ️ *Справка*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"▶️ *Запустить* — автопроверка каждые {CHECK_INTERVAL // 60} мин\\.\n\n"
                "⏸ *Остановить* — отключить автопроверку\\.\n\n"
                "🔍 *Проверить сейчас* — немедленная проверка\\.\n\n"
                "📊 *Статус* — состояние и статистика\\.\n\n"
                "🗑 *Сбросить кэш* — показать все объявления снова\\.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🔎 Бот ищет по словам: *mere* и *яблоки*\n"
                "⏱ Интервал: Railway → Variables → CHECK\\_INTERVAL")
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=back_keyboard())

    elif data == "back_to_main":
        mon = status.get("monitoring", False)
        text = f"🍎 *Бот мониторинга яблок на 999\\.md*\n\n📡 Статус: {'🟢 Активен' if mon else '🔴 Остановлен'}\n⏱ Интервал: каждые {CHECK_INTERVAL // 60} мин\\.\n\nНажмите кнопку для управления:"
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=main_keyboard(mon))


async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    status = load_status()
    if not status.get("monitoring", False):
        return
    seen_ids, listings = load_seen_ids(), fetch_listings()
    if listings is None:
        await send_check_report(context.bot, 0, 0, error=True); return
    new = [l for l in listings if l["id"] not in seen_ids]
    if new:
        for l in new:
            await send_listing_notification(context.bot, l); seen_ids.add(l["id"])
        save_seen_ids(seen_ids); status["total_found"] = status.get("total_found", 0) + len(new)
    await send_check_report(context.bot, len(new), len(listings))
    status["last_check"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    status["check_count"] = status.get("check_count", 0) + 1; save_status(status)


def main():
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN not set!"); return
    if not CHAT_ID:
        print("ERROR: CHAT_ID not set!"); return
    print(f"Bot started. Interval: {CHECK_INTERVAL}s.")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    status = load_status()
    if status.get("monitoring"):
        async def restore(application):
            application.job_queue.run_repeating(auto_check_job, interval=CHECK_INTERVAL, first=15, name="monitor")
        app.post_init = restore
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
