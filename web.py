from flask import Flask
import os
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

def run_telegram():
    # اجرای ربات در thread جدا
    from bot import run_bot
    run_bot()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    bot_thread = threading.Thread(target=run_telegram, daemon=True)
    bot_thread.start()

    # زنده نگه داشتن پروسه اصلی
    bot_thread.join()
