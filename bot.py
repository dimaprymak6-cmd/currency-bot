import json
import logging
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# Переменные берутся из Railway -> Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))

# ScraperAPI для обхода блокировки 999.md
# Зарегистрируйтесь бесплатно на scraperapi.com
# и добавьте SCRAPER_API_KEY в Railway -> Variables
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")

KEYWORDS = ["яблок", "яблоко", "mere", "apple", "mar", "mar"]
SEARCH_URL = "https://999.md/ru/list/agriculture/vegetables-and-fruits"
SEEN_IDS_FILE = "/tmp/seen_ids.json"
STATUS_FILE = "/tmp/status.json"

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ro-MD,ro;q=0.9,ru;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
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
    default = {
        "monitoring": False,
        "last_check": "Ещё не проверялось",
        "total_found": 0,
        "check_count": 0,
    }
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


def fetch_listings():
    """
    Загружает страницу 999.md.
    Попытка 1: прямой запрос с реалистичными заголовками.
    Попытка 2: через ScraperAPI (если задан ключ).
    Возвращает список объявлений или None при ошибке.
    """
    html = None

    # --- Попытка 1: прямой запрос ---
    try:
        session = requests.Session()
        session.get("https://999.md/", headers=HEADERS, timeout=10)
        response = session.get(SEARCH_URL, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            html = response.text
            logger.info("Прямой запрос успешен")
        else:
            logger.warning(f"Прямой запрос: статус {response.status_code}")
    except Exception as e:
        logger.warning(f"Прямой запрос не удался: {e}")

    # --- Попытка 2: через ScraperAPI ---
    if html is None and SCRAPER_API_KEY:
        try:
            scraper_url = (
                f"http://api.scraperapi.com"
                f"?api_key={SCRAPER_API_KEY}"
                f"&url={SEARCH_URL}"
                f"&render=false"
            )
            response = requests.get(scraper_url, timeout=30)
            if response.status_code == 200:
                html = response.text
                logger.info("ScraperAPI запрос успешен")
            else:
                logger.warning(f"ScraperAPI: статус {response.status_code}")
        except Exception as e:
            logger.warning(f"ScraperAPI не удался: {e}")

    if html is None:
        logger.error("Все методы запроса провалились")
        return None

    # --- Парсинг ---
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    for item in soup.select("li.ads-list-photo-item"):
        try:
            link_tag = item.select_one("a.ads-list-photo-item-title")
            if not link_tag:
                continue
            href = link_tag.get("href", "")
            title = link_tag.get_text(strip=True)
            if not any(kw in title.lower() for kw in KEYWORDS):
                continue
            ad_id_match = re.search(r"/(\d+)$", href)
            if not ad_id_match:
                continue
            ad_id = ad_id_match.group(1)
            price_tag = item.select_one(".ads-list-photo-item-price-wrapper")
            price = price_tag.get_text(strip=True) if price_tag else "Договорная"
            full_url = f"https://999.md{href}" if href.startswith("/") else href
            listings.append({"id": ad_id, "title": title, "price": price, "url": full_url})
        except Exception as e:
            logger.warning(f"Ошибка парсинга: {e}")

    logger.info(f"Найдено объявлений с яблоками: {len(listings)}")
    return listings


def escape_md(text: str) -> str:
    return re.sub(r"([\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])", r"\\\1", text)


def main_keyboard(monitoring_active: bool) -> InlineKeyboardMarkup:
    toggle_btn = (
        InlineKeyboardButton("⏸ Остановить мониторинг", callback_data="stop")
        if monitoring_active
        else InlineKeyboardButton("▶️ Запустить мониторинг", callback_data="start")
    )
    return InlineKeyboardMarkup([
        [toggle_btn],
        [InlineKeyboardButton("🔍 Проверить сейчас", callback_data="check_now"),
         InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("🗑 Сбросить кэш", callback_data="clear_cache"),
         InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]])


async def send_listing_notification(bot: Bot, listing: dict):
    title = escape_md(listing["title"])
    price = escape_md(listing["price"])
    time_str = escape_md(datetime.now().strftime("%d.%m.%Y %H:%M"))
    url = listing["url"]
    text = (
        "🍎 *Новое объявление\\!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 *{title}*\n\n"
        f"💰 Цена: {price}\n"
        f"🕐 {time_str}\n\n"
        f"[👁 Открыть объявление]({url})"
    )
    try:
        await bot.send_message(
            chat_id=CHAT_ID, text=text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👁 Смотреть на 999.md", url=url)]]),
            disable_notification=False,
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")


async def send_check_report(bot: Bot, new_count: int, total_checked: int, error: bool = False):
    now = escape_md(datetime.now().strftime("%H:%M"))
    interval = escape_md(str(CHECK_INTERVAL // 60))
    if error:
        hint = ""
        if not SCRAPER_API_KEY:
            hint = "\n💡 _Добавьте SCRAPER\\_API\\_KEY в Railway Variables_"
        text = (
            f"⚠️ `{now}` — 999\\.md блокирует запрос с сервера\\.\n"
            f"Следующая попытка через {interval} мин\\."
            f"{hint}"
        )
    elif new_count > 0:
        text = f"🍎 `{now}` — Найдено новых: *{new_count}* шт\\. Уведомления выше 👆"
    else:
        text = (
            f"🔄 `{now}` — Проверка ок\\. "
            f"Объявлений с яблоками: {escape_md(str(total_checked))}\\. "
            f"Новых нет\\. Следующая через {interval} мин\\."
        )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="MarkdownV2", disable_notification=True)
    except Exception as e:
        logger.error(f"Ошибка отправки отчёта: {e}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = load_status()
    mon = status.get("monitoring", False)
    scraper = "✅ Настроен" if SCRAPER_API_KEY else "❌ Не настроен"
    text = (
        "🍎 *Бот мониторинга яблок на 999\\.md*\n\n"
        f"📡 Статус: {'🟢 Активен' if mon else '🔴 Остановлен'}\n"
        f"⏱ Интервал: каждые {CHECK_INTERVAL // 60} мин\\.\n"
        f"🔑 ScraperAPI: {scraper}\n\n"
        "Нажмите кнопку для управления:"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=main_keyboard(mon))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    status = load_status()

    if data == "start":
        if status.get("monitoring"):
            await query.answer("Мониторинг уже запущен!", show_alert=True)
            return
        status["monitoring"] = True
        save_status(status)
        if not context.job_queue.get_jobs_by_name("monitor"):
            context.job_queue.run_repeating(auto_check_job, interval=CHECK_INTERVAL, first=5, name="monitor")
        await query.edit_message_text(
            f"✅ *Мониторинг запущен\\!*\n\n"
            f"Проверяю каждые *{CHECK_INTERVAL // 60} минут*\\.\n"
            f"📋 После каждой проверки — тихое сообщение с результатом\\.\n"
            f"🔔 При новом объявлении — громкое уведомление\\!",
            parse_mode="MarkdownV2", reply_markup=main_keyboard(True)
        )

    elif data == "stop":
        status["monitoring"] = False
        save_status(status)
        for job in context.job_queue.get_jobs_by_name("monitor"):
            job.schedule_removal()
        await query.edit_message_text(
            "⏸ *Мониторинг остановлен*\n\nНажмите *Запустить мониторинг*, чтобы возобновить\\.",
            parse_mode="MarkdownV2", reply_markup=main_keyboard(False)
        )

    elif data == "check_now":
        await query.edit_message_text("🔍 *Проверяю объявления\\.\\.\\.*", parse_mode="MarkdownV2")
        seen_ids = load_seen_ids()
        listings = fetch_listings()
        if listings is None:
            hint = ""
            if not SCRAPER_API_KEY:
                hint = "\n\n💡 Зарегистрируйтесь на scraperapi\\.com и добавьте SCRAPER\\_API\\_KEY в Railway Variables"
            result = f"❌ *999\\.md блокирует запросы с сервера*{hint}"
        else:
            new_listings = [l for l in listings if l["id"] not in seen_ids]
            if new_listings:
                for listing in new_listings:
                    await send_listing_notification(context.bot, listing)
                    seen_ids.add(listing["id"])
                save_seen_ids(seen_ids)
                status["total_found"] = status.get("total_found", 0) + len(new_listings)
                result = f"✅ *Найдено новых: {len(new_listings)}*\n\n📤 Уведомления выше\\!"
            else:
                result = (
                    f"✅ *Проверка завершена\\!*\n\n"
                    f"📭 Новых объявлений нет\\.\n"
                    f"Объявлений с яблоками на странице: {escape_md(str(len(listings)))} шт\\."
                )
            status["last_check"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            status["check_count"] = status.get("check_count", 0) + 1
            save_status(status)
        await query.edit_message_text(result, parse_mode="MarkdownV2", reply_markup=main_keyboard(status.get("monitoring", False)))

    elif data == "status":
        mon = status.get("monitoring", False)
        scraper = "✅ Настроен" if SCRAPER_API_KEY else "❌ Не настроен \\(нужен для обхода блокировки\\)"
        text = (
            "📊 *Статус мониторинга*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 Состояние: {'🟢 Активен' if mon else '🔴 Остановлен'}\n"
            f"⏱ Интервал: каждые {CHECK_INTERVAL // 60} мин\\.\n"
            f"🕐 Последняя проверка: {escape_md(status.get('last_check', 'Ещё не проверялось'))}\n"
            f"🔁 Всего проверок: {status.get('check_count', 0)}\n"
            f"📦 В кэше объявлений: {len(load_seen_ids())}\n"
            f"📬 Новых найдено за всё время: {status.get('total_found', 0)}\n"
            f"🔑 ScraperAPI: {scraper}"
        )
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=back_keyboard())

    elif data == "clear_cache":
        old_count = len(load_seen_ids())
        save_seen_ids(set())
        await query.edit_message_text(
            f"🗑 *Кэш сброшен\\!*\n\nУдалено записей: *{old_count}*\n\nПри следующей проверке все объявления будут показаны как новые\\.",
            parse_mode="MarkdownV2", reply_markup=back_keyboard()
        )

    elif data == "help":
        text = (
            "ℹ️ *Справка по боту*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"▶️ *Запустить мониторинг* — автопроверка каждые {CHECK_INTERVAL // 60} мин\\.\n\n"
            "⏸ *Остановить мониторинг* — отключить автопроверку\\.\n\n"
            "🔍 *Проверить сейчас* — немедленная ручная проверка\\.\n\n"
            "📊 *Статус* — состояние бота и статистика\\.\n\n"
            "🗑 *Сбросить кэш* — показать все объявления снова\\.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔑 *Если сайт блокирует запросы:*\n"
            "1\\. Зайдите на scraperapi\\.com\n"
            "2\\. Нажмите *Get Free API Key* \\(бесплатно 1000 запросов/месяц\\)\n"
            "3\\. Скопируйте API Key\n"
            "4\\. Railway → ваш сервис → Variables\n"
            "5\\. Добавьте: SCRAPER\\_API\\_KEY = ваш\\_ключ"
        )
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=back_keyboard())

    elif data == "back_to_main":
        mon = status.get("monitoring", False)
        scraper = "✅ Настроен" if SCRAPER_API_KEY else "❌ Не настроен"
        text = (
            "🍎 *Бот мониторинга яблок на 999\\.md*\n\n"
            f"📡 Статус: {'🟢 Активен' if mon else '🔴 Остановлен'}\n"
            f"⏱ Интервал: каждые {CHECK_INTERVAL // 60} мин\\.\n"
            f"🔑 ScraperAPI: {scraper}\n\n"
            "Нажмите кнопку для управления:"
        )
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=main_keyboard(mon))


async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    status = load_status()
    if not status.get("monitoring", False):
        return
    logger.info("Auto-check running...")
    seen_ids = load_seen_ids()
    listings = fetch_listings()
    if listings is None:
        await send_check_report(context.bot, 0, 0, error=True)
        return
    new_listings = [l for l in listings if l["id"] not in seen_ids]
    if new_listings:
        for listing in new_listings:
            await send_listing_notification(context.bot, listing)
            seen_ids.add(listing["id"])
        save_seen_ids(seen_ids)
        status["total_found"] = status.get("total_found", 0) + len(new_listings)
    await send_check_report(context.bot, len(new_listings), len(listings))
    status["last_check"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    status["check_count"] = status.get("check_count", 0) + 1
    save_status(status)


def main():
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN not set!")
        return
    if not CHAT_ID:
        print("ERROR: CHAT_ID not set!")
        return
    if not SCRAPER_API_KEY:
        print("WARNING: SCRAPER_API_KEY not set. 999.md may block direct requests.")

    print(f"Bot started. Interval: {CHECK_INTERVAL}s. ScraperAPI: {'yes' if SCRAPER_API_KEY else 'no'}")

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
