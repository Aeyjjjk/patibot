import requests
import os
import pytz
import json
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# === Load Environment Variables ===
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
GNEWS_KEY = os.getenv("GNEWS_KEY")
MEDIASTACK_KEY = os.getenv("MEDIASTACK_KEY")

# === Settings ===
MASTER_KEYWORDS = [
    "Donald Trump", "Jerome Powell", "Non-Farm Payrolls", "NFP",
    "Consumer Price Index", "CPI"
]
NFP_KEYWORDS = [
    "Non-Farm Payrolls", "NFP", "jobs report", "employment report",
    "payrolls", "BLS jobs", "unemployment rate"
]
CPI_KEYWORDS = [
    "Consumer Price Index", "CPI", "inflation", "inflation report",
    "inflation data", "price index"
]

STORAGE_FILE = "news_storage.json"
EXPIRY_DAYS = 3  # auto-expire storage after 3 days
CHECK_INTERVAL = 3600  # 5 minutes

# Track user subscriptions
subscribers: dict[int, bool] = {}

# === Storage Helpers ===
def load_storage() -> dict:
    if not os.path.exists(STORAGE_FILE):
        return {}
    try:
        with open(STORAGE_FILE, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return {}

    cutoff = datetime.now(pytz.UTC) - timedelta(days=EXPIRY_DAYS)
    fresh = {
        k: v for k, v in data.items()
        if datetime.fromisoformat(v["timestamp"]) > cutoff
    }
    return fresh

def save_storage(storage: dict):
    with open(STORAGE_FILE, "w") as f:
        json.dump(storage, f, indent=2)

def mark_sent(storage: dict, headline: str):
    storage[headline] = {"timestamp": datetime.now(pytz.UTC).isoformat()}
    save_storage(storage)

# === Helpers ===
def get_date_range(days: int = 3) -> tuple[str, str]:
    to_date = datetime.now(pytz.UTC).strftime("%Y-%m-%d")
    from_date = (datetime.now(pytz.UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    return from_date, to_date

def get_sentiment(text: str) -> str:
    text_l = text.lower()
    positive = ["gain", "growth", "rise", "optimistic", "positive", "bullish", "strong", "surge", "profit"]
    negative = ["fall", "drop", "loss", "crisis", "bearish", "weak", "negative", "decline", "debt"]
    if any(w in text_l for w in positive):
        return "🙂 Positive"
    elif any(w in text_l for w in negative):
        return "☹️ Negative"
    else:
        return "😐 Neutral"

def get_priority(text: str) -> str:
    text_l = text.lower()
    if any(w in text_l for w in ["urgent", "breaking", "crisis", "announcement", "decision", "meeting", "speech"]):
        return "🔥 HIGH"
    elif any(w in text_l for w in ["report", "update", "data", "release", "jobs", "inflation"]):
        return "⚡ MEDIUM"
    else:
        return "🟢 LOW"

def priority_score(label: str) -> int:
    return {"🔥 HIGH": 3, "⚡ MEDIUM": 2, "🟢 LOW": 1}.get(label, 0)

def dedupe_keep_order(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def filter_headlines(headlines: list[str], keywords: list[str]) -> list[str]:
    keywords_lower = [k.lower() for k in keywords]
    return [h for h in headlines if any(k in h.lower() for k in keywords_lower)]

# === News Fetchers ===
def fetch_news_newsapi(keywords: list[str]) -> list[str]:
    if not NEWSAPI_KEY:
        return []
    from_date, to_date = get_date_range(3)
    url = (
        f"https://newsapi.org/v2/everything?"
        f"q={' OR '.join(keywords)}&apiKey={NEWSAPI_KEY}&language=en"
        f"&from={from_date}&to={to_date}&sortBy=publishedAt"
    )
    r = requests.get(url, timeout=20).json()
    return [f"{a.get('title','').strip()} - {a.get('url','')}"
            for a in r.get("articles", []) if a.get("title") and a.get("url")]

def fetch_news_gnews(keywords: list[str]) -> list[str]:
    if not GNEWS_KEY:
        return []
    from_date, to_date = get_date_range(3)
    url = (
        f"https://gnews.io/api/v4/search?"
        f"q={' OR '.join(keywords)}&lang=en&token={GNEWS_KEY}"
        f"&from={from_date}&to={to_date}"
    )
    r = requests.get(url, timeout=20).json()
    return [f"{a.get('title','').strip()} - {a.get('url','')}"
            for a in r.get("articles", []) if a.get("title") and a.get("url")]

def fetch_news_mediastack(keywords: list[str]) -> list[str]:
    if not MEDIASTACK_KEY:
        return []
    from_date, to_date = get_date_range(3)
    url = (
        f"http://api.mediastack.com/v1/news?"
        f"access_key={MEDIASTACK_KEY}&keywords={' OR '.join(keywords)}&languages=en"
        f"&date={from_date},{to_date}"
    )
    r = requests.get(url, timeout=20).json()
    return [f"{a.get('title','').strip()} - {a.get('url','')}"
            for a in r.get("data", []) if a.get("title") and a.get("url")]

def aggregate_news(keywords: list[str]) -> list[str]:
    all_news = []
    all_news.extend(fetch_news_newsapi(keywords))
    all_news.extend(fetch_news_gnews(keywords))
    all_news.extend(fetch_news_mediastack(keywords))
    deduped = dedupe_keep_order(all_news)
    return filter_headlines(deduped, keywords)

# === Sending Helpers ===
async def send_headlines(chat_id: int, context: ContextTypes.DEFAULT_TYPE, headlines: list[str], limit: int = 5):
    if not headlines:
        await context.bot.send_message(chat_id=chat_id, text="No fresh headlines found yet. I’ll keep watching 👀")
        return

    scored = []
    for h in headlines:
        pr = get_priority(h)
        scored.append((priority_score(pr), pr, get_sentiment(h), h))
    scored.sort(key=lambda x: x[0], reverse=True)

    for _, pr, sent, h in scored[:limit]:
        msg = f"📰 {h}\n\nSentiment: {sent}\nPriority: {pr}"
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers[chat_id] = True

    await context.bot.send_message(
        chat_id=chat_id,
        text="👋 Hello! I’ll track financial/economic news for you. Updates every 60 minutes.",
        parse_mode=ParseMode.HTML,
    )

    asyncio.create_task(news_loop(context, chat_id))

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers[chat_id] = False
    await context.bot.send_message(chat_id=chat_id, text="🛑 Stopped news updates for this chat.")

async def nfp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    headlines = aggregate_news(NFP_KEYWORDS)
    await context.bot.send_message(chat_id=chat_id, text="💼 Latest NFP headlines:")
    await send_headlines(chat_id, context, headlines, limit=5)

async def cpi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    headlines = aggregate_news(CPI_KEYWORDS)
    await context.bot.send_message(chat_id=chat_id, text="📊 Latest CPI headlines:")
    await send_headlines(chat_id, context, headlines, limit=5)

# === Background Loop with Storage ===
async def news_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    storage = load_storage()

    while subscribers.get(chat_id, False):
        try:
            headlines = aggregate_news(MASTER_KEYWORDS)

            for h in headlines:
                if h in storage:
                    continue
                sent = get_sentiment(h)
                pr = get_priority(h)
                msg = f"📰 {h}\n\nSentiment: {sent}\nPriority: {pr}"

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )
                mark_sent(storage, h)

        except Exception as e:
            print("Error in loop:", e)

        await asyncio.sleep(CHECK_INTERVAL)

# === Main ===
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("nfp", nfp))
    application.add_handler(CommandHandler("cpi", cpi))
    application.run_polling()

if __name__ == "__main__":
    main()