import os
import secrets
import asyncio
import threading
import json
from flask import Flask, request, jsonify, send_file
from pyrogram import Client, filters
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN   = os.environ.get("BOT_TOKEN")
API_ID      = int(os.environ.get("API_ID", 0))
API_HASH    = os.environ.get("API_HASH", "")
BASE_URL    = os.environ.get("BASE_URL", "").rstrip("/")

flask_app = Flask(__name__)

# ── in-memory stores ──────────────────────────────────────────────────────────
sessions      = {}   # phone -> session_string
user_tokens   = {}   # telegram_user_id -> access_token
user_state    = {}
user_data     = {}
user_clients  = {}
user_code_hash= {}
token_phone   = {}   # access_token -> phone  (for API auth)
token_session = {}   # access_token -> session_string

WAIT_PHONE    = "WAIT_PHONE"
WAIT_CODE     = "WAIT_CODE"
WAIT_PASSWORD = "WAIT_PASSWORD"

bot = None

# ── helpers ───────────────────────────────────────────────────────────────────
def get_token_from_request():
    """Extract token from ?token= query param or Authorization header."""
    token = request.args.get("token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token

def authorized_session(token):
    """Return session_string for token, or None if invalid."""
    return token_session.get(token)

# ── bot handlers ──────────────────────────────────────────────────────────────
async def handle_start(client, message):
    user_id = message.from_user.id
    user_state[user_id] = WAIT_PHONE
    await message.reply(
        "👋 أهلاً بك في TG AutoBlast!\n\n"
        "أرسل رقم هاتفك مع رمز الدولة:\n"
        "مثال: +9647801234567"
    )

async def handle_message(client, message):
    user_id = message.from_user.id
    text    = message.text.strip()
    state   = user_state.get(user_id)

    if state == WAIT_PHONE:
        await handle_phone(message, user_id, text)
    elif state == WAIT_CODE:
        await handle_code(message, user_id, text)
    elif state == WAIT_PASSWORD:
        await handle_password(message, user_id, text)
    else:
        await message.reply("اكتب /start للبدء من جديد.")

async def handle_phone(message, user_id, phone):
    user_data[user_id] = {"phone": phone}
    try:
        pyro = Client(
            name=f"user_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
            device_model="iPhone 14",
            system_version="16.0",
            app_version="9.6.3"
        )
        await pyro.connect()
        sent = await pyro.send_code(phone)
        user_clients[user_id]   = pyro
        user_code_hash[user_id] = sent.phone_code_hash
        user_state[user_id]     = WAIT_CODE
        await message.reply("✅ تم إرسال الرمز إلى تلغرام، أرسله هنا:")
    except Exception as e:
        logger.error(f"handle_phone error: {e}")
        await message.reply(f"❌ خطأ: {e}\nحاول مرة ثانية /start")
        user_state.pop(user_id, None)

async def handle_code(message, user_id, code):
    code       = code.replace(" ", "").replace("-", "")
    phone      = user_data[user_id]["phone"]
    pyro       = user_clients.get(user_id)
    phone_hash = user_code_hash.get(user_id)
    try:
        await pyro.sign_in(phone, phone_hash, code)
        await finish_login(message, user_id, pyro)
    except SessionPasswordNeeded:
        user_state[user_id] = WAIT_PASSWORD
        await message.reply(
            "🔐 حسابك يحتاج كلمة مرور المصادقة الثنائية.\n"
            "أرسلها الآن:"
        )
    except (PhoneCodeInvalid, PhoneCodeExpired):
        await message.reply("❌ الرمز خاطئ أو منتهي. أرسل الرمز مرة ثانية:")
    except Exception as e:
        logger.error(f"handle_code error: {e}")
        await message.reply(f"❌ خطأ: {e}\nأرسل الرمز مرة ثانية:")

async def handle_password(message, user_id, password):
    password = password.replace(" ", "")
    pyro     = user_clients.get(user_id)
    try:
        await pyro.check_password(password)
        await finish_login(message, user_id, pyro)
    except Exception as e:
        await message.reply(f"❌ كلمة المرور خاطئة: {e}\nحاول مرة ثانية:")

async def finish_login(message, user_id, pyro):
    phone       = user_data[user_id]["phone"]
    session_str = await pyro.export_session_string()
    token       = secrets.token_hex(24)

    sessions[phone]      = session_str
    user_tokens[user_id] = token
    token_phone[token]   = phone
    token_session[token] = session_str

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
    logger.info(f"User {user_id} logged in, phone={phone}")

# ── Flask routes ──────────────────────────────────────────────────────────────
@flask_app.route("/")
def index():
    return send_file("index.html")

@flask_app.route("/dashboard")
def dashboard():
    token = request.args.get("token")
    if not token or token not in token_session:
        return "⛔ غير مصرح. ارجع للبوت واحصل على رابط جديد.", 403
    return send_file("index.html")

# ── /api/groups ───────────────────────────────────────────────────────────────
@flask_app.route("/api/groups")
def api_groups():
    token = get_token_from_request()
    session_str = authorized_session(token)
    if not session_str:
        return jsonify({"error": "غير مصرح"}), 401

    try:
        result = asyncio.run(_fetch_groups(session_str))
        return jsonify(result)
    except Exception as e:
        logger.error(f"api_groups error: {e}")
        return jsonify({"error": str(e)}), 500

async def _fetch_groups(session_str):
    groups = []
    async with Client(
        name="fetch_tmp",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_str,
        in_memory=True
    ) as client:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if chat.type.value in ("group", "supergroup", "channel"):
                groups.append({
                    "id":      str(chat.id),
                    "name":    chat.title or "بدون اسم",
                    "members": getattr(chat, "members_count", 0) or 0,
                    "icon":    "📢"
                })
    return groups

# ── /api/stats ────────────────────────────────────────────────────────────────
@flask_app.route("/api/stats")
def api_stats():
    token = get_token_from_request()
    if not authorized_session(token):
        return jsonify({"error": "غير مصرح"}), 401
    # Static demo stats — replace with real DB counters when ready
    return jsonify({
        "success":      1284,
        "failed":       68,
        "rate":         95,
        "queue":        12,
        "uptime":       "٣س ٤٢د",
        "activeGroups": 7
    })

# ── /api/launch ───────────────────────────────────────────────────────────────
@flask_app.route("/api/launch", methods=["POST"])
def api_launch():
    token = get_token_from_request()
    session_str = authorized_session(token)
    if not session_str:
        return jsonify({"error": "غير مصرح"}), 401

    data     = request.json or {}
    groups   = data.get("groups",   [])
    messages = data.get("messages", [])
    schedule = data.get("schedule", {})

    if not groups:
        return jsonify({"error": "لم تختر أي مجموعة"}), 400
    if not messages:
        return jsonify({"error": "لم تكتب أي رسالة"}), 400

    # Launch in background thread
    threading.Thread(
        target=_launch_worker,
        args=(session_str, groups, messages, schedule),
        daemon=True
    ).start()

    return jsonify({"status": "ok", "message": "تم إطلاق الحملة ✅"})

def _launch_worker(session_str, groups, messages, schedule):
    """Runs in a background thread — sends messages to each group."""
    import time
    delay_sec = int(schedule.get("delay", 15)) * 60

    async def _send():
        async with Client(
            name="sender_tmp",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_str,
            in_memory=True
        ) as client:
            msg_idx = 0
            for group_id in groups:
                try:
                    text = messages[msg_idx % len(messages)]
                    await client.send_message(int(group_id), text)
                    logger.info(f"Sent to {group_id} ✅")
                    msg_idx += 1
                    await asyncio.sleep(delay_sec)
                except Exception as e:
                    logger.error(f"Send error to {group_id}: {e}")

    asyncio.run(_send())

# ── run ───────────────────────────────────────────────────────────────────────
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

async def main():
    global bot
    bot = Client(
        name="bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )
    bot.on_message(filters.command("start"))(handle_start)
    bot.on_message(filters.text & ~filters.command("start"))(handle_message)

    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    await bot.start()
    logger.info("✅ البوت شغال")
    await asyncio.get_event_loop().create_future()

if __name__ == "__main__":
    asyncio.run(main())
