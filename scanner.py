#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, csv, math, os, time, random, requests, pandas as pd
from datetime import datetime, timezone

# ===== Binance public endpoints (không cần API key) =====
BINANCE_ENDPOINTS = [
    "https://api.binance.com",          # mặc định
    "https://api-gcp.binance.com",      # cụm GCP
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    # data-api chỉ có market data public
    "https://data-api.binance.vision"
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; macd-screener/1.0)",
    "Accept": "application/json",
    "Connection": "close",
})

def _sleep_backoff(attempt: int):
    # backoff + jitter
    time.sleep(min(2 ** attempt + random.random(), 8.0))

def binance_get(path: str, params=None, timeout=15):
    """
    Gọi GET với fallback qua nhiều endpoint.
    Tự động đổi endpoint khi gặp 451/403/429/5xx hoặc timeout.
    """
    params = params or {}
    last_err = None
    # xoay vòng danh sách endpoint, shuffle nhẹ để tránh dính 1 cụm
    endpoints = BINANCE_ENDPOINTS[:]
    random.shuffle(endpoints)
    for attempt in range(len(endpoints)):
        base = endpoints[attempt]
        url = f"{base}{path}"
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            # nếu bị 451/403/429 hoặc 5xx -> thử endpoint khác
            if r.status_code in (451, 403, 429) or 500 <= r.status_code < 600:
                last_err = requests.HTTPError(f"{r.status_code} {url}")
                _sleep_backoff(attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_err = e
            _sleep_backoff(attempt)
            continue
    # hết endpoint vẫn fail
    if last_err:
        raise last_err
    raise RuntimeError("Unexpected request flow")

# ====== Data helpers ======
def get_all_usdt_spot_symbols():
    data = binance_get("/api/v3/exchangeInfo")
    syms = []
    for s in data.get("symbols", []):
        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s.get("isSpotTradingAllowed", False):
            name = s["symbol"]
            if any(x in name for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
                continue
            syms.append(name)
    return sorted(syms)

def get_klines_weekly(symbol, limit=180):
    arr = binance_get("/api/v3/klines", params={"symbol": symbol, "interval": "1w", "limit": limit})
    if not arr:
        return pd.DataFrame()
    df = pd.DataFrame(arr, columns=[
        "openTime","open","high","low","close","volume","closeTime",
        "quoteAssetVolume","nt","tbBase","tbQuote","ignore"
    ])
    df["openTime"]  = pd.to_datetime(df["openTime"],  unit="ms", utc=True)
    df["closeTime"] = pd.to_datetime(df["closeTime"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume","quoteAssetVolume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["openTime","closeTime","open","high","low","close","volume","quoteAssetVolume"]]

def ema(s, span): return s.ewm(span=span, adjust=False).mean()
def macd(close, fast=12, slow=26, signal=9):
    m = ema(close, fast) - ema(close, slow)
    s = ema(m, signal)
    return m, s, m - s

def crossed_up(a1_prev, a1_now, a2_prev, a2_now):
    return a1_prev <= a2_prev and a1_now > a2_now

def screen(df, min_qv, min_price):
    if len(df) < 35:
        return None
    last_close = float(df["close"].iloc[-1])
    avg_qv6 = float(df["quoteAssetVolume"].tail(6).mean())
    if math.isnan(avg_qv6) or avg_qv6 < min_qv or last_close < min_price:
        return None
    m, s, h = macd(df["close"])
    m_prev, m_now = float(m.iloc[-2]), float(m.iloc[-1])
    s_prev, s_now = float(s.iloc[-2]), float(s.iloc[-1])
    h_prev, h_now = float(h.iloc[-2]), float(h.iloc[-1])

    cut_up = crossed_up(m_prev, m_now, s_prev, s_now)
    on_top_small = (m_now > s_now) and (m_prev <= s_prev or abs(m_now - s_now) < max(1e-9, 0.15*abs(s_now)))
    if (cut_up or on_top_small) and (m_now < 0) and (h_now > 0):
        score = (1 if cut_up else 0) + (0.5 if on_top_small else 0) + (0.7 if h_now > h_prev else 0) + min(1.0, avg_qv6/(10*min_qv))
        return {
            "close": round(last_close, 8),
            "avg_qv_6w": round(avg_qv6, 2),
            "macd": round(m_now, 6),
            "signal": round(s_now, 6),
            "hist": round(h_now, 6),
            "score": round(score, 3),
        }
    return None

# ====== Telegram ======
def send_telegram_file(file_path, caption=""):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("Telegram skipped: missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                files={"document": (os.path.basename(file_path), f, "text/csv")},
                timeout=30,
            )
            r.raise_for_status()
        print("Telegram: sent.")
    except Exception as e:
        print("Telegram error:", e)

def send_telegram_text(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/sendMessage",
            params={"chat_id": chat, "text": msg},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        print("Telegram error:", e)

# ====== Main ======
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-vol", type=float, default=300000.0)
    ap.add_argument("--min-price", type=float, default=0.005)
    ap.add_argument("--limit", type=int, default=180)
    ap.add_argument("--out", type=str, default="scan_results.csv")
    args = ap.parse_args()

    print("Loading symbols…")
    syms = get_all_usdt_spot_symbols()

    results = []
    for i, sym in enumerate(syms, 1):
        try:
            df = get_klines_weekly(sym, args.limit)
            if df.empty:
                continue
            rec = screen(df, args.min_vol, args.min_price)
            if rec:
                results.append({"symbol": sym, **rec})
        except Exception as e:
            # log ngắn gọn, tránh spam
            print(f"[{sym}] error: {e}")
        if i % 25 == 0:
            print(f"Scanned {i}/{len(syms)}")

    results.sort(key=lambda x: (x["score"], x["avg_qv_6w"]), reverse=True)
    if results:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["symbol", "close", "avg_qv_6w", "macd", "signal", "hist", "score"])
            w.writeheader()
            w.writerows(results)
        print(f"Saved {args.out} with {len(results)} rows.")
        send_telegram_file(
            args.out,
            f"✅ Binance MACD 1W Screener\nSymbols: {len(results)}\nUTC: {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        )
    else:
        print("No matches today.")
        send_telegram_text(f"⛔ Không có coin đạt tiêu chí hôm nay (UTC {datetime.now(timezone.utc):%Y-%m-%d}).")

if __name__ == "__main__":
    main()
