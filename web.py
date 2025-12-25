from flask import Flask
import os
import threading
import nest_asyncio

nest_asyncio.apply()

app = Flask(__name__)

@app.get("/")
def home():
    return "Bot is running ✅"

def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # Keep a web port open for Render Web Service
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    # Bot must run in main thread
    from bot import run_bot
    run_bot()
