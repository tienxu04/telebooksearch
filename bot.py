import os
import csv
import io
import json
import logging
import httpx
from unidecode import unidecode
from rapidfuzz import fuzz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
SHEET_CSV_URL  = os.environ["SHEET_CSV_URL"]
WEBHOOK_URL    = os.environ["WEBHOOK_URL"]

COL_TITLE   = "Tựa"
COL_GENRE   = "Thể loại"
COL_AUTHOR  = "Tác giả"
COL_COUNTRY = "Quốc gia"
COL_LINK    = "xuxu review"

PAGE_SIZE  = 5
THRESHOLD  = 70  # fuzzy score tối thiểu

# ── Fetch ─────────────────────────────────────────────────────────────────────

async def fetch_books() -> list[dict]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(SHEET_CSV_URL)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        return [{k.strip(): v.strip() for k, v in row.items()} for row in reader]

# ── Fuzzy search ──────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    """Lowercase + bỏ dấu để compare."""
    return unidecode(s).lower().strip()

def fuzzy_search(books: list[dict], query: str) -> list[dict]:
    q = normalize(query)
    scored = []
    for b in books:
        title = b.get(COL_TITLE, "")
        score = max(fuzz.token_set_ratio(q, normalize(title)), fuzz.partial_ratio(q, normalize(title)))
        if score >= THRESHOLD:
            scored.append((score, b))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Nếu có perfect match (score 100) thì chỉ trả những cuốn đó thôi
    perfect = [b for score, b in scored if score == 100]
    if perfect:
        return perfect

    return [b for _, b in scored]

# ── Country flags ─────────────────────────────────────────────────────────────

COUNTRY_FLAGS = {
    "Mỹ": "🇺🇸", "Đức": "🇩🇪", "Úc": "🇦🇺", "Anh": "🇬🇧",
    "Hàn Quốc": "🇰🇷", "Việt Nam": "🇻🇳", "Nhật": "🇯🇵",
    "Thụy Điển": "🇸🇪", "Pháp": "🇫🇷", "Bồ Đào Nha": "🇵🇹",
    "Czech": "🇨🇿", "Brazil": "🇧🇷", "Đan Mạch": "🇩🇰",
    "Bỉ": "🇧🇪", "Phần Lan": "🇫🇮", "Trung Quốc": "🇨🇳",
    "Ba Lan": "🇵🇱", "Thụy Sĩ": "🇨🇭", "Đài Loan": "🇹🇼",
    "Ireland": "🇮🇪", "Afghanistan": "🇦🇫", "Na Uy": "🇳🇴",
    "Hong Kong": "🇭🇰", "Ý": "🇮🇹", "Thái Lan": "🇹🇭",
    "Israel": "🇮🇱", "Triều Tiên": "🇰🇵", "Hà Lan": "🇳🇱",
    "Hungary": "🇭🇺", "Belarus": "🇧🇾", "Nga": "🇷🇺",
    "Trung": "🇨🇳",
}

def country_flag(country: str) -> str:
    return COUNTRY_FLAGS.get(country) or "🌍"

def escape_md(s: str) -> str:
    for ch in ["*", "_", "`", "[", "]"]:
        s = s.replace(ch, f"\\{ch}")
    return s

# ── Format ────────────────────────────────────────────────────────────────────

def format_book(i: int, b: dict) -> str:
    title   = escape_md(b.get(COL_TITLE, "?"))
    author  = escape_md(b.get(COL_AUTHOR, "?"))
    country = b.get(COL_COUNTRY, "")
    genre   = b.get(COL_GENRE, "")
    flag    = country_flag(country)
    entry = (
        f"{i}. 📖 *{title}*\n"
        f"    👤 {author}  ·  {flag} {country}  ·  🏷️ {genre}"
    )
    if b.get(COL_LINK):
        entry += f"\n    🔗 {b[COL_LINK]}"
    return entry

def format_page(results: list[dict], offset: int) -> str:
    page = results[offset:offset + PAGE_SIZE]
    return "\n\n".join(format_book(offset + i + 1, b) for i, b in enumerate(page))

def more_button(query: str, offset: int, total: int) -> InlineKeyboardMarkup:
    remaining = total - offset
    payload = json.dumps({"q": query, "o": offset}, ensure_ascii=False)
    btn = InlineKeyboardButton(
        f"Xem {min(PAGE_SIZE, remaining)} cuốn tiếp ›",
        callback_data=payload,
    )
    return InlineKeyboardMarkup([[btn]])

# ── Send results ──────────────────────────────────────────────────────────────

async def send_results(send_fn, results: list[dict], query: str, offset: int = 0):
    total = len(results)
    text  = format_page(results, offset)
    new_offset = offset + PAGE_SIZE

    if new_offset < total:
        await send_fn(
            text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=more_button(query, new_offset, total),
        )
    else:
        suffix = "\n\n_Vậy là hết rồi!_" if offset > 0 else ""
        await send_fn(
            text + suffix,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *Tìm sách của xuxu*\n\n"
        "Gõ tên sách — hoặc một phần tên — mình sẽ tìm!\n\n"
        "VD: `ove`, `dong cam`, `housemaid`, `Saramago`",
        parse_mode="Markdown",
    )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return

    await update.message.reply_chat_action("typing")

    try:
        books = await fetch_books()
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        await update.message.reply_text("❌ Không tải được dữ liệu, thử lại sau nhé!")
        return

    results = fuzzy_search(books, query)

    if not results:
        await update.message.reply_text(
            f"📭 Trong danh sách chưa có sách nào khớp với *\"{query}\"*.",
            parse_mode="Markdown",
        )
        return

    await send_results(
        update.message.reply_text,
        results, query, offset=0,
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    await cb.answer()

    try:
        payload = json.loads(cb.data)
        query  = payload["q"]
        offset = payload["o"]
    except Exception:
        await cb.message.reply_text("❌ Có lỗi, thử tìm lại nhé!")
        return

    try:
        books = await fetch_books()
    except Exception:
        await cb.message.reply_text("❌ Không tải được dữ liệu.")
        return

    results = fuzzy_search(books, query)
    await send_results(cb.message.reply_text, results, query, offset=offset)

# ── App ───────────────────────────────────────────────────────────────────────

from fastapi import FastAPI, Request

app = FastAPI()
tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
tg_app.add_handler(CallbackQueryHandler(button))

@app.on_event("startup")
async def startup():
    await tg_app.initialize()
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook set: {WEBHOOK_URL}/webhook")

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
