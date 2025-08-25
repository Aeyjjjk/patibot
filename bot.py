import requests
import os
import pytz
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from textblob import TextBlob
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# === Load Environment Variables ===
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
GNEWS_KEY = os.getenv("GNEWS_KEY")
MEDIASTACK_KEY = os.getenv("MEDIASTACK_KEY")

# Broad watch list (background loop)
MASTER_KEYWORDS = [
    "Donald Trump",
    "Jerome Powell",
    "Non-Farm Payrolls",
    "NFP",
    "Consumer Price Index",
    "CPI",
]

# Narrow, topic-specific keyword sets (for /nfp and /cpi)
NFP_KEYWORDS = [
    "Non-Farm Payrolls",
    "NFP",
    "jobs report",
    "employment report",
    "payrolls",
    "BLS jobs",
    "unemployment rate",
]
CPI_KEYWORDS = [
    "Consumer Price Index",
    "CPI",
    "inflation",
    "inflation report",
    "inflation data",
    "price index",
]

# Track user subscriptions (per-chat loop)
subscribers: dict[int, bool] = {}

# === Helpers ===
def get_today() -> str:
    return datetime.now(pytz.UTC).strftime("%Y-%m-%d")

def get_sentiment(text: str) -> str:
    polarity = TextBlob(text).sentiment.polarity
    if polarity > 0.1:
        return "📈 +ve"
    elif polarity < -0.1:
        return "📉 -ve"
    else:
        return "⚖️ neutral"

def get_priority(text: str) -> str:
    high_priority_words = [
        "urgent", "breaking", "crisis", "announcement", "decision",
        "meeting", "speech", "jobs", "inflation", "report", "data", "release"
    ]
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
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

# === News Fetchers (parameterized by keywords) ===
def fetch_news_newsapi(keywords: list[str]) -> list[str]:
    if not NEWSAPI_KEY:
        return []
    url = (
        f"https://newsapi.org/v2/everything?"
        f"q={' OR '.join(keywords)}&apiKey={NEWSAPI_KEY}&language=en"
        f"&from={get_today()}&to={get_today()}&sortBy=publishedAt"
    )
    r = requests.get(url, timeout=20).json()
    return [f"{a.get('title','').strip()} - {a.get('url','')}"
            for a in r.get("articles", []) if a.get("title") and a.get("url")]

def fetch_news_gnews(keywords: list[str]) -> list[str]:
    if not GNEWS_KEY:
        return []
    url = (
        f"https://gnews.io/api/v4/search?"
        f"q={' OR '.join(keywords)}&lang=en&token={GNEWS_KEY}"
        f"&from={get_today()}&to={get_today()}"
    )
    r = requests.get(url, timeout=20).json()
    return [f"{a.get('title','').strip()} - {a.get('url','')}"
            for a in r.get("articles", []) if a.get("title") and a.get("url")]

def fetch_news_mediastack(keywords: list[str]) -> list[str]:
    if not MEDIASTACK_KEY:
        return []
    url = (
        f"http://api.mediastack.com/v1/news?"
        f"access_key={MEDIASTACK_KEY}&keywords={' OR '.join(keywords)}&languages=en"
        f"&date={get_today()}"
    )
    r = requests.get(url, timeout=20).json()
    return [f"{a.get('title','').strip()} - {a.get('url','')}"
            for a in r.get("data", []) if a.get("title") and a.get("url")]

def aggregate_news(keywords: list[str]) -> list[str]:
    # pull from all, dedupe, keep order
    all_news = []
    all_news.extend(fetch_news_newsapi(keywords))
    all_news.extend(fetch_news_gnews(keywords))
    all_news.extend(fetch_news_mediastack(keywords))
    return dedupe_keep_order(all_news)

# === Sending helpers ===
async def send_headlines(chat_id: int, context: ContextTypes.DEFAULT_TYPE, headlines: list[str], limit: int = 5):
    if not headlines:
        await context.bot.send_message(chat_id=chat_id, text="No fresh headlines found yet. I’ll keep watching 👀")
        return

    # Rank by priority (HIGH→LOW), then keep original order
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
    """Handle /start and spin up a personal news loop."""
    chat_id = update.effective_chat.id
    subscribers[chat_id] = True

    # Welcome
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Hello! I'm online and tracking news about Donald Trump, Jerome Powell, "
            "Non-Farm Payrolls (NFP), and Consumer Price Index (CPI).\n\n"
            "Each update includes:\n"
            "• Sentiment (📈 +ve / 📉 -ve / ⚖️ neutral)\n"
            "• Priority (🔥 HIGH / ⚡ MEDIUM / 🟢 LOW)\n\n"
            "Here’s a sample of how updates look:"
        ),
        parse_mode=ParseMode.HTML,
    )

    # Sample
    sample = "CPI inflation report shows unexpected rise - https://example.com/news"
    await send_headlines(chat_id, context, [sample], limit=1)

    # Start independent background loop for this user
    asyncio.create_task(news_loop(context, chat_id))

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribe user (stop their loop)."""
    chat_id = update.effective_chat.id
    subscribers[chat_id] = False
    await context.bot.send_message(chat_id=chat_id, text="🛑 Stopped news updates for this chat.")

async def nfp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """On-demand NFP headlines (latest)."""
    chat_id = update.effective_chat.id
    headlines = aggregate_news(NFP_KEYWORDS)
    await context.bot.send_message(chat_id=chat_id, text="💼 Latest NFP headlines:")
    await send_headlines(chat_id, context, headlines, limit=5)

async def cpi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """On-demand CPI headlines (latest)."""
    chat_id = update.effective_chat.id
    headlines = aggregate_news(CPI_KEYWORDS)
    await context.bot.send_message(chat_id=chat_id, text="📊 Latest CPI headlines:")
    await send_headlines(chat_id, context, headlines, limit=5)

# === Per-user background loop ===
async def news_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Fetch & send news for one chat in a loop (includes NFP/CPI automatically)."""
    sent_articles: set[str] = set()

    while subscribers.get(chat_id, False):
        try:
            # Watch broad set (includes Trump, Powell, NFP, CPI)
            headlines = aggregate_news(MASTER_KEYWORDS)

            for h in headlines:
                if h in sent_articles:
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
                sent_articles.add(h)

        except Exception as e:
            print("Error in loop:", e)

        # Check every 30 minutes (adjust if you want it faster)
        await asyncio.sleep(1800)

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
