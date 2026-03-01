import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime

import aiohttp
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

# ============================================================
# НАСТРОЙКИ — заполните перед запуском
# ============================================================
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN_HERE"   # 8628403556:AAEd9wl3wc9W6NOU7REc__S0M_d9XsNw-Hw
CHAT_ID = "YOUR_CHAT_ID_HERE"            # 1090802357
CHECK_INTERVAL = 300                     # интервал проверки в секундах (5 минут)
# ============================================================

SEARCH_URL = "https://999.md/ru/list/food-and-agriculture/fruits-and-berries?eo%5B475%5D=7249"
# Если нужен конкретный поиск по слову "яблок":
# SEARCH_URL = "https://999.md/ru/list/food-and-agriculture/fruits-and-berries?q=%D1%8F%D0%B1%D0%BB%D0%BE%D0%BA%D0%B0"

SEEN_IDS_FILE = "seen_ids.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def load_seen_ids() -> set:
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(ids: set):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)
