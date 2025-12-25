import os
import threading
import asyncio
from flask import Flask, request

from telegram import Update
from telegram.ext import Application

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL") or "https://ni6488295720w.onrender.com"
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH") or "/tg-webhook"
WEBHOOK_URL = PUBLIC_BASE_URL.rstrip("/") + WEBHOOK_PATH

app = Flask(__name__)

bot_app: Application | None = None
bot_loop: asyncio.AbstractEventLoop | None = None


@app.get("/")
def home():
    return "Bot is running"


@app.post(WEBHOOK_PATH)
def telegram_webhook():
    global bot_app, bot_loop
    if bot_app is None or bot_loop is None:
        return "Bot not ready", 503

    data = request.get_json(force=True, silent=True) or {}

    try:
        update = Update.de_json(data, bot_app.bot)

        # ✅ DEBUG: بفهمیم چی داره میاد
        if update.callback_query:
            print("📩 INCOMING: callback_query", "data=", update.callback_query.data)
        elif update.message:
            print("📩 INCOMING: message", "text=", getattr(update.message, "text", None))
        else:
            print("📩 INCOMING: other update type")

        # ✅ مهم‌ترین تغییر: به جای process_update، بنداز داخل صف خود PTB
        bot_loop.call_soon_threadsafe(bot_app.update_queue.put_nowait, update)

        return "ok", 200

    except Exception as e:
        print("❌ WEBHOOK ERROR:", repr(e))
        return f"error: {e}", 500


def start_bot_background():
    global bot_app, bot_loop
    try:
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)

        from bot import build_application
        bot_app = build_application()

        async def boot():
            await bot_app.initialize()
            await bot_app.start()

            await bot_app.bot.delete_webhook(drop_pending_updates=True)
            ok = await bot_app.bot.set_webhook(
                url=WEBHOOK_URL,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            wh = await bot_app.bot.get_webhook_info()

            print("🌐 PUBLIC_BASE_URL =", PUBLIC_BASE_URL)
            print("🌐 WEBHOOK_URL     =", WEBHOOK_URL)
            print("✅ setWebhook:", ok)
            print("✅ webhook info url:", wh.url)
            print("✅ webhook allowed_updates:", wh.allowed_updates)

        bot_loop.run_until_complete(boot())
        bot_loop.run_forever()

    except Exception as e:
        print("❌ BOT THREAD FAILED:", repr(e))


if __name__ == "__main__":
    t = threading.Thread(target=start_bot_background, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
