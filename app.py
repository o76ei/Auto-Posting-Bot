import os
import secrets
import asyncio
import threading
from flask import Flask, request, jsonify, send_file
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from pyrogram import Client as PyroClient
from pyrogram.errors import SessionPasswordNeeded
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

# كل يوزر عنده loop خاص فيه
user_loops = {}
user_clients = {}
user_code_hash = {}

def get_user_loop(user_id):
    if user_id not in user_loops:
        loop = asyncio.new_event_loop()
        user_loops[user_id] = loop
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
    return user_loops[user_id]

def run_in_user_loop(user_id, coro):
    loop = get_user_loop(user_id)
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=30)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً! أرسل رقم هاتفك مع رمز الدولة\nمثال: +9647801234567"
    )
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    context.user_data["phone"] = phone

    async def do_connect():
        client = PyroClient(
            name=f"u_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        await client.connect()
        sent = await client.send_code(phone)
        user_clients[user_id] = client
        user_code_hash[user_id] = sent.phone_code_hash

    try:
        run_in_user_loop(user_id, do_connect())
        await update.message.reply_text("✅ وصلك رمز على تلغرام، أرسله هنا:")
        return CODE
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}\nحاول مرة ثانية /start")
        return ConversationHandler.END

async def get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().replace(" ", "")
    phone = context.user_data["phone"]
    user_id = update.effective_user.id
    client = user_clients.get(user_id)
    phone_hash = user_code_hash.get(user_id)

    async def do_signin():
        await client.sign_in(phone, phone_hash, code)

    try:
        run_in_user_loop(user_id, do_signin())
        return await finish_login(update, context, client, user_id)
    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 حسابك عنده مصادقة ثنائية!\n\n"
            "أرسل كلمة المرور بشكل مفرق\n"
            "مثال: h e l l o 1 2 3"
        )
        return PASSWORD
    except Exception as e:
        await update.message.reply_text(f"❌ الرمز خاطئ: {e}\nأرسل مرة ثانية:")
        return CODE

async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip().replace(" ", "")
    user_id = update.effective_user.id
    client = user_clients.get(user_id)

    async def do_password():
        await client.check_password(password)

    try:
        run_in_user_loop(user_id, do_password())
        return await finish_login(update, context, client, user_id)
    except Exception:
        await update.message.reply_text("❌ كلمة المرور خاطئة\nحاول مرة ثانية:")
        return PASSWORD

async def finish_login(update, context, client, user_id):
    phone = context.user_data["phone"]

    async def do_export():
        return await client.export_session_string()

    session_str = run_in_user_loop(user_id, do_export())
    sessions[phone] = session_str
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

@app.route("/api/send", methods=["POST"])
def send_messages():
    data = request.json
    phone = data.get("phone")
    if not sessions.get(phone):
        return jsonify({"error": "لا توجد جلسة"}), 401
    return jsonify({"status": "ok"})

def run_bot():
    async def start_bot():
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
        await application.run_polling()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_bot())

if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
