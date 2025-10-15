#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, csv, math, os, requests, pandas as pd
from datetime import datetime

BINANCE_API = "https://api.binance.com"

def get_all_usdt_spot_symbols():
    r = requests.get(f"{BINANCE_API}/api/v3/exchangeInfo", timeout=20)
    r.raise_for_status()
    syms = []
    for s in r.json()["symbols"]:
        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("isSpotTradingAllowed", False)
        ):
            name = s["symbol"]
            if any(x in name for x in ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"]):
                continue
            syms.append(name)
    return sorted(syms)


def get_klines_weekly(symbol, limit=180):
    r = requests.get(
        f"{BINANCE_API}/api/v3/klines",
        params={"symbol": symbol, "interval": "1w", "limit": limit},
        timeout=30,
    )
    r.raise_for_status()
    arr = r.json()
    if not arr:
        return pd.DataFrame()
    df = pd.DataFrame(
        arr,
        columns=[
            "openTime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "closeTime",
            "quoteAssetVolume",
            "nt",
            "tbBase",
            "tbQuote",
            "ignore",
        ],
    )
    df["openTime"] = pd.to_datetime(df["openTime"], unit="ms", utc=True)
    df["closeTime"] = pd.to_datetime(df["closeTime"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "quoteAssetVolume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[
        ["openTime", "closeTime", "open", "high", "low", "close", "volume", "quoteAssetVolume"]
    ]


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def macd(close, fast=12, slow=26, signal=9):
    m = ema(close, fast) - ema(close, slow)
    sg = ema(m, signal)
    return m, sg, m - sg


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
    on_top_small = (m_now > s_now) and (
        m_prev <= s_prev or abs(m_now - s_now) < max(1e-9, 0.15 * abs(s_now))
    )
    if (cut_up or on_top_small) and (m_now < 0) and (h_now > 0):
        score = (
            (1 if cut_up else 0)
            + (0.5 if on_top_small else 0)
            + (0.7 if h_now > h_prev else 0)
            + min(1.0, avg_qv6 / (10 * min_qv))
        )
        return {
            "close": round(last_close, 8),
            "avg_qv_6w": round(avg_qv6, 2),
            "macd": round(m_now, 6),
            "signal": round(s_now, 6),
            "hist": round(h_now, 6),
            "score": round(score, 3),
        }
    return None


def send_telegram(file_path, caption=""):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("⚠️ Chưa có TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
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
        print("✅ Đã gửi file CSV qua Telegram.")
    except Exception as e:
        print("Telegram error:", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-vol", type=float, default=300000.0)
    ap.add_argument("--min-price", type=float, default=0.005)
    ap.add_argument("--limit", type=int, default=180)
    ap.add_argument("--out", type=str, default="scan_results.csv")
    args = ap.parse_args()

    print("🚀 Đang tải danh sách symbol...")
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
            print(f"Lỗi khi xử lý {sym}: {e}")
        if i % 20 == 0:
            print(f"Đã quét {i}/{len(syms)} symbol...")

    results.sort(key=lambda x: (x["score"], x["avg_qv_6w"]), reverse=True)

    if results:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["symbol", "close", "avg_qv_6w", "macd", "signal", "hist", "score"],
            )
            w.writeheader()
            w.writerows(results)
        print(f"✅ Lưu {args.out} ({len(results)} dòng).")
        send_telegram(args.out, f"✅ Binance MACD 1W Screener\nSymbols: {len(results)}\nUTC: {datetime.utcnow():%Y-%m-%d %H:%M}")
    else:
        print("❌ Không có coin đạt tiêu chí hôm nay.")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and chat:
            requests.get(
                f"https://api.telegram.org/bot{token}/sendMessage",
                params={
                    "chat_id": chat,
                    "text": f"⛔ Không có coin đạt tiêu chí hôm nay (UTC {datetime.utcnow():%Y-%m-%d}).",
                },
                timeout=15,
            )


if __name__ == "__main__":
    main()
