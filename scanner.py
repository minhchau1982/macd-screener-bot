#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, csv, time, math, requests, pandas as pd
from datetime import datetime, timezone

BINANCE_API = "https://data-api.binance.vision"
TIMEOUT = 15

def _get(url, params=None, max_retries=3, backoff=0.8):
    """GET có retry nhẹ để tránh lỗi tạm (451/5xx)."""
    last_err = None
    for i in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            # vài WAF trả 451/403 -> thử lại
            last_err = requests.HTTPError(f"{r.status_code} {r.text}")
        except Exception as e:
            last_err = e
        time.sleep(backoff * (2**i))
    raise last_err

def get_all_usdt_spot_symbols():
    r = _get(f"{BINANCE_API}/api/v3/exchangeInfo")
    syms = []
    for s in r.json().get("symbols", []):
        if (s.get("status")=="TRADING"
            and s.get("quoteAsset")=="USDT"
            and s.get("isSpotTradingAllowed", False)):
            name = s["symbol"]
            if any(x in name for x in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):
                continue
            syms.append(name)
    return sorted(syms)

def get_klines_weekly(symbol, limit=180):
    r = _get(f"{BINANCE_API}/api/v3/klines",
             params={"symbol":symbol,"interval":"1w","limit":limit})
    arr = r.json()
    if not arr: return pd.DataFrame()
    df = pd.DataFrame(arr, columns=[
        "openTime","open","high","low","close","volume","closeTime",
        "quoteAssetVolume","numTrades","tbb","tbq","ignore"
    ])
    # ép kiểu
    for c in ["open","high","low","close","volume","quoteAssetVolume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def ema(s, span): return s.ewm(span=span, adjust=False).mean()
def macd(close, fast=12, slow=26, signal=9):
    m  = ema(close, fast) - ema(close, slow)
    sg = ema(m, signal)
    return m, sg, m - sg

def crossed_up(a1_prev, a1_now, a2_prev, a2_now):
    return a1_prev <= a2_prev and a1_now > a2_now

def screen_one(df, min_qv, min_price):
    if len(df) < 35: return None
    last_close = float(df["close"].iloc[-1])
    avg_qv6 = float(df["quoteAssetVolume"].tail(6).mean())
    if math.isnan(avg_qv6) or avg_qv6 < min_qv or last_close < min_price:
        return None
    m, s, h = macd(df["close"])
    m_prev, m_now = float(m.iloc[-2]), float(m.iloc[-1])
    s_prev, s_now = float(s.iloc[-2]), float(s.iloc[-1])
    h_prev, h_now = float(h.iloc[-2]), float(h.iloc[-1])

    cut_up = crossed_up(m_prev, m_now, s_prev, s_now)     # MACD cắt lên
    on_top_small = (m_now > s_now) and (m_prev <= s_prev or abs(m_now-s_now) < max(1e-9, 0.15*abs(s_now)))
    # logic lọc: tín hiệu vừa cắt lên HOẶC nằm trên signal sát nhau,
    # đồng thời MACD vẫn < 0 (điểm sớm) và histogram > 0
    if (cut_up or on_top_small) and (m_now < 0) and (h_now > 0):
        score = (1 if cut_up else 0) + (0.5 if on_top_small else 0) + (0.7 if h_now > h_prev else 0) \
                + min(1.0, avg_qv6/(10*min_qv))
        return {
            "close": round(last_close, 8),
            "avg_qv_6w": round(avg_qv6, 2),
            "macd": round(m_now, 6),
            "signal": round(s_now, 6),
            "hist": round(h_now, 6),
            "score": round(score, 3),
        }
    return None

def send_telegram_document(file_path, caption):
    token = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat  = os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat:
        print("Telegram: thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
        return
    with open(file_path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                          data={"chat_id":chat,"caption":caption},
                          files={"document":(os.path.basename(file_path), f, "text/csv")},
                          timeout=20)
        print("Telegram:", r.status_code, r.text[:200])

def send_telegram_text(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat  = os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat: return
    requests.get(f"https://api.telegram.org/bot{token}/sendMessage",
                 params={"chat_id":chat,"text":text}, timeout=15)

def run_scan(min_vol=300000.0, min_price=0.005, limit=180, out_path="scan_results.csv"):
    print(">> Loading USDT spot symbols ...")
    syms = get_all_usdt_spot_symbols()
    print(f">> Found {len(syms)} symbols.")
    rows = []
    for i, sym in enumerate(syms, 1):
        try:
            df = get_klines_weekly(sym, limit=limit)
            if df.empty: continue
            rec = screen_one(df, min_vol, min_price)
            if rec: rows.append({"symbol":sym, **rec})
        except Exception as e:
            # bỏ qua lỗi lẻ
            print("skip", sym, "->", e)
        if i % 30 == 0:
            print(f"scanned {i}/{len(syms)}")

    rows.sort(key=lambda x: (x["score"], x["avg_qv_6w"]), reverse=True)
    utc_now = datetime.now(timezone.utc)
    if rows:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["symbol","close","avg_qv_6w","macd","signal","hist","score"])
            w.writeheader(); w.writerows(rows)
        caption = f"✅ Binance MACD 1W Screener\nSymbols: {len(rows)}\nUTC: {utc_now:%Y-%m-%d %H:%M}"
        send_telegram_document(out_path, caption)
    else:
        send_telegram_text(f"⛔ Không có coin đạt tiêu chí hôm nay (UTC {utc_now:%Y-%m-%d}).")

    return {"count": len(rows), "saved": bool(rows), "utc": utc_now.isoformat()}

if __name__ == "__main__":
    info = run_scan()
    print(info)
