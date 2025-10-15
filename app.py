from flask import Flask, jsonify
import subprocess, os, datetime

def run_scan():
    cmd = ["python", "scanner.py", "--min-vol", "500000", "--min-price", "0.01", "--limit", "180"]
    subprocess.run(cmd, check=False)

app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

@app.route("/run")
def run():
    run_scan()
    return jsonify({
        "status": "ok",
        "ran_at_utc": datetime.datetime.utcnow().isoformat() + "Z"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
