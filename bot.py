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

# Keywords we want to monitor
KEYWORDS = ["Donald Trump", "Jerome Powell", "Non-Farm Payrolls", "NFP", "Consumer Price Index", "CPI"]

# Track user subscriptions
subscribers = {}

# === Helpers ===
def get_today():
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
    high_priority_words = ["urgent", "breaking", "crisis", "announcement", "decision", "meeting", "speech", "jobs", "inflation"]
    if any(word.lower() in text.lower() for word in high_priority_words):
        return "🔥 HIGH"
    elif "report" in text.lower() or "update" in text.lower():
        return "⚡ MEDIUM"
    else:
        return "🟢 LOW"

# === News Fetchers ===
def fetch_news_newsapi():
    url = (
        f"https://newsapi.org/v2/everything?"
        f"q={' OR '.join(KEYWORDS)}&apiKey={NEWSAPI_KEY}&language=en"
        f"&from={get_today()}&to={get_today()}&sortBy=publishedAt"
    )
    r = requests.get(url).json()
    return [article["title"] + " - " + article["url"] for article in r.get("articles", [])]

def fetch_news_gnews():
    url = (
        f"https://gnews.io/api/v4/search?"
        f"q={' OR '.join(KEYWORDS)}&lang=en&token={GNEWS_KEY}"
        f"&from={get_today()}&to={get_today()}"
    )
    r = requests.get(url).json()
    return [article["title"] + " - " + article["url"] for article in r.get("articles", [])]

def fetch_news_mediastack():
    url = (
        f"http://api.mediastack.com/v1/news?"
        f"access_key={MEDIASTACK_KEY}&keywords={' OR '.join(KEYWORDS)}&languages=en"
        f"&date={get_today()}"
    )
    r = requests.get(url).json()
    return [article["title"] + " - " + article["url"] for article in r.get("data", [])]

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command and subscribe user"""
    chat_id = update.effective_chat.id
    subscribers[chat_id] = True

    # Welcome message
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Hello! I am now online and listening for news updates.\n\n"
            "✅ I’ll track fresh stories about Donald Trump, Jerome Powell, NFP, and CPI.\n"
            "📰 Each update will include:\n"
            "- Sentiment (📈 +ve / 📉 -ve / ⚖️ neutral)\n"
            "- Priority level (🔥 HIGH / ⚡ MEDIUM / 🟢 LOW)\n\n"
            "Here’s a sample of how updates will look: 👇"
        ),
        parse_mode=ParseMode.HTML
    )

    # Example sample news
    sample_news = "CPI inflation report shows unexpected rise - https://example.com/news"
    sample_sentiment = get_sentiment(sample_news)
    sample_priority = get_priority(sample_news)

    sample_message = (
        f"📰 {sample_news}\n\n"
        f"Sentiment: {sample_sentiment}\n"
        f"Priority: {sample_priority}"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=sample_message,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    # Start independent loop for this user
    asyncio.create_task(news_loop(context, chat_id))

async def news_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Independent loop per user to fetch/send news"""
    sent_articles = set()
    while subscribers.get(chat_id, False):
        try:
            all_news = []
            all_news.extend(fetch_news_newsapi())
            all_news.extend(fetch_news_gnews())
            all_news.extend(fetch_news_mediastack())

            for news in all_news:
                if news not in sent_articles:
                    sentiment = get_sentiment(news)
                    priority = get_priority(news)

                    message = (
                        f"📰 {news}\n\n"
                        f"Sentiment: {sentiment}\n"
                        f"Priority: {priority}"
                    )

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                    sent_articles.add(news)

        except Exception as e:
            print("Error in loop:", e)

        await asyncio.sleep(1800)  # wait 30 mins before checking again

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribe user"""
    chat_id = update.effective_chat.id
    subscribers[chat_id] = False
    await context.bot.send_message(chat_id=chat_id, text="🛑 You have stopped receiving news updates.")

# Manual commands for NFP & CPI
async def nfp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💼 Tracking latest Non-Farm Payrolls news... updates will appear automatically.")

async def cpi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Tracking latest Consumer Price Index (CPI) news... updates will appear automatically.")

# === Main ===
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("nfp", nfp))
    application.add_handler(CommandHandler("cpi", cpi))

    # Run bot
    application.run_polling()

if __name__ == "__main__":
    main()
