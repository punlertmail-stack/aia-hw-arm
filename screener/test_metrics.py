"""ทดสอบสูตรใน metrics.py และตัวช่วยของตัวอ่าน ด้วยข้อมูลสมมติ (ไม่ต้องมีไฟล์ข้อมูลจริง)

รัน (จากโฟลเดอร์ repo):  python -m unittest discover -s screener -v
"""
import unittest

import metrics
import read_setsmart
import read_yahoo


def period(label, np, equity, prev, revenue=1000.0, div_paid=50.0, fcf=80.0, cfo=120.0):
    return {"label": label, "np": np, "equity": equity, "equity_prev": prev, "revenue": revenue,
            "gross_profit": revenue * 0.3, "normalized_np": np, "equity_total": equity,
            "ib_debt": equity * 0.5, "cur_assets": 300.0, "cur_liab": 200.0,
            "cfo": cfo, "fcf": fcf, "div_paid": div_paid}


def record(periods, mcap=2000.0):
    return {"ticker": "TEST", "name": "Test", "sector": "", "fin": False,
            "price": 10.0, "price_date": "2026-10-30", "mcap": mcap, "periods": periods, "dps": {}}


class TestHelpers(unittest.TestCase):
    def test_cagr(self):
        self.assertAlmostEqual(metrics.cagr(100, 133.1, 3), 10.0, places=6)
        self.assertIsNone(metrics.cagr(-100, 133.1, 3))   # ฐานติดลบ → คำนวณไม่ได้ ไม่เดา
        self.assertIsNone(metrics.cagr(None, 133.1, 3))

    def test_roe_uses_average_equity(self):
        self.assertAlmostEqual(metrics.roe({"np": 15, "equity": 110, "equity_prev": 90}), 15.0)
        self.assertAlmostEqual(metrics.roe({"np": 15, "equity": 100, "equity_prev": None}), 15.0)
        self.assertIsNone(metrics.roe({"np": 15, "equity": -5, "equity_prev": None}))

    def test_streak_counts_from_latest(self):
        self.assertEqual(metrics.streak([10, 0, 5, 5, 5]), 3)
        self.assertEqual(metrics.streak([5, 5, None]), 0)

    def test_last_n_refuses_gaps(self):
        self.assertIsNone(metrics.last_n([1, None, 3], 3))
        self.assertEqual(metrics.last_n([0, 1, 2, 3], 3), [1, 2, 3])


class TestCompute(unittest.TestCase):
    def setUp(self):
        ps = [period(f"FY{2021 + i}", np=100 * 1.1 ** i, equity=1000.0 + 100 * i, prev=900.0 + 100 * i,
                     revenue=1000 * 1.1 ** i) for i in range(5)]
        self.m = metrics.compute(record(ps))

    def test_valuation(self):
        np_last = 100 * 1.1 ** 4
        self.assertAlmostEqual(self.m["pe"], round(2000 / np_last, 2))
        self.assertAlmostEqual(self.m["pbv"], round(2000 / 1400, 2))
        self.assertAlmostEqual(self.m["dy"], 2.5)
        self.assertAlmostEqual(self.m["fcfy"], 4.0)

    def test_growth(self):
        self.assertAlmostEqual(self.m["rev_cagr3"], 10.0)
        self.assertAlmostEqual(self.m["np_cagr3"], 10.0)
        self.assertAlmostEqual(self.m["peg"], round(self.m["pe"] / 10.0, 2), places=1)

    def test_dividend(self):
        self.assertEqual(self.m["div_streak"], 5)
        self.assertAlmostEqual(self.m["fcf_cov3"], 1.6)
        self.assertAlmostEqual(self.m["payout"], round(50 / (100 * 1.1 ** 4) * 100, 2))

    def test_balance_sheet(self):
        self.assertAlmostEqual(self.m["de"], 0.5)
        self.assertAlmostEqual(self.m["cr"], 1.5)

    def test_loss_makes_pe_none(self):
        ps = [period(f"FY{2022 + i}", np=v, equity=1000.0, prev=1000.0) for i, v in enumerate([50, 60, -10])]
        m = metrics.compute(record(ps))
        self.assertIsNone(m["pe"])
        self.assertIsNone(m["payout"])
        self.assertIsNone(m["np_cagr3"])   # มีแค่ 3 ช่วง และกำไรล่าสุดติดลบ
        self.assertEqual(m["np_min3"], -10)

    def test_short_history_gives_none_not_guess(self):
        m = metrics.compute(record([period("FY2025", np=100.0, equity=1000.0, prev=None)]))
        self.assertIsNone(m["roe_avg3"])
        self.assertIsNone(m["rev_cagr3"])
        self.assertIsNotNone(m["pe"])


class TestGates(unittest.TestCase):
    PRESET = {"gates": [{"metric": "pe", "op": "<=", "value": 10},
                        {"metric": "de", "op": "<=", "value": 1.0, "skip_fin": True}]}

    def test_missing_metric_fails(self):
        self.assertFalse(metrics.passes({"pe": None, "de": 0.5}, False, self.PRESET))

    def test_financials_skip_debt_gate(self):
        self.assertFalse(metrics.passes({"pe": 8, "de": 5.0}, False, self.PRESET))
        self.assertTrue(metrics.passes({"pe": 8, "de": 5.0}, True, self.PRESET))


class TestReaders(unittest.TestCase):
    def test_col_index(self):
        self.assertEqual(read_setsmart.col_index("A1"), 0)
        self.assertEqual(read_setsmart.col_index("Z9"), 25)
        self.assertEqual(read_setsmart.col_index("AB12"), 27)

    def test_qnum_is_continuous(self):
        self.assertEqual(read_setsmart.qnum("2026Q1") - read_setsmart.qnum("2025Q4"), 1)
        self.assertEqual(read_setsmart.qnum("2026Q2") - read_setsmart.qnum("2025Q2"), 4)

    def test_setsmart_ttm_windows_one_year_apart(self):
        qs = [{"period": f"{y}Q{q}", "net_profit_parent_ttm": str(y * 10 + q), "equity_parent": "1000",
               "gross_profit": "5"}
              for y in range(2021, 2027) for q in range(1, 5) if (y, q) <= (2026, 2)]
        events = [("2025-05-01", 1.0), ("2025-09-01", 0.5), ("2026-04-01", 2.0), ("2026-07-01", 9.0)]
        px = {"price": 10.0, "price_date": "2026-10-30", "shares": 1e6, "name": "T", "sector": "",
              "dps": {}, "div_events": events}
        rec = read_setsmart.to_record("T", {"entity_type": "general"}, qs, px)
        self.assertEqual([p["label"] for p in rec["periods"]],
                         ["TTM 2022Q2", "TTM 2023Q2", "TTM 2024Q2", "TTM 2025Q2", "TTM 2026Q2"])
        # ช่วง 2025-07-01 ถึง 2026-06-30: XD 2025-09-01 (0.5) + 2026-04-01 (2.0) · 2026-07-01 อยู่นอกช่วง
        self.assertAlmostEqual(rec["periods"][-1]["div_paid"], 2.5)
        self.assertAlmostEqual(rec["periods"][-2]["div_paid"], 1.0)
        self.assertEqual(rec["periods"][-1]["gross_profit"], 20.0)
        self.assertFalse(rec["fin"])

    def test_yahoo_dividend_fallback_from_dps(self):
        d = {"ticker": "T", "price_pin_date": "2026-10-30",
             "info": {"sharesOutstanding": 2e6, "longName": "T", "sector": "Industrials"},
             "prices": [{"date": "2026-10-29", "close": 9.0}, {"date": "2026-10-30", "close": 10.0}],
             "dividends": [{"xd_date": "2025-04-01", "dps": 0.5}, {"xd_date": "2025-09-01", "dps": 0.25}],
             "income_stmt": {"2025-12-31": {"Net Income Common Stockholders": 5e6}},
             "balance_sheet": {"2025-12-31": {"Stockholders Equity": 50e6}}, "cashflow": {}}
        rec = read_yahoo.to_record(d)
        self.assertEqual(rec["mcap"], 20.0)
        self.assertAlmostEqual(rec["periods"][-1]["div_paid"], 1.5)   # 0.75 บาท × 2 ล้านหุ้น = 1.5 ลบ.


if __name__ == "__main__":
    unittest.main()
