from flask import Flask, jsonify
from datetime import datetime
import subprocess

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ MACD Screener Bot is running!"

@app.route('/run')
def run():
    try:
        # chạy scanner.py
        subprocess.run(["python3", "scanner.py"], check=True)
        return jsonify({
            "status": "ok",
            "ran_at_utc": datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
