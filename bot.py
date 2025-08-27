import requests
import os
import pytz
import json
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue
from collections import defaultdict

# === Load Environment Variables ===
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
GNEWS_KEY = os.getenv("GNEWS_KEY")
MEDIASTACK_KEY = os.getenv("MEDIASTACK_KEY")
NEWSDATA_KEY = os.getenv("NEWSDATA_KEY")

# === Settings ===
MASTER_KEYWORDS = [
    "Donald Trump", "Jerome Powell", "Non-Farm Payrolls", "NFP",
    "Consumer Price Index", "CPI", "Federal Reserve", "interest rates",
    "economy", "stock market", "inflation", "unemployment"
]
NFP_KEYWORDS = [
    "Non-Farm Payrolls", "NFP", "jobs report", "employment report",
    "payrolls", "BLS jobs", "unemployment rate", "wage growth"
]
CPI_KEYWORDS = [
    "Consumer Price Index", "CPI", "inflation", "inflation report",
    "inflation data", "price index", "consumer prices"
]

STORAGE_FILE = "news_storage.json"
EXPIRY_DAYS = 3  # auto-expire storage after 3 days
CHECK_INTERVAL = 1200  #20 minutes

# Track user subscriptions
subscribers = defaultdict(bool)

# === Storage Helpers ===
def load_storage() -> dict:
    if not os.path.exists(STORAGE_FILE):
        return {}
    try:
        with open(STORAGE_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
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
    positive = ["gain", "growth", "rise", "optimistic", "positive", "bullish", "strong", "surge", "profit", "recovery", "rally"]
    negative = ["fall", "drop", "loss", "crisis", "bearish", "weak", "negative", "decline", "debt", "crash", "recession"]
    if any(w in text_l for w in positive):
        return "🙂 Positive"
    elif any(w in text_l for w in negative):
        return "☹️ Negative"
    else:
        return "😐 Neutral"

def get_priority(text: str) -> str:
    text_l = text.lower()
    if any(w in text_l for w in ["urgent", "breaking", "crisis", "announcement", "decision", "meeting", "speech", "alert"]):
        return "🔥 HIGH"
    elif any(w in text_l for w in ["report", "update", "data", "release", "jobs", "inflation", "fed", "rates"]):
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
        print("NewsAPI key not found")
        return []
    try:
        from_date, to_date = get_date_range(3)
        url = (
            f"https://newsapi.org/v2/everything?"
            f"q={' OR '.join(keywords)}&apiKey={NEWSAPI_KEY}&language=en"
            f"&from={from_date}&to={to_date}&sortBy=publishedAt"
        )
        response = requests.get(url, timeout=20)
        r = response.json()
        
        if r.get("status") == "error":
            print(f"NewsAPI Error: {r.get('message')}")
            return []
            
        return [f"{a.get('title','').strip()} - {a.get('url','')}"
                for a in r.get("articles", []) if a.get("title") and a.get("url")]
    except Exception as e:
        print(f"Error fetching from NewsAPI: {e}")
        return []

def fetch_news_gnews(keywords: list[str]) -> list[str]:
    if not GNEWS_KEY:
        print("GNews key not found")
        return []
    try:
        from_date, to_date = get_date_range(3)
        url = (
            f"https://gnews.io/api/v4/search?"
            f"q={' OR '.join(keywords)}&lang=en&token={GNEWS_KEY}"
            f"&from={from_date}&to={to_date}"
        )
        response = requests.get(url, timeout=20)
        r = response.json()
        return [f"{a.get('title','').strip()} - {a.get('url','')}"
                for a in r.get("articles", []) if a.get("title") and a.get("url")]
    except Exception as e:
        print(f"Error fetching from GNews: {e}")
        return []

def fetch_news_mediastack(keywords: list[str]) -> list[str]:
    if not MEDIASTACK_KEY:
        print("MediaStack key not found")
        return []
    try:
        from_date, to_date = get_date_range(3)
        url = (
            f"http://api.mediastack.com/v1/news?"
            f"access_key={MEDIASTACK_KEY}&keywords={' OR '.join(keywords)}&languages=en"
            f"&date={from_date},{to_date}"
        )
        response = requests.get(url, timeout=20)
        r = response.json()
        return [f"{a.get('title','').strip()} - {a.get('url','')}"
                for a in r.get("data", []) if a.get("title") and a.get("url")]
    except Exception as e:
        print(f"Error fetching from MediaStack: {e}")
        return []

def fetch_news_newsdata(keywords: list[str]) -> list[str]:
    if not NEWSDATA_KEY:
        print("NewsData.io key not found")
        return []
    try:
        # NewsData.io uses a different date format
        to_date = datetime.now(pytz.UTC).strftime("%Y-%m-%d")
        from_date = (datetime.now(pytz.UTC) - timedelta(days=3)).strftime("%Y-%m-%d")
        
        url = (
            f"https://newsdata.io/api/1/news?"
            f"apikey={NEWSDATA_KEY}&q={' OR '.join(keywords)}&language=en"
            f"&from_date={from_date}&to_date={to_date}"
        )
        response = requests.get(url, timeout=20)
        r = response.json()
        
        if r.get("status") != "success":
            print(f"NewsData.io Error: {r.get('results', [{}])[0].get('message', 'Unknown error')}")
            return []
            
        return [f"{a.get('title','').strip()} - {a.get('link','')}"
                for a in r.get("results", []) if a.get("title") and a.get("link")]
    except Exception as e:
        print(f"Error fetching from NewsData.io: {e}")
        return []

def aggregate_news(keywords: list[str]) -> list[str]:
    all_news = []
    all_news.extend(fetch_news_newsapi(keywords))
    all_news.extend(fetch_news_gnews(keywords))
    all_news.extend(fetch_news_mediastack(keywords))
    all_news.extend(fetch_news_newsdata(keywords))
    
    deduped = dedupe_keep_order(all_news)
    return filter_headlines(deduped, keywords)

# === Sending Helpers ===
async def send_headlines(chat_id: int, context: ContextTypes.DEFAULT_TYPE, headlines: list[str], limit: int = 5):
    if not headlines:
        await context.bot.send_message(chat_id=chat_id, text="No fresh headlines found yet. I'll keep watching 👀")
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
        text="👋 Hello! I'll track financial/economic news for you. Updates every 20 minutes. /help for more commands ",
        parse_mode=ParseMode.HTML,
    )

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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 Financial News Bot Help:

/start - Start receiving news updates
/stop - Stop receiving news updates
/nfp - Get latest Non-Farm Payrolls news
/cpi - Get latest Consumer Price Index news
/help - Show this help message

I monitor multiple news sources for financial and economic news, with a focus on:
- Federal Reserve announcements
- Employment data (NFP)
- Inflation data (CPI)
- Market-moving economic indicators
"""
    await update.message.reply_text(help_text)

# === Background Task ===
async def news_task(context: ContextTypes.DEFAULT_TYPE):
    storage = load_storage()
    
    # Get all active subscribers
    active_chats = [chat_id for chat_id, active in subscribers.items() if active]
    
    if not active_chats:
        print("No active subscribers, skipping news check")
        return
        
    print(f"Checking news for {len(active_chats)} subscribers")
    
    try:
        headlines = aggregate_news(MASTER_KEYWORDS)
        new_headlines = [h for h in headlines if h not in storage]
        
        if not new_headlines:
            print("No new headlines found")
            return
            
        print(f"Found {len(new_headlines)} new headlines")
        
        for headline in new_headlines:
            sent = get_sentiment(headline)
            pr = get_priority(headline)
            msg = f"📰 {headline}\n\nSentiment: {sent}\nPriority: {pr}"
            
            # Send to all active subscribers
            for chat_id in active_chats:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"Error sending to {chat_id}: {e}")
                    # If there's an error (like user blocked bot), unsubscribe them
                    subscribers[chat_id] = False
            
            mark_sent(storage, headline)
            
    except Exception as e:
        print(f"Error in news task: {e}")

# === Main ===
def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in environment variables")
        return
        
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("nfp", nfp))
    application.add_handler(CommandHandler("cpi", cpi))
    application.add_handler(CommandHandler("help", help_command))
    
    # Create background task
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(
            lambda context: asyncio.create_task(news_task(context)), 
            interval=CHECK_INTERVAL, 
            first=10
        )
        print(f"News task scheduled to run every {CHECK_INTERVAL} seconds")
    else:
        print("Job queue not available")
    
    print("Bot started...")
    application.run_polling()

if __name__ == "__main__":
    main()
