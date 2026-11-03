"""คำนวณตัวชี้วัดจาก "รูปแบบกลาง" — สูตรชุดเดียวใช้กับทุกแหล่งข้อมูล

รูปแบบกลาง (record) ที่ตัวอ่านทุกตัวต้องส่งมา:
  ticker, name, sector, fin (bool), price, price_date, mcap (ล้านบาท)
  periods: list เรียงเก่า → ใหม่ (ไม่เกิน 5 ช่วง) แต่ละช่วงยาว 12 เดือนและห่างกัน 1 ปี
    label, revenue, np, gross_profit, normalized_np, equity, equity_prev,
    equity_total, ib_debt, cur_assets, cur_liab, cfo, fcf, div_paid, div_special
    (หน่วยล้านบาท · div_paid เป็นค่าบวก · div_special = ส่วนที่เป็นปันผลพิเศษ · ไม่มีข้อมูล = None)
  dps, dps_special: {ปี ค.ศ.: ปันผลต่อหุ้นรวม / เฉพาะงวดพิเศษ ที่ XD ในปีนั้น}
"""
import statistics

# ตัวชี้วัดทั้งหมด: ป้ายชื่อ · หน่วย · สูตร · ช่องข้อมูลที่ใช้ (ใช้แสดงที่มาบนหน้าจอ)
METRICS = {
    "price":      {"label": "ราคา", "unit": "บาท", "formula": "ราคาปิด", "inputs": ["price"]},
    "mcap":       {"label": "มูลค่าตลาด", "unit": "ลบ.", "formula": "ราคาปิด × จำนวนหุ้น", "inputs": ["price", "shares"]},
    "pe":         {"label": "P/E", "unit": "เท่า", "formula": "มูลค่าตลาด ÷ กำไรสุทธิ (ช่วงล่าสุด)", "inputs": ["price", "shares", "np"]},
    "pbv":        {"label": "P/BV", "unit": "เท่า", "formula": "มูลค่าตลาด ÷ ส่วนผู้ถือหุ้นบริษัทใหญ่", "inputs": ["price", "shares", "equity"]},
    "ey":         {"label": "Earnings yield", "unit": "%", "formula": "กำไรสุทธิ ÷ มูลค่าตลาด × 100", "inputs": ["np", "price", "shares"]},
    "by":         {"label": "Book yield", "unit": "%", "formula": "ส่วนผู้ถือหุ้น ÷ มูลค่าตลาด × 100", "inputs": ["equity", "price", "shares"]},
    "dy":         {"label": "Dividend yield", "unit": "%", "formula": "ปันผลจ่าย (เงินสด) ÷ มูลค่าตลาด × 100", "inputs": ["div_paid", "price", "shares"]},
    "fcfy":       {"label": "FCF yield", "unit": "%", "formula": "กระแสเงินสดอิสระ ÷ มูลค่าตลาด × 100", "inputs": ["fcf", "price", "shares"]},
    "roe_last":   {"label": "ROE ล่าสุด", "unit": "%", "formula": "กำไรสุทธิ ÷ ค่าเฉลี่ยส่วนผู้ถือหุ้นต้นและปลายช่วง × 100", "inputs": ["np", "equity"]},
    "roe_avg3":   {"label": "ROE เฉลี่ย 3 ปี", "unit": "%", "formula": "ค่าเฉลี่ย ROE 3 ช่วงล่าสุด", "inputs": ["np", "equity"]},
    "roe_min3":   {"label": "ROE ต่ำสุด 3 ปี", "unit": "%", "formula": "ROE ต่ำสุดใน 3 ช่วงล่าสุด", "inputs": ["np", "equity"]},
    "roe_sd3":    {"label": "ROE แกว่ง (SD)", "unit": "จุด", "formula": "ส่วนเบี่ยงเบนมาตรฐานของ ROE 3 ช่วงล่าสุด (ต่ำ = นิ่ง)", "inputs": ["np", "equity"]},
    "npm":        {"label": "Net margin", "unit": "%", "formula": "กำไรสุทธิ ÷ รายได้ × 100", "inputs": ["np", "revenue"]},
    "gpm":        {"label": "Gross margin", "unit": "%", "formula": "กำไรขั้นต้น ÷ รายได้ × 100", "inputs": ["gross_profit", "revenue"]},
    "de":         {"label": "IBD/E", "unit": "เท่า", "formula": "หนี้สินที่มีภาระดอกเบี้ย ÷ ส่วนของผู้ถือหุ้นรวม", "inputs": ["ib_debt", "equity_total"]},
    "cr":         {"label": "Current ratio", "unit": "เท่า", "formula": "สินทรัพย์หมุนเวียน ÷ หนี้สินหมุนเวียน", "inputs": ["cur_assets", "cur_liab"]},
    "cfo_np3":    {"label": "CFO ÷ กำไร 3 ปี", "unit": "เท่า", "formula": "ผลรวมเงินสดจากการดำเนินงาน 3 ช่วง ÷ ผลรวมกำไรสุทธิ 3 ช่วง", "inputs": ["cfo", "np"]},
    "norm_gap":   {"label": "กำไรพิเศษ", "unit": "%", "formula": "(กำไรสุทธิ − กำไรปกติ) ÷ |กำไรสุทธิ| × 100", "inputs": ["np", "normalized_np"]},
    "rev_cagr3":  {"label": "CAGR รายได้ 3 ปี", "unit": "%", "formula": "(รายได้ล่าสุด ÷ รายได้ 3 ปีก่อน)^(1/3) − 1", "inputs": ["revenue"]},
    "np_cagr3":   {"label": "CAGR กำไร 3 ปี", "unit": "%", "formula": "(กำไรล่าสุด ÷ กำไร 3 ปีก่อน)^(1/3) − 1 (ทั้งสองค่าต้องเป็นบวก)", "inputs": ["np"]},
    "peg":        {"label": "PEG", "unit": "เท่า", "formula": "P/E ÷ CAGR กำไร 3 ปี (%)", "inputs": ["price", "shares", "np"]},
    "payout":     {"label": "Payout ratio", "unit": "%", "formula": "ปันผลจ่าย ÷ กำไรสุทธิ × 100", "inputs": ["div_paid", "np"]},
    "div_streak": {"label": "จ่ายปันผลต่อเนื่อง", "unit": "ปี", "formula": "จำนวนช่วงล่าสุดติดกันที่จ่ายปันผล > 0", "inputs": ["div_paid"]},
    "fcf_cov3":   {"label": "FCF ÷ ปันผล 3 ปี", "unit": "เท่า", "formula": "ผลรวม FCF 3 ช่วง ÷ ผลรวมปันผลจ่าย 3 ช่วง", "inputs": ["fcf", "div_paid"]},
    "dy_ex":      {"label": "Dividend yield ไม่รวมพิเศษ", "unit": "%", "formula": "(ปันผลจ่าย − ปันผลพิเศษ) ÷ มูลค่าตลาด × 100", "inputs": ["div_paid", "div_special", "price", "shares"]},
    "payout_ex":  {"label": "Payout ไม่รวมพิเศษ", "unit": "%", "formula": "(ปันผลจ่าย − ปันผลพิเศษ) ÷ กำไรสุทธิ × 100", "inputs": ["div_paid", "div_special", "np"]},
    "sp_share":   {"label": "สัดส่วนปันผลพิเศษ", "unit": "%", "formula": "ปันผลพิเศษ ÷ ปันผลจ่ายทั้งหมด (ช่วงล่าสุด) × 100", "inputs": ["div_special", "div_paid"]},
    "np_last":    {"label": "กำไรสุทธิล่าสุด", "unit": "ลบ.", "formula": "กำไรสุทธิส่วนของบริษัทใหญ่ ช่วงล่าสุด", "inputs": ["np"]},
    "np_min3":    {"label": "กำไรต่ำสุด 3 ปี", "unit": "ลบ.", "formula": "กำไรสุทธิต่ำสุดใน 3 ช่วงล่าสุด", "inputs": ["np"]},
}


def div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def pct(a, b):
    r = div(a, b)
    return None if r is None else r * 100


def cagr(first, last, years):
    """อัตราเติบโตเฉลี่ยต่อปี (%) ใช้ได้เมื่อทั้งสองค่าเป็นบวกเท่านั้น"""
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def roe(p):
    """ROE ของช่วงหนึ่ง: กำไร ÷ ค่าเฉลี่ยส่วนผู้ถือหุ้นต้นและปลายช่วง (ไม่มีต้นช่วง → ใช้ปลายช่วง)"""
    eq, prev = p.get("equity"), p.get("equity_prev")
    base = (eq + prev) / 2 if eq is not None and prev is not None else eq
    if base is None or base <= 0:
        return None
    return pct(p.get("np"), base)


def last_n(values, n):
    """n ค่าล่าสุด ถ้าขาดตัวใดตัวหนึ่งคืน None (ไม่เดาค่าแทน)"""
    if len(values) < n:
        return None
    tail = values[-n:]
    return None if any(v is None for v in tail) else tail


def streak(values):
    n = 0
    for v in reversed(values):
        if v is None or v <= 0:
            break
        n += 1
    return n


def rnd(v, nd=2):
    return None if v is None else round(v, nd)


def compute(rec):
    """record รูปแบบกลาง → {ชื่อตัวชี้วัด: ค่า} (คำนวณไม่ได้ = None)"""
    ps = rec["periods"]
    L = ps[-1]
    col = lambda k: [p.get(k) for p in ps]
    mcap = rec.get("mcap")
    np_l = L.get("np")
    pe = div(mcap, np_l) if np_l and np_l > 0 else None
    eq_l = L.get("equity")
    roes = [roe(p) for p in ps]
    roe3 = last_n(roes, 3)
    nps3 = last_n(col("np"), 3)
    cfo3 = last_n(col("cfo"), 3)
    fcf3 = last_n(col("fcf"), 3)
    divs3 = last_n(col("div_paid"), 3)
    rev = col("revenue")
    nps = col("np")
    np_g = cagr(nps[-4], nps[-1], 3) if len(nps) >= 4 else None
    div_l, sp_l = L.get("div_paid"), L.get("div_special") or 0
    # ปันผลพิเศษนับตามวัน XD ส่วน div_paid ของ Yahoo นับตามวันจ่ายเงิน จึงกันไม่ให้ติดลบ
    div_ex = max(div_l - sp_l, 0) if div_l is not None else None

    m = {
        "price": rec.get("price"),
        "mcap": mcap,
        "pe": pe,
        "pbv": div(mcap, eq_l) if eq_l and eq_l > 0 else None,
        "ey": pct(np_l, mcap),
        "by": pct(eq_l, mcap),
        "dy": pct(L.get("div_paid"), mcap),
        "fcfy": pct(L.get("fcf"), mcap),
        "roe_last": roes[-1],
        "roe_avg3": statistics.fmean(roe3) if roe3 else None,
        "roe_min3": min(roe3) if roe3 else None,
        "roe_sd3": statistics.pstdev(roe3) if roe3 else None,
        "npm": pct(np_l, L.get("revenue")),
        "gpm": pct(L.get("gross_profit"), L.get("revenue")),
        "de": div(L.get("ib_debt"), L.get("equity_total")),
        "cr": div(L.get("cur_assets"), L.get("cur_liab")),
        "cfo_np3": div(sum(cfo3), sum(nps3)) if cfo3 and nps3 and sum(nps3) > 0 else None,
        "norm_gap": pct(np_l - L["normalized_np"], abs(np_l)) if np_l and L.get("normalized_np") is not None else None,
        "rev_cagr3": cagr(rev[-4], rev[-1], 3) if len(rev) >= 4 else None,
        "np_cagr3": np_g,
        "peg": div(pe, np_g) if pe and np_g and np_g > 0 else None,
        "payout": pct(L.get("div_paid"), np_l) if np_l and np_l > 0 else None,
        "div_streak": streak(col("div_paid")),
        "fcf_cov3": div(sum(fcf3), sum(divs3)) if fcf3 and divs3 and sum(divs3) > 0 else None,
        "dy_ex": pct(div_ex, mcap),
        "payout_ex": pct(div_ex, np_l) if np_l and np_l > 0 else None,
        "sp_share": min(pct(sp_l, div_l), 100) if div_l else None,
        "np_last": np_l,
        "np_min3": min(nps3) if nps3 else None,
    }
    return {k: rnd(v, 0 if k == "mcap" else 2) for k, v in m.items()}


def history(rec):
    """ประวัติรายช่วงสำหรับกราฟหน้ารายตัว (ล้านบาท ปัด 1 ตำแหน่ง)"""
    ps = rec["periods"]
    return {
        "p": [p["label"] for p in ps],
        "rev": [rnd(p.get("revenue"), 1) for p in ps],
        "np": [rnd(p.get("np"), 1) for p in ps],
        "roe": [rnd(roe(p), 2) for p in ps],
        "fcf": [rnd(p.get("fcf"), 1) for p in ps],
        "div": [rnd(p.get("div_paid"), 1) for p in ps],
    }


def passes(m, fin, preset, alt=None):
    """ใช้ตรวจจำนวนที่ผ่านตอน build (หน้าเว็บมีตรรกะเดียวกันใน app.js)
    alt = ตัวชี้วัดที่ใช้แทน เช่น {"dy": "dy_ex"} ตอนเปิด "ไม่นับปันผลพิเศษ\""""
    alt = alt or {}
    for g in preset["gates"]:
        if fin and g.get("skip_fin"):
            continue
        v = m.get(alt.get(g["metric"], g["metric"]))
        if v is None:
            return False
        ok = {">=": v >= g["value"], "<=": v <= g["value"], ">": v > g["value"], "<": v < g["value"]}[g["op"]]
        if not ok:
            return False
    return True
