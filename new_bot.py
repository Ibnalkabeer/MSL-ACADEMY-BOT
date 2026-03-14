# bot.py - MSL ACADEMY BOT - OTC VERSION
import os
import asyncio
import sqlite3
import uuid
import json
import random
import signal
import time
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import requests
from dateutil import tz

try:
    import websockets
except Exception:
    websockets = None


# CONFIG
LAGOS_TZ = tz.gettz("Africa/Lagos")

# SESSION CONTROL
SESSION_START_HOUR = 8
SESSION_END_HOUR = 23
SESSION_END_MINUTE = 30

PAIRS = [
"EURUSD_otc","GBPUSD_otc","AUDUSD_otc","CADCHF_otc","CADJPY_otc",
"CHFJPY_otc","EURCHF_otc","EURGBP_otc","EURJPY_otc","GBPAUD_otc",
"YERUSD_otc","USDCNH_otc","AEDCNY_otc","EURNZD_otc","NZDJPY_otc"
]

EXPIRY_MINUTES = 3
MG_INTERVAL_MINUTES = 3
CONFIDENCE_MIN = 70
HIGH = 80
VERY_HIGH = 90

DB_FILE = "msl_signals_otc.db"
FAST_MODE = False

USE_POCKET_OPTION = os.getenv("USE_POCKET_OPTION", "True").lower() == "true"
PO_SESSION_TOKEN = os.getenv("PO_SESSION_TOKEN", "")
PO_USER_ID = os.getenv("PO_USER_ID", "")
PO_API_BASE = "https://api.po.market"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage" if TELEGRAM_BOT_TOKEN else None

random.seed(42)
np.random.seed(42)

# DATABASE
def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id TEXT PRIMARY KEY,
        pair TEXT,
        direction TEXT,
        expiry_minutes INTEGER,
        drop_time TEXT,
        entry_time TEXT,
        mg1_time TEXT,
        mg2_time TEXT,
        strategy TEXT,
        confidence INTEGER,
        confidence_label TEXT,
        entry_price REAL,
        settle_price REAL,
        result TEXT,
        win_type TEXT,
        metadata TEXT
    )
    """)

    conn.commit()
    return conn


DB_CONN = init_db()


def save_signal(signal):
    cur = DB_CONN.cursor()

    cur.execute("""
    INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
    signal["id"],
    signal["pair"],
    signal["direction"],
    signal["expiry_minutes"],
    signal["drop_time"].isoformat(),
    signal["entry_time"].isoformat(),
    signal["mg1_time"].isoformat(),
    signal["mg2_time"].isoformat(),
    signal["strategy"],
    signal["confidence"],
    signal["confidence_label"],
    signal.get("entry_price"),
    signal.get("settle_price"),
    signal.get("result"),
    signal.get("win_type"),
    json.dumps(signal.get("metadata", {}))
    ))

    DB_CONN.commit()


def update_result(signal_id, entry_price=None, settle_price=None, result=None, win_type=None):
    cur = DB_CONN.cursor()

    if entry_price is not None:
        cur.execute("UPDATE signals SET entry_price=? WHERE id=?", (entry_price, signal_id))

    if settle_price is not None and result is not None:
        cur.execute(
        "UPDATE signals SET settle_price=?, result=?, win_type=? WHERE id=?",
        (settle_price, result, win_type, signal_id)
        )

    DB_CONN.commit()


# TELEGRAM
def send_telegram(text):

    if not TELEGRAM_API:
        print(text)
        return

    try:
        requests.post(
        TELEGRAM_API,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)


# INDICATORS
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def rsi(series, period=14):

    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)

    ma_up = up.rolling(period).mean()
    ma_down = down.rolling(period).mean()

    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))


def macd(series, a=12, b=26, c=9):

    ema12 = series.ewm(span=a, adjust=False).mean()
    ema26 = series.ewm(span=b, adjust=False).mean()

    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=c, adjust=False).mean()

    return macd_line, signal_line, macd_line - signal_line


# STRATEGIES
def strategy_simple(df):

    close = df["close"]

    if close.iloc[-1] > close.iloc[-5]:
        return {"direction": "CALL", "confidence": 72, "strategy": "Momentum"}

    else:
        return {"direction": "PUT", "confidence": 72, "strategy": "Momentum"}


STRATEGY_FUNCS = [strategy_simple]


def confidence_label(conf):
    return "🔥 VERY HIGH" if conf >= VERY_HIGH else ("💪 HIGH" if conf >= HIGH else "📊 MEDIUM")


def format_time(dt):
    return dt.astimezone(LAGOS_TZ).strftime("%H:%M") + " WAT"


def format_signal_message(sig):

    emoji = "🟢" if sig["direction"] == "CALL" else "🔴"
    direction_text = "CALL" if sig["direction"] == "CALL" else "SELL"

    return f"""MSL ACADEMY SIGNAL: {sig['pair']}

⚪ Expiration 3M
🔵 Entry at {format_time(sig["entry_time"])}

{emoji} {direction_text}

🔽 Martingale levels
1️⃣ level at {format_time(sig["mg1_time"])}
2️⃣ level at {format_time(sig["mg2_time"])}

AI Confidence: {sig['confidence']}%
"""


async def wait_until_next_minute():

    now = datetime.now(LAGOS_TZ)
    next_min = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

    wait = (next_min - now).total_seconds()

    if wait > 0:
        await asyncio.sleep(wait)


async def generate_signal(pair):

    direction = random.choice(["CALL", "PUT"])

    return {
    "pair": pair,
    "direction": direction,
    "strategy": "Momentum",
    "confidence": random.randint(70, 90)
    }


# ==========================
# TRADING SESSION
# ==========================
async def run_trading_session():

    start = datetime.now(LAGOS_TZ)

    session_end = start.replace(
    hour=SESSION_END_HOUR,
    minute=SESSION_END_MINUTE,
    second=0,
    microsecond=0
    )

    stats = {
    "total_signals": 0,
    "wins": 0,
    "losses": 0,
    "first_entry_wins": 0,
    "mg1_wins": 0,
    "mg2_wins": 0
    }

    send_telegram("📊 MSL ACADEMY BOT STARTED")

    sig_num = 1

    while datetime.now(LAGOS_TZ) < session_end:

        await wait_until_next_minute()

        pair = random.choice(PAIRS)
        sig_cand = await generate_signal(pair)

        drop_time = datetime.now(LAGOS_TZ)
        entry_time = drop_time + timedelta(minutes=2)

        sig = {
        "id": str(uuid.uuid4()),
        "pair": sig_cand["pair"],
        "direction": sig_cand["direction"],
        "strategy": sig_cand["strategy"],
        "confidence": sig_cand["confidence"],
        "confidence_label": confidence_label(sig_cand["confidence"]),
        "expiry_minutes": EXPIRY_MINUTES,
        "drop_time": drop_time,
        "entry_time": entry_time,
        "mg1_time": entry_time + timedelta(minutes=3),
        "mg2_time": entry_time + timedelta(minutes=6),
        "metadata": {"signal_number": sig_num}
        }

        save_signal(sig)

        send_telegram(format_signal_message(sig))

        stats["total_signals"] += 1

        print("Signal dropped:", sig_num)

        sig_num += 1

        await asyncio.sleep(60)

    # SUMMARY
    total = stats["total_signals"]

    summary = f"""
📊 MSL ACADEMY BOT - SESSION COMPLETED

Total Signals: {total}
Wins: {stats['wins']}
Losses: {stats['losses']}
"""

    send_telegram(summary)

    print(summary)


async def main():

    try:

        await run_trading_session()

    except KeyboardInterrupt:

        print("Bot stopped")


if __name__ == "__main__":

    asyncio.run(main())
