import os
import csv
import io
import logging
import httpx
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
SHEET_CSV_URL  = os.environ["SHEET_CSV_URL"]
WEBHOOK_URL    = os.environ["WEBHOOK_URL"]  # https://your-app.onrender.com

COL_TITLE   = "Tựa"
COL_GENRE   = "Thể loại"
COL_AUTHOR  = "Tác giả"
COL_COUNTRY = "Quốc gia"
COL_LINK    = "xuxu review"

GREETING = (
    "📚 *Chào mừng đến bot tìm sách của xuxu!*\n\n"
    "Gõ tựa sách hoặc vài từ trong tựa để tìm kiếm.\n\n"
    "⚠️ *Lưu ý:* cần ít nhất 3 từ để tìm chính xác hơn.\n\n"
    "Ví dụ:\n"
    "• `Về nhà trước trời tối`\n"
    "• `Harry Potter cái chén`\n"
    "• `đời ai nấy chết`"
)

RULE_REMINDER = (
    "🔍 Hãy gõ *ít nhất 3 từ* trong tựa sách nhé!\n\n"
    "Ví dụ: `Về nhà trước trời tối`, `wild dark shore`"
)

# ── Fetch & search ────────────────────────────────────────────────────────────

async def fetch_books() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(SHEET_CSV_URL)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        return [row for row in reader]

def title_search(books: list[dict], query: str) -> list[dict]:
    q = query.lower().strip()
    return [b for b in books if q in b.get(COL_TITLE, "").lower()]

def format_results(books: list[dict], query: str) -> str:
    if not books:
        return f"😕 Không tìm thấy sách nào khớp với *\"{query}\"*.\n\nThử lại với từ khóa khác nhé!"
    lines = []
    for i, b in enumerate(books, 1):
        entry = (
            f"{i}. 📖 *{b.get(COL_TITLE,'?')}*\n"
            f"    👤 {b.get(COL_AUTHOR,'?')}  ·  🌍 {b.get(COL_COUNTRY,'')}  ·  🏷️ {b.get(COL_GENRE,'')}"
        )
        if b.get(COL_LINK):
            entry += f"\n    🔗 {b[COL_LINK]}"
        lines.append(entry)
    header = f"Tìm thấy *{len(books)}* cuốn cho *\"{query}\"*:\n\n"
    return header + "\n\n".join(lines)

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(GREETING, parse_mode="Markdown")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return
    if len(query.split()) < 3:
        await update.message.reply_text(RULE_REMINDER, parse_mode="Markdown")
        return
    await update.message.reply_chat_action("typing")
    try:
        books = await fetch_books()
    except Exception as e:
        logger.error(f"Sheet fetch error: {e}")
        await update.message.reply_text("❌ Không tải được dữ liệu. Thử lại sau nhé!")
        return
    results = title_search(books, query)
    await update.message.reply_text(
        format_results(results, query),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI()

# Build telegram app once at module level
tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

@app.on_event("startup")
async def startup():
    await tg_app.initialize()
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook set to {WEBHOOK_URL}/webhook")

@app.on_event("shutdown")
async def shutdown():
    await tg_app.shutdown()

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def health():
    return {"status": "ok"}
