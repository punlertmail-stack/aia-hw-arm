"""อ่านข้อมูลดิบ Yahoo Finance (<workspace>/data/raw/<TICKER>.json) → record รูปแบบกลาง (ดู metrics.py)

ไฟล์ดิบสร้างโดย data/fetch.py ของ workspace: งบรายปี (income_stmt, balance_sheet, cashflow)
หน่วยบาท, ราคารายวัน, ปันผลรายครั้ง
"""
import bisect
import json
import statistics
from datetime import date
from pathlib import Path

URL = "https://finance.yahoo.com/quote/{t}.BK/{page}/"

# ปันผลพิเศษ: กฎเดียวกับ tools/build_fundamentals.py ของ workspace (dashboard Day 1)
SPECIAL_X = 3.0          # DPS งวดนั้น >= 3 เท่าของค่ามัธยฐานงวดก่อนหน้า
SPECIAL_MIN_PCT = 1.0    # และต้อง >= 1% ของราคา ณ วัน XD ไม่งั้นเล็กเกินไปที่จะเตือน
SAME_SEASON_DAYS = 45    # เทียบงวดเดียวกันของปีก่อน ๆ ก่อน (กันงวดปลายปีที่ใหญ่กว่ากลางปีเป็นปกติ)

# ช่องในรูปแบบกลาง → (งบ, ชื่อรายการใน Yahoo, หน้าเว็บที่ใช้เป็นที่มา)
FIELDS = {
    "revenue":       ("income_stmt", ["Total Revenue"], "financials"),
    "np":            ("income_stmt", ["Net Income Common Stockholders"], "financials"),
    "gross_profit":  ("income_stmt", ["Gross Profit"], "financials"),
    "unusual":       ("income_stmt", ["Total Unusual Items"], "financials"),
    "unusual_tax":   ("income_stmt", ["Tax Effect Of Unusual Items"], "financials"),
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
    src["np_special"] = {"name": "Total Unusual Items − Tax Effect Of Unusual Items", "page": "financials",
                         "how": "กำไรพิเศษหลังภาษี (ขายเงินลงทุน/สินทรัพย์ ด้อยค่า ฯลฯ ตามที่ Yahoo จัดประเภท) · ไม่รวมกำไรค่าเงิน"}
    src["div_special"] = {"name": "Yahoo dividends (ส่วนเกินของงวดที่ตีธงพิเศษ) × sharesOutstanding", "page": "history",
                          "how": f"งวดที่ DPS ≥ {SPECIAL_X:g} เท่าของมัธยฐานงวดก่อนหน้า และ ≥ {SPECIAL_MIN_PCT:g}% ของราคาวัน XD · นับเฉพาะส่วนที่เกินมัธยฐาน"}
    return src


def season_gap(a, b):
    """ระยะห่าง (วัน) ระหว่างวันในปีของสองวันที่ วนรอบปี"""
    d = abs(date.fromisoformat(a).timetuple().tm_yday - date.fromisoformat(b).timetuple().tm_yday)
    return min(d, 365 - d)


def special_parts(events, close_on):
    """events = [(xd, dps)] เรียงตามวัน · close_on(xd) = ราคาปิด ณ วัน XD
    → ส่วนที่เป็นปันผลพิเศษของแต่ละงวด (บาท/หุ้น) = DPS − มัธยฐานงวดที่ใช้เทียบ (งวดปกติ = 0)
    นับเฉพาะส่วนเกิน เพราะงวดที่จ่ายพิเศษมักรวมปันผลปกติของงวดนั้นไว้ด้วย"""
    parts = []
    for i, (xd, dps) in enumerate(events):
        px = close_on(xd)
        same = [v for d0, v in events[:i] if season_gap(d0, xd) <= SAME_SEASON_DAYS][-3:]
        prior = same if len(same) >= 2 else [v for _, v in events[max(0, i - 6):i]]
        base = statistics.median(prior) if len(prior) >= 2 else None
        big = px and dps / px * 100 >= SPECIAL_MIN_PCT and base is not None and dps >= SPECIAL_X * base
        parts.append(dps - base if big else 0.0)
    return parts


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
    priced = [(p["date"], p["close"]) for p in d.get("prices") or [] if p.get("close") is not None]
    dates = [x for x, _ in priced]
    close_on = lambda xd: priced[i - 1][1] if (i := bisect.bisect_right(dates, xd)) else None
    raw = sorted((x["xd_date"], x["dps"]) for x in d.get("dividends") or [] if x.get("dps"))
    events = [(xd, v, sp) for (xd, v), sp in zip(raw, special_parts(raw, close_on))]
    dps, dps_sp = {}, {}
    for xd, v, sp in events:
        dps[xd[:4]] = round(dps.get(xd[:4], 0) + v, 4)
        if sp:
            dps_sp[xd[:4]] = round(dps_sp.get(xd[:4], 0) + sp, 4)

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
        # กำไรพิเศษหลังภาษี = รายการพิเศษก่อนภาษี − ผลทางภาษีของรายการนั้น (ไม่มีรายการ = 0)
        p["np_special"] = (p.pop("unusual") or 0) - (p.pop("unusual_tax") or 0) if p["np"] is not None else None
        start = str(int(y[:4]) - 1) + y[4:]
        p["div_special"] = sum(sp for xd, _, sp in events if start < xd <= y) * shares / 1e6
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
        "dps_special": dps_sp,
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
