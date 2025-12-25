from flask import Flask
import os
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    # Flask تو Thread اجرا میشه
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Bot باید تو Main Thread اجرا بشه (برای Python 3.13)
    from bot import run_bot
    run_bot()
