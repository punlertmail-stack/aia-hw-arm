"""อัปเดตข้อมูลบนหน้าเว็บเป็นราคาปิดล่าสุด (และงบล่าสุดเมื่อสั่ง) — รันใน GitHub Actions ทุกวันทำการ หรือรันในเครื่องก็ได้

  python screener/refresh.py               ราคาปิดล่าสุดของทุกหุ้นใน output/data/yahoo.js และ setsmart.js
  python screener/refresh.py --statements  ดึงงบ ปันผล และราคาย้อนหลังจาก Yahoo ใหม่ทั้งหมด แล้วสร้าง yahoo.js ใหม่
                                           (setsmart.js อัปเดตเฉพาะราคา เพราะไฟล์ SETSMART อยู่ในเครื่องผู้เรียนเท่านั้น)

ราคาปิดล่าสุด = วันทำการล่าสุดที่มีข้อมูล · ก่อน 17:00 น. ใช้วันก่อนหน้า (เหมือน data/fetch.py ของ workspace)
ตัวชี้วัดที่ขึ้นกับราคา (P/E, P/BV, yield ฯลฯ) คำนวณใหม่จากค่าคงที่ k/kc ที่ build.py เก็บไว้ ตัวอื่นไม่เปลี่ยน
ต้องมี yfinance (GitHub Actions ติดตั้งให้เอง: pip install yfinance==1.7.0)
"""
import argparse
import json
import math
import os
import sys
import time
import warnings
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import metrics  # noqa: E402
import read_yahoo  # noqa: E402

ICT = timezone(timedelta(hours=7))
CUTOFF_HOUR = 17            # ก่อนเวลานี้ตลาดยังไม่ปิด/ข้อมูลยังไม่ครบ → ใช้วันทำการก่อนหน้า
REF = "PTT.BK"              # ใช้หาวันทำการล่าสุดที่มีข้อมูลจริง
CHUNK = 100                 # จำนวนหุ้นต่อการดาวน์โหลดราคา 1 ครั้ง
MIN_COVERAGE = 0.8          # ได้ราคาไม่ถึง 80% ของหุ้น → ไม่เขียนไฟล์ (น่าจะโดน Yahoo จำกัด)
MAX_CONSEC_FAIL = 25        # ดึงงบไม่ได้ติดกันเกินนี้ → หยุด ไม่เขียนไฟล์
RAW_DIR = build.ROOT / ".cache" / "yahoo_raw"   # ไฟล์ดิบชั่วคราวตอนดึงงบ (ไม่ขึ้น git)
INFO_FIELDS = ["longName", "sector", "currency", "financialCurrency", "sharesOutstanding"]


def yf():
    import yfinance  # นำเข้าตอนใช้ ให้ test และ build รันได้แม้ไม่มี yfinance
    warnings.filterwarnings("ignore", module="yfinance")   # คำเตือนเลิกใช้ของ pandas ภายใน yfinance ไม่กระทบผล
    return yfinance


def now_ict():
    return datetime.now(ICT)


def target_day():
    n = now_ict()
    return n.date() if n.hour >= CUTOFF_HOUR else n.date() - timedelta(days=1)


def retry(fn, what, tries=3):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{what}: {type(last).__name__}: {last}")


def latest_closes(tickers, day):
    """{ticker: (วันที่, ราคาปิด)} ของวันล่าสุดที่ไม่เกิน day · ดาวน์โหลดทีละ CHUNK ตัว"""
    syms = [t + ".BK" for t in tickers]
    if REF not in syms:
        syms.append(REF)
    start, end = (day - timedelta(days=14)).isoformat(), (day + timedelta(days=1)).isoformat()
    out = {}
    for i in range(0, len(syms), CHUNK):
        part = syms[i:i + CHUNK]
        df = retry(lambda: yf().download(part, start=start, end=end, auto_adjust=False, progress=False,
                                         threads=True, group_by="column"), f"download {i}")
        close = df["Close"]
        for sym in part:
            if sym not in close:
                continue
            s = close[sym].dropna()
            s = s[[d.date() <= day for d in s.index]]
            if len(s):
                out[sym[:-3]] = (str(s.index[-1].date()), float(s.iloc[-1]))
        print(f"  ราคา {min(i + CHUNK, len(syms))}/{len(syms)}")
        time.sleep(1)
    return out


def pin_from(closes):
    """วันทำการล่าสุด = วันของราคาปิดล่าสุดของ PTT (สำรอง: วันที่พบบ่อยสุด)"""
    if "PTT" in closes:
        return closes["PTT"][0]
    return Counter(d for d, _ in closes.values()).most_common(1)[0][0]


def reprice_payload(payload, closes, pin, stamp):
    """อัปเดตตัวชี้วัดที่ขึ้นกับราคาของทุกหุ้น · คืนจำนวนที่อัปเดตได้"""
    done = 0
    for s in payload["stocks"]:
        c = closes.get(s["t"])
        if not c or not c[1] or "k" not in s:
            continue
        d, px = c
        s["m"] = metrics.reprice(s["m"], s["k"], px)
        s["mc"] = metrics.reprice(s["mc"], s["kc"], px)
        if d != pin:
            s["pd"] = d      # หุ้นที่ไม่มีการซื้อขายวันล่าสุด (เช่นถูกพักการซื้อขาย) → ใช้ราคาปิดวันล่าสุดที่มี
        else:
            s.pop("pd", None)
        done += 1
    payload["meta"]["price_date"] = pin
    payload["meta"]["updated"] = stamp
    return done


def check_coverage(name, done, total):
    if total and done / total < MIN_COVERAGE:
        sys.exit(f"{name}: ได้ราคาเพียง {done}/{total} ตัว (< {MIN_COVERAGE:.0%}) — ไม่เขียนไฟล์ ลองใหม่ภายหลัง")


# ---------- ดึงงบใหม่ (รูปแบบไฟล์เดียวกับ data/fetch.py ของ workspace) ----------
def clean(v):
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def frame_to_dict(df):
    if df is None or getattr(df, "empty", True):
        return None
    return {str(c.date()) if hasattr(c, "date") else str(c):
            {str(i): clean(df.at[i, c]) for i in df.index} for c in df.columns}


def fetch_raw(ticker, pin, stamp):
    t = yf().Ticker(ticker + ".BK")
    start = (datetime.fromisoformat(pin) - timedelta(days=3660)).date().isoformat()
    end = (datetime.fromisoformat(pin) + timedelta(days=1)).date().isoformat()
    hist = retry(lambda: t.history(start=start, end=end, auto_adjust=False), "history")
    if hist is None or hist.empty:
        raise RuntimeError("ไม่มีราคา")
    info = retry(lambda: t.info, "info") or {}
    divs = retry(lambda: t.dividends, "dividends")
    hist = hist[[d.date().isoformat() <= pin for d in hist.index]]
    return {
        "ticker": ticker, "yahoo_symbol": ticker + ".BK", "price_pin_date": pin,
        "source": f"Yahoo Finance (yfinance) ดึงเมื่อ {stamp}", "fetched_ict": stamp,
        "info": {k: clean(info.get(k)) for k in INFO_FIELDS},
        "prices": [{"date": str(i.date()), "close": clean(r["Close"])} for i, r in hist.iterrows()],
        "dividends": [{"xd_date": str(i.date()), "dps": clean(v)} for i, v in (divs if divs is not None else {}).items()
                      if start <= str(i.date()) <= pin],
        "income_stmt": frame_to_dict(retry(lambda: t.income_stmt, "income_stmt")),
        "cashflow": frame_to_dict(retry(lambda: t.cashflow, "cashflow")),
        "balance_sheet": frame_to_dict(retry(lambda: t.balance_sheet, "balance_sheet")),
    }


def refresh_statements(old, pin, stamp, presets):
    """ดึงงบทุกหุ้นใน yahoo.js เดิม → payload ใหม่ (ตัวที่ดึงไม่ได้ใช้แถวเดิม + ราคาใหม่)"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tickers = [s["t"] for s in old["stocks"]]
    failed, consec = [], 0
    for n, tk in enumerate(tickers, 1):
        path = RAW_DIR / f"{tk}.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("price_pin_date") == pin:
            continue   # ดึงไว้แล้วรอบนี้ (รันต่อจากที่ค้างได้)
        try:
            path.write_text(json.dumps(fetch_raw(tk, pin, stamp), ensure_ascii=False), encoding="utf-8")
            consec = 0
        except Exception as e:  # noqa: BLE001
            failed.append(tk)
            consec += 1
            print(f"  ✗ {tk}: {e}")
            if consec > MAX_CONSEC_FAIL:
                sys.exit(f"ดึงงบไม่ได้ติดกัน {consec} ตัว — หยุด ไม่เขียนไฟล์ (Yahoo อาจจำกัดการดึง)")
        if n % 50 == 0:
            print(f"  งบ {n}/{len(tickers)} (ล้มเหลว {len(failed)})")
        time.sleep(0.5)
    recs = read_yahoo.load(RAW_DIR)
    if len(recs) < MIN_COVERAGE * len(tickers):
        sys.exit(f"ได้งบเพียง {len(recs)}/{len(tickers)} ตัว — ไม่เขียนไฟล์")
    rows = {r["ticker"]: build.stock_row(r) for r in recs}
    meta = build.yahoo_meta(recs, stamp)
    # ตัวที่ดึงงบไม่ได้: คงแถวเดิมไว้ แล้วอัปเดตแค่ราคา
    keep = {"stocks": [s for s in old["stocks"] if s["t"] not in rows], "meta": {}}
    if keep["stocks"]:
        reprice_payload(keep, latest_closes([s["t"] for s in keep["stocks"]], datetime.fromisoformat(pin).date()), pin, stamp)
    payload = {"meta": meta, "stocks": sorted([*rows.values(), *keep["stocks"]], key=lambda s: s["t"])}
    closes = {r["ticker"]: (r["price_date"], r["price"]) for r in recs}
    print(f"งบใหม่ {len(recs)} ตัว · ใช้แถวเดิม {len(keep['stocks'])} ตัว · ล้มเหลว {failed[:20]}")
    return payload, closes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--statements", action="store_true", help="ดึงงบการเงินใหม่ทั้งหมดด้วย (ราว 40 นาที)")
    a = ap.parse_args()
    presets = build.load_presets()
    stamp = build.now_stamp()
    day = target_day()
    yahoo, setsmart = build.load_js("yahoo"), build.load_js("setsmart")
    if not yahoo:
        sys.exit("ไม่พบ output/data/yahoo.js — รัน screener/build.py ในเครื่องก่อน")

    if a.statements:
        ref = latest_closes(["PTT"], day)
        pin = pin_from(ref)
        print(f"ดึงงบใหม่ · วันราคาปิด {pin}")
        yahoo, closes = refresh_statements(yahoo, pin, stamp, presets)
        if setsmart:
            missing = [s["t"] for s in setsmart["stocks"] if s["t"] not in closes]
            if missing:
                closes.update(latest_closes(missing, day))
    else:
        tickers = sorted({s["t"] for s in yahoo["stocks"]} | {s["t"] for s in (setsmart or {"stocks": []})["stocks"]})
        print(f"ดึงราคาปิด {len(tickers)} ตัว (ไม่เกิน {day})")
        closes = latest_closes(tickers, day)
        pin = pin_from(closes)
        check_coverage("yahoo", reprice_payload(yahoo, closes, pin, stamp), len(yahoo["stocks"]))
    if setsmart:
        check_coverage("setsmart", reprice_payload(setsmart, closes, pin, stamp), len(setsmart["stocks"]))

    # เขียนไฟล์หลังตรวจครบทั้งสองชุด — ถ้าชุดใดไม่ผ่าน จะไม่มีไฟล์ไหนถูกเขียนครึ่ง ๆ
    for name, payload in (("yahoo", yahoo), ("setsmart", setsmart)):
        if payload:
            build.dump_js(name, payload)
            build.summarize(name, payload, presets)
    print(f"เสร็จ: ราคาปิด {pin} · อัปเดต {stamp}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"price_date={pin}\nstatements={'yes' if a.statements else 'no'}\n")


if __name__ == "__main__":
    main()
