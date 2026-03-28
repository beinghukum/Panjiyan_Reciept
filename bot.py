"""
Kisan Receipt Downloader Bot
Downloads receipts from mpeuparjan.mp.gov.in via Telegram
"""

import asyncio
import logging
import os
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler, filters
)

from scraper import KisanScraper, CaptchaError

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
SELECT_DISTRICT, ENTER_KISAN_CODE, ENTER_CAPTCHA = range(3)

# ── MP Districts list ──────────────────────────────────────────────────────────
DISTRICTS = [
    "अगर मालवा", "अलीराजपुर", "अनूपपुर", "अशोकनगर", "बालाघाट",
    "बड़वानी", "बैतूल", "भिंड", "भोपाल", "बुरहानपुर",
    "छतरपुर", "छिंदवाड़ा", "दमोह", "दतिया", "देवास",
    "धार", "डिंडोरी", "गुना", "ग्वालियर", "हरदा",
    "होशंगाबाद", "इंदौर", "जबलपुर", "झाबुआ", "कटनी",
    "खंडवा", "खरगोन", "मंडला", "मंदसौर", "मुरैना",
    "नरसिंहपुर", "नीमच", "निवाड़ी", "पन्ना", "रायसेन",
    "राजगढ़", "रतलाम", "रीवा", "सागर", "सतना",
    "सीहोर", "सिवनी", "शहडोल", "शाजापुर", "श्योपुर",
    "शिवपुरी", "सीधी", "सिंगरौली", "टीकमगढ़", "उज्जैन",
    "उमरिया", "विदिशा"
]

BOT_TOKEN = "8756937178:AAHrVGNMhetcZuqPsv3QstlsTr7qPoeslyA"
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set!")


# ── Helpers ────────────────────────────────────────────────────────────────────

def district_keyboard(page: int = 0, page_size: int = 12):
    """Build inline keyboard for district selection with pagination."""
    start = page * page_size
    end = min(start + page_size, len(DISTRICTS))
    chunk = DISTRICTS[start:end]

    buttons = []
    row = []
    for i, d in enumerate(chunk):
        row.append(InlineKeyboardButton(d, callback_data=f"district:{d}"))
        if (i + 1) % 3 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Pagination row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ पिछला", callback_data=f"page:{page-1}"))
    if end < len(DISTRICTS):
        nav.append(InlineKeyboardButton("अगला ▶️", callback_data=f"page:{page+1}"))
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)


# ── Handlers ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "🌾 *किसान रसीद डाउनलोडर*\n\n"
        "MP e-Uparjan पोर्टल से आवेदन पर्ची डाउनलोड करने के लिए\n"
        "पहले अपना *जिला चुनें* 👇",
        parse_mode="Markdown",
        reply_markup=district_keyboard(0),
    )
    return SELECT_DISTRICT


async def select_district_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("page:"):
        page = int(data.split(":")[1])
        await query.edit_message_reply_markup(reply_markup=district_keyboard(page))
        return SELECT_DISTRICT

    if data.startswith("district:"):
        district = data.split(":", 1)[1]
        context.user_data["district"] = district
        await query.edit_message_text(
            f"✅ जिला चुना: *{district}*\n\n"
            "अब अपना *किसान कोड / मोबाइल नं. / समग्र नं.* भेजें:",
            parse_mode="Markdown",
        )
        return ENTER_KISAN_CODE

    return SELECT_DISTRICT


async def receive_kisan_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kisan_code = update.message.text.strip()
    context.user_data["kisan_code"] = kisan_code

    msg = await update.message.reply_text("⏳ पोर्टल खोल रहे हैं और CAPTCHA लोड हो रहा है...")

    scraper: KisanScraper = context.bot_data.get("scraper")
    if scraper is None:
        scraper = KisanScraper()
        context.bot_data["scraper"] = scraper

    user_id = update.effective_user.id
    district = context.user_data["district"]

    try:
        captcha_image_bytes = await scraper.load_form_and_get_captcha(
            user_id, district, kisan_code
        )
    except Exception as e:
        logger.error("Error loading form: %s", e)
        await msg.edit_text(f"❌ पोर्टल लोड करने में त्रुटि:\n`{e}`\n\n/start से दोबारा कोशिश करें।", parse_mode="Markdown")
        return ConversationHandler.END

    await msg.delete()

    # Send captcha image
    await update.message.reply_photo(
        photo=captcha_image_bytes,
        caption=(
            "🔐 ऊपर दिखाई दे रहा *CAPTCHA* टाइप करें और भेजें:\n"
            "_(तस्वीर में लिखे शब्द/अंक)_"
        ),
        parse_mode="Markdown",
    )
    return ENTER_CAPTCHA


async def receive_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    captcha_text = update.message.text.strip()
    user_id = update.effective_user.id

    msg = await update.message.reply_text("🔍 किसान खोजा जा रहा है...")

    scraper: KisanScraper = context.bot_data["scraper"]

    try:
        pdf_bytes = await scraper.submit_and_get_receipt_pdf(user_id, captcha_text)
    except CaptchaError:
        # CAPTCHA was wrong — refresh and ask again without ending the conversation
        logger.info("CAPTCHA wrong for user %s, refreshing", user_id)
        try:
            captcha_image_bytes = await scraper.refresh_captcha(user_id)
            await msg.delete()
            await update.message.reply_photo(
                photo=captcha_image_bytes,
                caption=(
                    "❌ CAPTCHA गलत था!\n\n"
                    "🔐 नया CAPTCHA देखें और फिर टाइप करके भेजें:"
                ),
            )
            return ENTER_CAPTCHA
        except Exception as e2:
            logger.error("Could not refresh captcha: %s", e2)
            await msg.edit_text("❌ CAPTCHA गलत था और नया लोड नहीं हुआ। /start से दोबारा कोशिश करें।")
            await scraper.close_session(user_id)
            return ConversationHandler.END
    except Exception as e:
        logger.error("Scraper error: %s", e)
        await msg.edit_text(
            f"❌ त्रुटि हुई:\n\n{e}\n",
            parse_mode=None,
        )
        await scraper.close_session(user_id)
        return ConversationHandler.END

    await msg.delete()
    kisan_code = context.user_data.get("kisan_code", "farmer")
    filename = f"receipt_{kisan_code}.pdf"

    await update.message.reply_document(
        document=pdf_bytes,
        filename=filename,
        caption=(
            "✅ *आवेदन पर्ची तैयार है!*\n"
            f"किसान कोड: `{kisan_code}`\n"
            f"जिला: {context.user_data.get('district', '')}\n\n"
            "दोबारा डाउनलोड करने के लिए /start करें।"
        ),
        parse_mode="Markdown",
    )

    await scraper.close_session(user_id)
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ रद्द किया गया। /start से दोबारा शुरू करें।")
    user_id = update.effective_user.id
    scraper: KisanScraper = context.bot_data.get("scraper")
    if scraper:
        await scraper.close_session(user_id)
    context.user_data.clear()
    return ConversationHandler.END


# ── Main ───────────────────────────────────────────────────────────────────────

# ── Health check server (keeps Render service alive) ──────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Kisan Bot Running")
    def log_message(self, *args):
        pass  # suppress noisy HTTP logs


def _start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("Health server running on port %d", port)
    server.serve_forever()


def main():
    # Start health check HTTP server in background thread (required for Render)
    threading.Thread(target=_start_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_DISTRICT: [
                CallbackQueryHandler(select_district_callback),
            ],
            ENTER_KISAN_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_kisan_code),
            ],
            ENTER_CAPTCHA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_captcha),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
