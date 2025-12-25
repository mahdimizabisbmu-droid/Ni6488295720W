import os
import threading
import asyncio
from flask import Flask, request

from telegram import Update
from telegram.ext import Application

# -----------------------------
# Config: Public URL of your Render service
# -----------------------------
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL") or "https://djjdkeoeososksms.onrender.com"
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH") or "/tg-webhook"
WEBHOOK_URL = PUBLIC_BASE_URL.rstrip("/") + WEBHOOK_PATH

app = Flask(__name__)

# These will be set on startup
bot_app: Application = None
bot_loop: asyncio.AbstractEventLoop = None


@app.get("/")
def home():
    return "Bot is running"


@app.post(WEBHOOK_PATH)
def telegram_webhook():
    """
    Telegram sends updates here.
    We push the update into PTB Application running on its own asyncio loop.
    """
    global bot_app, bot_loop
    if bot_app is None or bot_loop is None:
        return "Bot not ready", 503

    data = request.get_json(force=True, silent=True) or {}
    try:
        update = Update.de_json(data, bot_app.bot)
        fut = asyncio.run_coroutine_threadsafe(bot_app.process_update(update), bot_loop)
        # We don't block; just ensure it was scheduled
        _ = fut
    except Exception as e:
        return f"error: {e}", 500

    return "ok", 200


def start_bot_background():
    """
    Runs python-telegram-bot Application in a dedicated asyncio loop thread.
    """
    global bot_app, bot_loop

    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)

    from bot import build_application  # your handlers are in bot.py
    bot_app = build_application()

    async def _boot():
        # Initialize PTB
        await bot_app.initialize()
        await bot_app.start()

        # IMPORTANT: switch to webhook mode
        await bot_app.bot.delete_webhook(drop_pending_updates=True)
        await bot_app.bot.set_webhook(url=WEBHOOK_URL)

        print("✅ Webhook set to:", WEBHOOK_URL)

    bot_loop.run_until_complete(_boot())
    bot_loop.run_forever()


if __name__ == "__main__":
    # Start PTB bot loop in background thread
    t = threading.Thread(target=start_bot_background, daemon=True)
    t.start()

    # Start Flask (Render health check + webhook endpoint)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
