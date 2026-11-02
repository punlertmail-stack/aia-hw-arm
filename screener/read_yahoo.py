"""อ่านข้อมูลดิบ Yahoo Finance (<workspace>/data/raw/<TICKER>.json) → record รูปแบบกลาง (ดู metrics.py)

ไฟล์ดิบสร้างโดย data/fetch.py ของ workspace: งบรายปี (income_stmt, balance_sheet, cashflow)
หน่วยบาท, ราคารายวัน, ปันผลรายครั้ง
"""
import json
from pathlib import Path

URL = "https://finance.yahoo.com/quote/{t}.BK/{page}/"

# ช่องในรูปแบบกลาง → (งบ, ชื่อรายการใน Yahoo, หน้าเว็บที่ใช้เป็นที่มา)
FIELDS = {
    "revenue":       ("income_stmt", ["Total Revenue"], "financials"),
    "np":            ("income_stmt", ["Net Income Common Stockholders"], "financials"),
    "gross_profit":  ("income_stmt", ["Gross Profit"], "financials"),
    "normalized_np": ("income_stmt", ["Normalized Income"], "financials"),
    "equity":        ("balance_sheet", ["Stockholders Equity"], "balance-sheet"),
    "equity_total":  ("balance_sheet", ["Total Equity Gross Minority Interest"], "balance-sheet"),
    "ib_debt":       ("balance_sheet", ["Total Debt"], "balance-sheet"),
    "cur_assets":    ("balance_sheet", ["Current Assets"], "balance-sheet"),
    "cur_liab":      ("balance_sheet", ["Current Liabilities"], "balance-sheet"),
    "cfo":           ("cashflow", ["Operating Cash Flow"], "cash-flow"),
    "fcf":           ("cashflow", ["Free Cash Flow"], "cash-flow"),
    "div_paid":      ("cashflow", ["Cash Dividends Paid", "Common Stock Dividend Paid"], "cash-flow"),
}
MAX_PERIODS = 5


def field_sources():
    """ป้ายที่มาของแต่ละช่อง สำหรับ popover บนหน้าเว็บ"""
    src = {k: {"name": " / ".join(names), "page": page} for k, (_, names, page) in FIELDS.items()}
    src["price"] = {"name": "Close (ราคาปิด)", "page": "history"}
    src["shares"] = {"name": "sharesOutstanding", "page": "key-statistics"}
    src["equity_prev"] = src["equity"]
    return src


def pick(stmt, names):
    for n in names:
        v = stmt.get(n)
        if v is not None:
            return v
    return None


def mb(v):
    return None if v is None else v / 1e6


def price_on(prices, pin):
    """ราคาปิดของวันล่าสุดที่ไม่เกินวันปักราคา"""
    best = None
    for p in prices:
        if p["date"] <= pin and p.get("close") is not None:
            best = p
    return best


def to_record(d):
    info = d.get("info") or {}
    pin = d.get("price_pin_date")
    px = price_on(d.get("prices") or [], pin) if pin else None
    shares = info.get("sharesOutstanding")
    if not px or not shares:
        return None
    events = [(x["xd_date"], x["dps"]) for x in d.get("dividends") or [] if x.get("dps")]
    dps = {}
    for xd, v in events:
        dps[xd[:4]] = round(dps.get(xd[:4], 0) + v, 4)

    inc, bs = d.get("income_stmt") or {}, d.get("balance_sheet") or {}
    years = sorted(inc)[-MAX_PERIODS:]
    periods = []
    for y in years:
        p = {"label": "FY" + y[:4]}
        for k, (stmt, names, _) in FIELDS.items():
            p[k] = mb(pick((d.get(stmt) or {}).get(y) or {}, names))
        prev = [b for b in sorted(bs) if b < y]
        p["equity_prev"] = mb(bs[prev[-1]].get("Stockholders Equity")) if prev else None
        if p["div_paid"] is not None:
            p["div_paid"] = abs(p["div_paid"])
        elif y[:4] in dps:
            # ไม่มีรายการปันผลในงบกระแสเงินสด → ใช้ปันผลต่อหุ้นที่ขึ้น XD ในปีนั้น × จำนวนหุ้นปัจจุบัน
            p["div_paid"] = dps[y[:4]] * shares / 1e6
        else:
            p["div_paid"] = 0.0
        periods.append(p)
    if not periods:
        return None

    return {
        "ticker": d["ticker"],
        "name": info.get("longName") or d["ticker"],
        "sector": info.get("sector") or "",
        "fin": info.get("sector") == "Financial Services",
        "price": px["close"],
        "price_date": px["date"],
        "shares": shares,
        "mcap": px["close"] * shares / 1e6,
        "fetched": d.get("fetched_ict"),
        "periods": periods,
        "dps": dps,
        "div_events": events,
    }


def load(raw_dir):
    """คืน list ของ record (ตัวที่ไม่มีราคา/จำนวนหุ้น/งบ ถูกข้าม)"""
    out = []
    for fn in sorted(Path(raw_dir).glob("*.json")):
        rec = to_record(json.loads(fn.read_text(encoding="utf-8")))
        if rec:
            out.append(rec)
    return out
