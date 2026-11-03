"""สร้างไฟล์ข้อมูลของ dashboard จากข้อมูลในเครื่อง

  output/data/yahoo.js     จาก <workspace>/data/raw/*.json (Yahoo Finance)
  output/data/setsmart.js  จาก <workspace>/DATA001/setsmart_fundamentals_*.xlsx (ถ้ามีไฟล์)
  output/data/presets.js   สำเนาของ presets.json ให้หน้าเว็บโหลดได้แม้เปิดแบบ file://

ส่งออกเฉพาะตัวชี้วัดที่คำนวณแล้ว + ประวัติ รายได้/กำไร/ROE/FCF/ปันผลจ่าย สำหรับกราฟ ไม่มีไฟล์ดิบ

รัน (จากโฟลเดอร์ repo):  python screener/build.py [--workspace PATH] [--xlsx PATH] [--min-period 2026Q1]
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics  # noqa: E402
import read_setsmart  # noqa: E402
import read_yahoo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "data"
DPS_YEARS = 6
ICT = timezone(timedelta(hours=7))

# คำอธิบายใต้สวิตช์ "ไม่นับกำไรพิเศษ" — นิยามต่างกันตามแหล่งข้อมูล
SPECIAL_PROFIT_NOTE = {
    "yahoo": "กำไรพิเศษ = รายการที่ Yahoo Finance จัดเป็น Unusual Items หลังหักภาษี เช่น กำไร/ขาดทุนจากการขายเงินลงทุน "
             "การขายสินทรัพย์หรือธุรกิจ การด้อยค่าสินทรัพย์และค่าความนิยม ค่าใช้จ่ายปรับโครงสร้าง · "
             "ไม่รวมกำไร/ขาดทุนจากอัตราแลกเปลี่ยน เพราะ Yahoo ไม่ได้แยกไว้",
    "setsmart": "กำไรพิเศษ = รายได้อื่นในไตรมาสที่สูงผิดปกติ (≥ 3 เท่าของมัธยฐาน 8 ไตรมาสก่อน และส่วนเกิน ≥ 50% "
                "ของกำไรไตรมาสนั้น) นับเฉพาะส่วนที่เกินปกติ หักภาษี 20% · มักเป็นกำไรจากการขายเงินลงทุนหรือสินทรัพย์ "
                "หรือกำไรจากอัตราแลกเปลี่ยนที่บันทึกในรายได้อื่น · รายการพิเศษที่ไม่ได้ผ่านรายได้อื่นจะจับไม่ได้",
}


def stock_row(rec):
    dps = {y: rec["dps"][y] for y in sorted(rec["dps"])[-DPS_YEARS:]}
    dpss = {y: v for y, v in rec["dps_special"].items() if y in dps}
    return {
        "t": rec["ticker"],
        "n": rec["name"],
        "sec": rec["sector"],
        "fin": rec["fin"],
        "per": rec["periods"][-1]["label"],
        "m": metrics.compute(rec),
        "mc": metrics.compute_core(rec),
        "h": metrics.history(rec),
        "dps": dps,
        "dpss": dpss,
    }


def write_js(name, meta, recs):
    payload = {"meta": meta, "stocks": [stock_row(r) for r in recs]}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.js"
    path.write_text("window.SCREENER_DATA = window.SCREENER_DATA || {};\n"
                    f"window.SCREENER_DATA.{name} = {text};\n", encoding="utf-8")
    return payload, path


def summarize(name, payload, presets):
    stocks = [s for s in payload["stocks"] if (s["m"]["mcap"] or 0) >= presets["universe"]["min_mcap"]]
    sd, sp = presets["special_dividend"], presets["special_profit"]

    def count(p, ex_div, ex_profit):
        # แท็บปันผลมีแค่สวิตช์ปันผลพิเศษ · แท็บอื่นมีแค่สวิตช์กำไรพิเศษ (ตรงกับ app.js)
        if p["id"] == "dividend":
            return sum(metrics.passes(s["m"], s["fin"], p, sd["alt"] if ex_div else None) for s in stocks)
        return sum(metrics.passes(s["m"], s["fin"], p, None, s["mc"] if ex_profit else None) for s in stocks)

    default = {p["id"]: count(p, sd["exclude"], sp["exclude"]) for p in presets["presets"]}
    flipped = {p["id"]: count(p, not sd["exclude"], not sp["exclude"]) for p in presets["presets"]}
    div_flag = sum(1 for s in stocks if s["m"]["sp_share"])
    np_flag = sum(1 for s in stocks if abs(s["m"]["norm_gap"] or 0) >= 10)
    size = (OUT / f"{name}.js").stat().st_size / 1e6
    print(f"{name}: {len(payload['stocks'])} ตัว · ฐานคัด {len(stocks)} ตัว · ผ่าน (ค่าตั้งต้น) {default} · {size:.2f} MB\n"
          f"  ถ้าสลับสวิตช์ทั้งสอง: ผ่าน {flipped} · มีปันผลพิเศษ {div_flag} ตัว · กำไรพิเศษ ≥ 10% ของกำไร {np_flag} ตัว")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=str(ROOT.parent.parent))
    ap.add_argument("--xlsx", default=None)
    ap.add_argument("--min-period", default="2026Q1")
    a = ap.parse_args()
    ws = Path(a.workspace)
    presets = json.loads((ROOT / "presets.json").read_text(encoding="utf-8"))
    built = datetime.now(ICT).strftime("%Y-%m-%d %H:%M ICT")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "presets.js").write_text("window.SCREENER_PRESETS = "
                                    + json.dumps(presets, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")

    yahoo = read_yahoo.load(ws / "data" / "raw")
    if not yahoo:
        sys.exit(f"ไม่พบข้อมูล Yahoo ที่ {ws / 'data' / 'raw'}")
    price_date = Counter(r["price_date"] for r in yahoo).most_common(1)[0][0]
    fetched = Counter((r["fetched"] or "")[:10] for r in yahoo).most_common(1)[0][0]
    last_fy = Counter(r["periods"][-1]["label"] for r in yahoo).most_common(1)[0][0]
    meta = {
        "source": "yahoo", "label": "Yahoo Finance",
        "period_note": f"งบรายปี ส่วนใหญ่ล่าสุด {last_fy}",
        "price_date": price_date, "fetched": fetched, "built": built,
        "url": read_yahoo.URL, "fields": read_yahoo.field_sources(), "metrics": metrics.METRICS,
        "core_keys": metrics.CORE_KEYS, "special_profit_note": SPECIAL_PROFIT_NOTE["yahoo"],
    }
    payload, _ = write_js("yahoo", meta, yahoo)
    summarize("yahoo", payload, presets)

    xlsx = Path(a.xlsx) if a.xlsx else None
    if xlsx is None:
        found = sorted((ws / "DATA001").glob("setsmart_fundamentals_*.xlsx"), key=lambda p: p.stat().st_mtime)
        xlsx = found[-1] if found else None
    if xlsx is None or not xlsx.exists():
        print("ไม่พบไฟล์ SETSMART xlsx → ข้ามโหมด SETSMART")
        return
    prices = {r["ticker"]: {k: r[k] for k in ("price", "price_date", "shares", "name", "sector", "dps", "dps_special", "div_events")} for r in yahoo}
    recs, latest = read_setsmart.load(xlsx, prices, a.min_period)
    meta = {
        "source": "setsmart", "label": "SETSMART",
        "period_note": f"TTM ล่าสุด {latest} · ราคาจาก Yahoo",
        "price_date": price_date, "fetched": fetched, "built": built,
        "url": None, "file_note": "SETSMART fundamentals 2021Q1–2026Q2 (ไฟล์อยู่ในเครื่องผู้เรียน ไม่ขึ้น GitHub)",
        "fields": read_setsmart.field_sources(), "metrics": metrics.METRICS,
        "core_keys": metrics.CORE_KEYS, "special_profit_note": SPECIAL_PROFIT_NOTE["setsmart"],
    }
    payload, _ = write_js("setsmart", meta, recs)
    summarize("setsmart", payload, presets)


if __name__ == "__main__":
    main()
