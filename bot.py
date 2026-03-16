import os
import csv
import io
import logging
import httpx
from collections import Counter
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

PAGE_SIZE = 3

GENRES = [
    "Trinh thám", "Tiểu thuyết", "Phi hư cấu",
    "Tập truyện", "Tự truyện/Hồi ký", "Truyện tranh", "Truyện ngắn"
]

# Synonym map: những gì user có thể gõ → giá trị trong sheet
COUNTRY_SYNONYMS = {
    "my": "Mỹ", "mỹ": "Mỹ", "hoa ky": "Mỹ", "america": "Mỹ", "american": "Mỹ", "usa": "Mỹ",
    "duc": "Đức", "đức": "Đức", "germany": "Đức", "german": "Đức",
    "uc": "Úc", "úc": "Úc", "australia": "Úc", "australian": "Úc",
    "anh": "Anh", "uk": "Anh", "england": "Anh", "british": "Anh",
    "han quoc": "Hàn Quốc", "hàn quốc": "Hàn Quốc", "han": "Hàn Quốc", "hàn": "Hàn Quốc", "korea": "Hàn Quốc",
    "viet nam": "Việt Nam", "việt nam": "Việt Nam", "viet": "Việt Nam", "việt": "Việt Nam", "vietnam": "Việt Nam",
    "nhat": "Nhật", "nhật": "Nhật", "nhat ban": "Nhật", "nhật bản": "Nhật", "japan": "Nhật", "japanese": "Nhật",
    "thuy dien": "Thụy Điển", "thụy điển": "Thụy Điển", "sweden": "Thụy Điển",
    "phap": "Pháp", "pháp": "Pháp", "france": "Pháp", "french": "Pháp",
    "bo dao nha": "Bồ Đào Nha", "bồ đào nha": "Bồ Đào Nha", "portugal": "Bồ Đào Nha",
    "czech": "Czech", "séc": "Czech",
    "brazil": "Brazil",
    "dan mach": "Đan Mạch", "đan mạch": "Đan Mạch", "denmark": "Đan Mạch",
    "bi": "Bỉ", "bỉ": "Bỉ", "belgium": "Bỉ",
    "phan lan": "Phần Lan", "phần lan": "Phần Lan", "finland": "Phần Lan",
    "trung quoc": "Trung Quốc", "trung quốc": "Trung Quốc", "trung": "Trung", "china": "Trung Quốc", "chinese": "Trung Quốc",
    "ba lan": "Ba Lan", "poland": "Ba Lan", "polish": "Ba Lan",
    "thuy si": "Thụy Sĩ", "thụy sĩ": "Thụy Sĩ", "switzerland": "Thụy Sĩ",
    "dai loan": "Đài Loan", "đài loan": "Đài Loan", "taiwan": "Đài Loan",
    "ireland": "Ireland",
    "afghanistan": "Afghanistan",
    "na uy": "Na Uy", "norway": "Na Uy",
    "hong kong": "Hong Kong",
    "y": "Ý", "ý": "Ý", "italy": "Ý", "italian": "Ý",
    "thai lan": "Thái Lan", "thái lan": "Thái Lan", "thailand": "Thái Lan",
    "israel": "Israel",
    "trieu tien": "Triều Tiên", "triều tiên": "Triều Tiên", "north korea": "Triều Tiên",
    "ha lan": "Hà Lan", "hà lan": "Hà Lan", "netherlands": "Hà Lan", "dutch": "Hà Lan",
    "hungary": "Hungary",
    "belarus": "Belarus",
    "nga": "Nga", "russia": "Nga", "russian": "Nga",
}

# ── Fetch sheet ───────────────────────────────────────────────────────────────

async def fetch_books() -> list[dict]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(SHEET_CSV_URL)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        return [row for row in reader]

# ── Search logic ──────────────────────────────────────────────────────────────

def search_by_title(books: list[dict], query: str) -> list[dict]:
    q = query.lower().strip()
    return [b for b in books if q in b.get(COL_TITLE, "").lower()]

def search_by_author(books: list[dict], query: str) -> list[dict]:
    tokens = query.lower().strip().split()
    results = []
    for b in books:
        author = b.get(COL_AUTHOR, "").lower()
        if all(tok in author for tok in tokens):
            results.append(b)
    return results

def search_by_genre_country(books: list[dict], query: str) -> list[dict]:
    q = query.lower().strip()

    # Tìm genre
    matched_genre = None
    for g in GENRES:
        if g.lower() in q:
            matched_genre = g
            break

    # Tìm country (ưu tiên cụm dài)
    matched_country = None
    for phrase in sorted(COUNTRY_SYNONYMS, key=len, reverse=True):
        if phrase in q:
            matched_country = COUNTRY_SYNONYMS[phrase]
            break

    if not matched_genre and not matched_country:
        return []

    results = []
    for b in books:
        genre_match   = (not matched_genre)   or (b.get(COL_GENRE, "") == matched_genre)
        country_val   = b.get(COL_COUNTRY, "")
        # Handle "Trung" vs "Trung Quốc" in sheet
        country_match = (not matched_country) or (matched_country in country_val) or (country_val in matched_country)
        if genre_match and country_match:
            results.append(b)
    return results

def detect_mode(query: str) -> str:
    """Đoán user đang tìm theo mode nào."""
    q = query.lower().strip()
    words = q.split()

    # Có genre keyword → genre/country mode
    for g in GENRES:
        if g.lower() in q:
            return "genre_country"

    # Có country keyword → genre/country mode
    for phrase in sorted(COUNTRY_SYNONYMS, key=len, reverse=True):
        if phrase in q:
            return "genre_country"

    # ≥3 từ → title mode
    if len(words) >= 3:
        return "title"

    # 1-2 từ → author mode
    return "author"

# ── Format ────────────────────────────────────────────────────────────────────

def format_book(i: int, b: dict) -> str:
    entry = (
        f"{i}. 📖 *{b.get(COL_TITLE,'?')}*\n"
        f"    👤 {b.get(COL_AUTHOR,'?')}  ·  🌍 {b.get(COL_COUNTRY,'')}  ·  🏷️ {b.get(COL_GENRE,'')}"
    )
    if b.get(COL_LINK):
        entry += f"\n    🔗 {b[COL_LINK]}"
    return entry

def format_page(results: list[dict], offset: int, query: str) -> str:
    page = results[offset:offset + PAGE_SIZE]
    total = len(results)
    lines = [format_book(offset + i + 1, b) for i, b in enumerate(page)]
    text = "\n\n".join(lines)

    remaining = total - (offset + PAGE_SIZE)
    if remaining > 0:
        text += f"\n\n_Còn {remaining} cuốn nữa, gõ_ *ok* _để xem tiếp._"
    else:
        text += f"\n\n_Đã hiện hết {total} cuốn._"
    return text

# ── Glossary ──────────────────────────────────────────────────────────────────

async def glossary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        books = await fetch_books()
    except Exception:
        await update.message.reply_text("❌ Không tải được dữ liệu.")
        return

    top_authors = Counter(b.get(COL_AUTHOR, "") for b in books if b.get(COL_AUTHOR)).most_common(5)

    genre_text   = "  ".join(f"`{g}`" for g in GENRES)
    authors_text = "\n".join(f"  {i+1}. {a} ({c} cuốn)" for i, (a, c) in enumerate(top_authors))

    msg = (
        "📋 *GLOSSARY*\n\n"
        f"🏷️ *Thể loại:*\n{genre_text}\n\n"
        f"👤 *Top 5 tác giả:*\n{authors_text}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("📋 Xem Glossary", callback_data="glossary"),
            InlineKeyboardButton("🔍 Tìm sách", callback_data="search_help"),
        ]
    ]
    await update.message.reply_text(
        "📚 *Chào mừng đến bot tìm sách của xuxu!*\n\nBạn muốn làm gì?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "glossary":
        try:
            books = await fetch_books()
        except Exception:
            await query.message.reply_text("❌ Không tải được dữ liệu.")
            return
        top_authors = Counter(b.get(COL_AUTHOR, "") for b in books if b.get(COL_AUTHOR)).most_common(5)
        genre_text   = "  ".join(f"`{g}`" for g in GENRES)
        authors_text = "\n".join(f"  {i+1}. {a} ({c} cuốn)" for i, (a, c) in enumerate(top_authors))
        msg = (
            "📋 *GLOSSARY*\n\n"
            f"🏷️ *Thể loại:*\n{genre_text}\n\n"
            f"👤 *Top 5 tác giả:*\n{authors_text}"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif query.data == "search_help":
        await query.message.reply_text(
            "🔍 *Cách tìm sách:*\n\n"
            "1️⃣ *Theo tựa* — gõ ít nhất 3 từ trong tựa\n"
            "    VD: `về nhà trước trời tối`\n\n"
            "2️⃣ *Theo thể loại & quốc gia* — kết hợp tự nhiên\n"
            "    VD: `trinh thám Nhật`, `tiểu thuyết Pháp`\n"
            "    _(Tên quốc gia cần chính xác, xem Glossary nếu cần)_\n\n"
            "3️⃣ *Theo tác giả* — họ, tên, hoặc cả hai\n"
            "    VD: `Keigo`, `Higashino`, `Alice Feeney`\n\n"
            "Tìm ra hơn 3 kết quả thì gõ *ok* để xem tiếp nhé!",
            parse_mode="Markdown",
        )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    # Paging
    if text.lower() in ("ok", "có", "co", "tiếp", "tiep", "xem tiếp", "xem tiep"):
        results = context.user_data.get("results")
        offset  = context.user_data.get("offset", 0)
        query   = context.user_data.get("query", "")
        if not results:
            await update.message.reply_text("Hổng có kết quả nào đang chờ. Thử tìm sách trước nhé!")
            return
        await update.message.reply_text(
            format_page(results, offset, query),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        context.user_data["offset"] = offset + PAGE_SIZE
        if offset + PAGE_SIZE >= len(results):
            context.user_data["results"] = None
        return

    # New search — reset state
    context.user_data["results"] = None
    context.user_data["offset"]  = 0
    context.user_data["query"]   = text

    await update.message.reply_chat_action("typing")

    try:
        books = await fetch_books()
    except Exception as e:
        logger.error(f"Sheet fetch error: {e}")
        await update.message.reply_text("❌ Không tải được dữ liệu. Thử lại sau nhé!")
        return

    mode = detect_mode(text)
    if mode == "title":
        results = search_by_title(books, text)
    elif mode == "genre_country":
        results = search_by_genre_country(books, text)
    else:
        results = search_by_author(books, text)

    if not results:
        await update.message.reply_text(
            f"😕 Không tìm thấy sách nào khớp với *\"{text}\"*.\n\nThử cách khác hoặc xem /glossary nhé!",
            parse_mode="Markdown",
        )
        return

    context.user_data["results"] = results
    context.user_data["offset"]  = PAGE_SIZE

    await update.message.reply_text(
        format_page(results, 0, text),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

# ── App setup ─────────────────────────────────────────────────────────────────

from fastapi import FastAPI, Request

app = FastAPI()
tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(CommandHandler("glossary", glossary))
tg_app.add_handler(CallbackQueryHandler(button))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

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
