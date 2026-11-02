"""อ่านไฟล์ SETSMART fundamentals (.xlsx) ในเครื่อง → record รูปแบบกลาง (ดู metrics.py)

ไฟล์ดิบไม่ขึ้น GitHub (.gitignore บล็อก *SETSMART*) ตัวนี้ส่งออกเฉพาะค่าที่ metrics.py ใช้คำนวณ
อ่าน xlsx ด้วย stdlib (zipfile + iterparse) เพราะ Python 3.15 ยังไม่มี openpyxl

ข้อควรระวังที่เจอแล้ว (PROJECT_MEMORY.md):
- xlsx ไม่เก็บช่องว่าง → หาคอลัมน์จาก cell reference ห้ามนับลำดับช่อง
- คอลัมน์ *_pct เป็น ratio ไม่ใช่ % (ตัวนี้ไม่ใช้ *_pct เลย คำนวณ ROE เองจาก equity)
- dividends_paid ว่าง 61% ของแถว แม้ไตรมาสที่จ่ายจริง → ปันผลจ่ายใช้ DPS ที่ XD ในช่วง × จำนวนหุ้น จาก Yahoo แทน
"""
from datetime import date
import re
import zipfile
import xml.etree.ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MAX_PERIODS = 5

# ช่องในรูปแบบกลาง → คอลัมน์ SETSMART (ใช้คอลัมน์แรกที่มีค่า)
TTM = {
    "revenue": ["revenue_operations_ttm", "total_revenue_ttm"],
    "np": ["net_profit_parent_ttm"],
    "cfo": ["cfo_ttm"],
    "fcf": ["free_cash_flow_ttm"],
}
AT_END = {
    "equity": ["equity_parent"],
    "equity_total": ["total_equity"],
    "ib_debt": ["interest_bearing_debt"],
    "cur_assets": ["total_current_assets"],
    "cur_liab": ["total_current_liabilities"],
}
SUM4 = {"gross_profit": "gross_profit"}  # ไม่มีคอลัมน์ TTM → รวม 4 ไตรมาสเอง


def field_sources():
    src = {k: {"name": " / ".join(v), "how": "TTM (4 ไตรมาส)"} for k, v in TTM.items()}
    src.update({k: {"name": " / ".join(v), "how": "ณ สิ้นงวด"} for k, v in AT_END.items()})
    src["gross_profit"] = {"name": "gross_profit", "how": "รวม 4 ไตรมาส"}
    src["equity_prev"] = {"name": "equity_parent", "how": "ณ สิ้นงวดเดียวกันปีก่อน"}
    src["normalized_np"] = {"name": "(ไม่มีใน SETSMART)", "how": ""}
    src["div_paid"] = {"name": "Yahoo dividends × sharesOutstanding",
                       "how": "ปันผลต่อหุ้นที่ XD ใน 12 เดือนของช่วง × จำนวนหุ้นปัจจุบัน (dividends_paid ของ SETSMART ว่าง 61% ของแถว)"}
    src["price"] = {"name": "Yahoo Close (ราคาปิด)", "how": "Yahoo"}
    src["shares"] = {"name": "Yahoo sharesOutstanding", "how": "Yahoo"}
    return src


def col_index(ref):
    """'AB12' → 27 (เริ่มที่ 0)"""
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheet(z, name, strings):
    """คืน list ของ dict ต่อแถว โดยจับคู่หัวตารางด้วยเลขคอลัมน์"""
    header, rows = None, []
    for _, el in ET.iterparse(z.open(name)):
        if el.tag != NS + "row":
            continue
        cells = {}
        for c in el.iter(NS + "c"):
            v = c.find(NS + "v")
            if v is not None:
                cells[col_index(c.get("r"))] = strings[int(v.text)] if c.get("t") == "s" else v.text
        el.clear()
        if header is None:
            header = cells
        else:
            rows.append({header[i]: v for i, v in cells.items() if i in header})
    return rows


def sheet_paths(z):
    """ชื่อ sheet → พาธไฟล์ใน zip (ลำดับ sheet ตรงกับ sheet1.xml, sheet2.xml, ...)"""
    wb = ET.parse(z.open("xl/workbook.xml")).getroot()
    names = [s.get("name") for s in wb.iter(NS + "sheet")]
    return {n: f"xl/worksheets/sheet{i + 1}.xml" for i, n in enumerate(names)}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def first(row, cols):
    for c in cols:
        v = num(row.get(c))
        if v is not None:
            return v
    return None


def qnum(period):
    """'2026Q2' → เลขไตรมาสต่อเนื่อง ใช้เช็กว่าไม่มีไตรมาสหาย"""
    y, q = period.split("Q")
    return int(y) * 4 + int(q) - 1


def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        strings = ["".join(t.text or "" for t in si.iter(NS + "t"))
                   for si in ET.parse(z.open("xl/sharedStrings.xml")).getroot()]
        paths = sheet_paths(z)
        companies = {r["symbol"]: r for r in read_sheet(z, paths["companies"], strings)}
        rows = read_sheet(z, paths["fundamentals"], strings)
    return companies, rows


def quarter_end(q):
    """เลขไตรมาสต่อเนื่อง → วันสิ้นไตรมาส"""
    y, i = divmod(q, 4)
    return date(y, 3 * i + 3, 31 if i in (0, 3) else 30)


def div_in_window(events, shares, end_q):
    """ปันผลจ่าย (ล้านบาท) = DPS ที่ XD ในช่วง 12 เดือนที่จบไตรมาส end_q × จำนวนหุ้น"""
    start, end = quarter_end(end_q - 4).isoformat(), quarter_end(end_q).isoformat()
    return sum(v for xd, v in events if start < xd <= end) * shares / 1e6


def to_record(symbol, company, quarters, px):
    """quarters = แถวรายไตรมาสของหุ้นตัวนี้ · px = ข้อมูลจาก Yahoo (price, price_date, shares, name, sector, dps, div_events)"""
    byq = {qnum(r["period"]): r for r in quarters}
    latest = max(byq)
    periods = []
    # ช่วง TTM ที่จบไตรมาสเดียวกับล่าสุด ย้อนทีละ 1 ปี → ทุกช่วงยาว 12 เดือนและห่างกันพอดี 1 ปี
    for k in range(MAX_PERIODS - 1, -1, -1):
        end = latest - 4 * k
        r = byq.get(end)
        if r is None:
            continue
        p = {"label": "TTM " + r["period"]}
        for f, cols in TTM.items():
            p[f] = first(r, cols)
        for f, cols in AT_END.items():
            p[f] = first(r, cols)
        for f, c in SUM4.items():
            q4 = [num(byq[q].get(c)) if q in byq else None for q in range(end - 3, end + 1)]
            p[f] = sum(q4) if all(v is not None for v in q4) else None
        prev = byq.get(end - 4)
        p["equity_prev"] = first(prev, AT_END["equity"]) if prev else None
        p["normalized_np"] = None
        p["div_paid"] = div_in_window(px["div_events"], px["shares"], end)
        periods.append(p)
    if not periods:
        return None
    return {
        "ticker": symbol,
        "name": px["name"],
        "sector": px["sector"],
        "fin": company.get("entity_type") != "general",
        "price": px["price"],
        "price_date": px["price_date"],
        "shares": px["shares"],
        "mcap": px["price"] * px["shares"] / 1e6,
        "periods": periods,
        "dps": px["dps"],
    }


def load(path, prices, min_period):
    """prices = {ticker: dict จาก Yahoo} · min_period = งวดล่าสุดขั้นต่ำ (เช่น '2026Q1') ใช้ตัดหุ้นที่เลิกซื้อขาย"""
    companies, rows = read_xlsx(path)
    by = {}
    for r in rows:
        if r.get("symbol") and r.get("period"):
            by.setdefault(r["symbol"], []).append(r)
    out, latest_period = [], ""
    for s, qs in sorted(by.items()):
        last = max(r["period"] for r in qs)
        latest_period = max(latest_period, last)
        if last < min_period or s not in prices:
            continue
        rec = to_record(s, companies.get(s, {}), qs, prices[s])
        if rec:
            out.append(rec)
    return out, latest_period
