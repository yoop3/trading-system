"""
test_detectors.py — Unit tests สำหรับ SMC detectors แต่ละตัว (ดู SMC_Agent_Spec.md)
ใช้ synthetic OHLCV data ที่ออกแบบมาให้ trigger/ไม่ trigger แต่ละ pattern โดยเฉพาะ
"""

import unittest
from datetime import datetime, timezone

import pandas as pd

from agents.smc_agent.detectors.fvg import detect_htf_fvg, price_in_active_fvg
from agents.smc_agent.detectors.liquidity import detect_htf_liquidity
from agents.smc_agent.detectors.session import in_killzone, get_session_name
from agents.smc_agent.detectors.stop_hunt import detect_stop_hunt
from agents.smc_agent.detectors.displacement import detect_displacement
from agents.smc_agent.detectors.order_block import detect_order_block
from agents.smc_agent.detectors.entry import check_entry
from agents.smc_agent.detectors.scoring import calculate_score


def make_df(rows: list[dict]) -> pd.DataFrame:
    """สร้าง OHLCV DataFrame จาก list ของ dict {open,high,low,close} + DatetimeIndex"""
    df = pd.DataFrame(rows)
    if "volume" not in df.columns:
        df["volume"] = 1000.0
    df.index = pd.date_range("2024-01-15", periods=len(df), freq="1h")
    return df


class TestFVG(unittest.TestCase):
    def test_bullish_fvg_active(self):
        df = make_df([
            {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2},
            {"open": 100.2, "high": 100.6, "low": 100.0, "close": 100.4},
            {"open": 103.0, "high": 103.5, "low": 103.0, "close": 103.3},  # gap vs bar0.high(100.5) = 2.5
            {"open": 102.5, "high": 103.0, "low": 102.0, "close": 102.8},  # retrace, fill 40%
            {"open": 102.8, "high": 103.2, "low": 102.5, "close": 103.0},
        ])
        fvgs = detect_htf_fvg(df, min_gap=2.0, max_fill_pct=75.0)
        self.assertEqual(len(fvgs), 1)
        fvg = fvgs[0]
        self.assertEqual(fvg["type"], "BULL")
        self.assertEqual(fvg["top"], 103.0)
        self.assertEqual(fvg["bottom"], 100.5)
        self.assertTrue(fvg["active"])
        self.assertEqual(fvg["fill_pct"], 40.0)

    def test_bullish_fvg_invalidated(self):
        df = make_df([
            {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2},
            {"open": 100.2, "high": 100.6, "low": 100.0, "close": 100.4},
            {"open": 103.0, "high": 103.5, "low": 103.0, "close": 103.3},  # FVG top=103 bottom=100.5
            {"open": 102.5, "high": 103.0, "low": 101.0, "close": 101.5},
            {"open": 101.5, "high": 102.0, "low": 100.5, "close": 101.0},  # fill 100% -> invalid
        ])
        fvgs = detect_htf_fvg(df, min_gap=2.0, max_fill_pct=75.0)
        self.assertEqual(len(fvgs), 1)
        fvg = fvgs[0]
        self.assertEqual(fvg["type"], "BULL")
        self.assertFalse(fvg["active"])
        self.assertEqual(fvg["fill_pct"], 100.0)

    def test_bearish_fvg_active(self):
        df = make_df([
            {"open": 110, "high": 112, "low": 110, "close": 111},
            {"open": 110, "high": 111, "low": 109, "close": 110},
            {"open": 107, "high": 108, "low": 106, "close": 107},  # gap vs bar0.low(110) = 2
            {"open": 107, "high": 107.5, "low": 105.5, "close": 106},
            {"open": 106, "high": 107, "low": 105, "close": 105.5},
            {"open": 105.5, "high": 106.5, "low": 104.5, "close": 105},
            {"open": 105, "high": 106, "low": 104, "close": 104.5},
            {"open": 104.5, "high": 105.5, "low": 103.5, "close": 104},
        ])
        fvgs = detect_htf_fvg(df, min_gap=2.0, max_fill_pct=75.0)
        self.assertEqual(len(fvgs), 1)
        fvg = fvgs[0]
        self.assertEqual(fvg["type"], "BEAR")
        self.assertEqual(fvg["top"], 110)
        self.assertEqual(fvg["bottom"], 108)
        self.assertTrue(fvg["active"])

    def test_no_fvg(self):
        df = make_df([
            {"open": 100, "high": 101, "low": 99, "close": 100.5},
            {"open": 100.5, "high": 101.5, "low": 100, "close": 101},
            {"open": 101, "high": 102, "low": 100.5, "close": 101.5},
        ])
        fvgs = detect_htf_fvg(df, min_gap=2.0, max_fill_pct=75.0)
        self.assertEqual(fvgs, [])

    def test_price_in_active_fvg(self):
        fvgs = [
            {"type": "BULL", "top": 100.0, "bottom": 98.0, "active": True},
            {"type": "BULL", "top": 90.0, "bottom": 88.0, "active": False},
        ]
        self.assertTrue(price_in_active_fvg(99.0, fvgs, "BULL"))
        self.assertFalse(price_in_active_fvg(89.0, fvgs, "BULL"))   # inactive FVG
        self.assertFalse(price_in_active_fvg(99.0, fvgs, "BEAR"))   # wrong type


class TestLiquidity(unittest.TestCase):
    def test_bsl_ssl_detection(self):
        df = make_df([
            {"open": 100, "high": 105, "low": 95, "close": 100},
            {"open": 100, "high": 108.00, "low": 92.00, "close": 100},
            {"open": 100, "high": 108.05, "low": 92.03, "close": 100},  # equal high/low cluster
            {"open": 100, "high": 102, "low": 97, "close": 100},
            {"open": 100, "high": 103, "low": 96, "close": 100},
            {"open": 100, "high": 120, "low": 80, "close": 100},        # extremes
        ])
        result = detect_htf_liquidity(df, current_price=100.0, lookback=6, eq_threshold_pct=0.1)
        self.assertEqual(result["bsl"], 108.0)
        self.assertEqual(result["bsl2"], 108.05)
        self.assertEqual(result["ssl"], 92.03)
        self.assertEqual(result["ssl2"], 92.0)

    def test_no_liquidity_above_or_below(self):
        df = make_df([
            {"open": 100, "high": 105, "low": 95, "close": 100},
            {"open": 100, "high": 106, "low": 94, "close": 100},
        ])
        # current_price สูงกว่า high ทั้งหมด และต่ำกว่า low ทั้งหมดไม่ได้พร้อมกัน
        # ทดสอบกรณีไม่มี BSL เหนือราคา (ราคาสูงกว่า high ทุกแท่ง)
        result = detect_htf_liquidity(df, current_price=200.0, lookback=2)
        self.assertIsNone(result["bsl"])
        self.assertIsNone(result["bsl2"])


class TestSession(unittest.TestCase):
    def test_london_killzone(self):
        ts = datetime(2024, 1, 15, 8, 30, tzinfo=timezone.utc)
        self.assertTrue(in_killzone(ts))
        self.assertEqual(get_session_name(ts), "LONDON")

    def test_new_york_killzone(self):
        ts = datetime(2024, 1, 15, 13, 0, tzinfo=timezone.utc)
        self.assertTrue(in_killzone(ts))
        self.assertEqual(get_session_name(ts), "NEW_YORK")

    def test_outside_killzone(self):
        ts = datetime(2024, 1, 15, 18, 0, tzinfo=timezone.utc)
        self.assertFalse(in_killzone(ts))
        self.assertEqual(get_session_name(ts), "OUTSIDE")

    def test_killzone_boundary(self):
        # 10:00 = สิ้นสุด London (exclusive), ยังไม่ถึง NY (12:00)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        self.assertFalse(in_killzone(ts))
        self.assertEqual(get_session_name(ts), "OUTSIDE")

    def test_weekend_saturday_in_london_hours(self):
        # 2024-01-20 = วันเสาร์ ตรงกับช่วงเวลา London (08:00) แต่ตลาดจริงปิด -> ไม่ถือเป็น killzone
        ts = datetime(2024, 1, 20, 8, 0, tzinfo=timezone.utc)
        self.assertFalse(in_killzone(ts))
        self.assertEqual(get_session_name(ts), "WEEKEND")

    def test_weekend_sunday_in_new_york_hours(self):
        # 2024-01-21 = วันอาทิตย์ ตรงกับช่วงเวลา New York (13:00) แต่ตลาดจริงปิด -> ไม่ถือเป็น killzone
        ts = datetime(2024, 1, 21, 13, 0, tzinfo=timezone.utc)
        self.assertFalse(in_killzone(ts))
        self.assertEqual(get_session_name(ts), "WEEKEND")


class TestStopHunt(unittest.TestCase):
    def _base_rows(self):
        return [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.3},
            {"open": 100.3, "high": 101.0, "low": 100.0, "close": 100.6},
            {"open": 100.6, "high": 101.0, "low": 100.2, "close": 100.8},
            {"open": 100.8, "high": 101.2, "low": 100.5, "close": 101.0},
        ]

    def test_bullish_stop_hunt_detected(self):
        rows = self._base_rows() + [
            # เจาะ swingLow(100.0) แล้วปิดกลับขึ้น + เป็นแท่งเขียว
            {"open": 100.4, "high": 101.0, "low": 99.5, "close": 100.7},
        ]
        df = make_df(rows)
        active_fvgs = [{"type": "BULL", "top": 100.0, "bottom": 99.0, "active": True, "fill_pct": 0}]
        result = detect_stop_hunt(df, active_fvgs, lookback=3, scan_bars=4)
        self.assertTrue(result["detected"])
        self.assertEqual(result["type"], "BULL")
        self.assertEqual(result["bar_index"], 4)

    def test_bearish_stop_hunt_detected(self):
        rows = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 99.7},
            {"open": 99.7, "high": 100.0, "low": 99.0, "close": 99.4},
            {"open": 99.4, "high": 99.8, "low": 98.8, "close": 99.5},
            {"open": 99.5, "high": 99.7, "low": 99.2, "close": 99.3},
            # เจาะ swingHigh(100.0) แล้วปิดกลับลง + เป็นแท่งแดง
            {"open": 99.6, "high": 100.5, "low": 99.4, "close": 99.3},
        ]
        df = make_df(rows)
        active_fvgs = [{"type": "BEAR", "top": 101.0, "bottom": 100.0, "active": True, "fill_pct": 0}]
        result = detect_stop_hunt(df, active_fvgs, lookback=3, scan_bars=4)
        self.assertTrue(result["detected"])
        self.assertEqual(result["type"], "BEAR")
        self.assertEqual(result["bar_index"], 4)

    def test_no_stop_hunt_without_matching_fvg(self):
        rows = self._base_rows() + [
            {"open": 100.4, "high": 101.0, "low": 99.5, "close": 100.7},
        ]
        df = make_df(rows)
        result = detect_stop_hunt(df, active_fvgs=[], lookback=3, scan_bars=4)
        self.assertFalse(result["detected"])
        self.assertIsNone(result["type"])
        self.assertEqual(result["bar_index"], -1)


class TestDisplacement(unittest.TestCase):
    def test_bullish_displacement_with_msb(self):
        rows = [{"open": 100, "high": 100.5, "low": 99.5, "close": 100.1} for _ in range(10)]
        rows.append({"open": 100, "high": 102.2, "low": 99.9, "close": 102})  # big green, MSB
        df = make_df(rows)
        stop_hunt = {"detected": True, "type": "BULL", "bar_index": 9}
        result = detect_displacement(df, stop_hunt, max_bars_after=3, avg_body_bars=10)
        self.assertTrue(result["detected"])
        self.assertEqual(result["type"], "BULL")
        self.assertTrue(result["msb"])
        self.assertEqual(result["bar_index"], 10)

    def test_bearish_displacement_with_msb(self):
        rows = [{"open": 100, "high": 100.5, "low": 99.5, "close": 99.9} for _ in range(10)]
        rows.append({"open": 100, "high": 100.1, "low": 97.8, "close": 98})  # big red, MSB
        df = make_df(rows)
        stop_hunt = {"detected": True, "type": "BEAR", "bar_index": 9}
        result = detect_displacement(df, stop_hunt, max_bars_after=3, avg_body_bars=10)
        self.assertTrue(result["detected"])
        self.assertEqual(result["type"], "BEAR")
        self.assertTrue(result["msb"])
        self.assertEqual(result["bar_index"], 10)

    def test_no_displacement_when_no_stop_hunt(self):
        df = make_df([{"open": 100, "high": 100.5, "low": 99.5, "close": 100.1} for _ in range(5)])
        stop_hunt = {"detected": False, "type": None, "bar_index": -1}
        result = detect_displacement(df, stop_hunt)
        self.assertFalse(result["detected"])
        self.assertIsNone(result["type"])
        self.assertFalse(result["msb"])


class TestOrderBlock(unittest.TestCase):
    def test_bullish_ob_detected(self):
        df = make_df([
            {"open": 98.0, "high": 98.5, "low": 97.5, "close": 98.3},
            {"open": 98.3, "high": 98.8, "low": 98.0, "close": 98.6},
            {"open": 99.0, "high": 99.2, "low": 98.5, "close": 98.7},   # bearish -> OB
            {"open": 98.7, "high": 101.0, "low": 98.6, "close": 100.8},  # displacement bar
        ])
        displacement = {"detected": True, "type": "BULL", "bar_index": 3}
        ob = detect_order_block(df, displacement)
        self.assertIsNotNone(ob)
        self.assertEqual(ob["type"], "BULL")
        self.assertEqual(ob["top"], 99.0)
        self.assertEqual(ob["bottom"], 98.7)

    def test_bearish_ob_detected(self):
        df = make_df([
            {"open": 102.0, "high": 102.5, "low": 101.5, "close": 101.7},
            {"open": 101.7, "high": 102.0, "low": 101.4, "close": 101.5},
            {"open": 101.0, "high": 101.8, "low": 100.8, "close": 101.6},  # bullish -> OB
            {"open": 101.6, "high": 101.7, "low": 99.0, "close": 99.2},    # displacement bar
        ])
        displacement = {"detected": True, "type": "BEAR", "bar_index": 3}
        ob = detect_order_block(df, displacement)
        self.assertIsNotNone(ob)
        self.assertEqual(ob["type"], "BEAR")
        self.assertEqual(ob["top"], 101.6)
        self.assertEqual(ob["bottom"], 101.0)

    def test_no_ob_when_no_displacement(self):
        df = make_df([{"open": 100, "high": 101, "low": 99, "close": 100.5}])
        displacement = {"detected": False, "type": None, "bar_index": -1}
        self.assertIsNone(detect_order_block(df, displacement))

    def test_no_ob_when_no_opposite_candle(self):
        # ทุกแท่งก่อน displacement เป็นสีเดียวกัน (เขียว) -> ไม่มีแท่ง bearish ให้เป็น Bullish OB
        df = make_df([
            {"open": 98.0, "high": 98.5, "low": 97.5, "close": 98.3},
            {"open": 98.3, "high": 98.8, "low": 98.0, "close": 98.6},
            {"open": 98.6, "high": 99.0, "low": 98.4, "close": 98.9},
            {"open": 98.9, "high": 101.0, "low": 98.8, "close": 100.8},
        ])
        displacement = {"detected": True, "type": "BULL", "bar_index": 3}
        self.assertIsNone(detect_order_block(df, displacement))


class TestEntry(unittest.TestCase):
    def test_long_entry(self):
        df = make_df([{"open": 99.9, "high": 100.1, "low": 99.6, "close": 99.9}])
        ob = {"top": 100.0, "bottom": 99.7, "type": "BULL"}
        fvg_active = [{"type": "BULL", "top": 100.5, "bottom": 99.5, "active": True, "fill_pct": 10.0}]
        result = check_entry(df, ob, fvg_active)
        self.assertEqual(result["signal"], "LONG")

    def test_short_entry(self):
        df = make_df([{"open": 100.1, "high": 100.2, "low": 99.9, "close": 100.1}])
        ob = {"top": 100.3, "bottom": 100.0, "type": "BEAR"}
        fvg_active = [{"type": "BEAR", "top": 100.5, "bottom": 99.8, "active": True, "fill_pct": 5.0}]
        result = check_entry(df, ob, fvg_active)
        self.assertEqual(result["signal"], "SHORT")

    def test_no_setup_without_ob(self):
        df = make_df([{"open": 100, "high": 100.1, "low": 99.9, "close": 100}])
        self.assertEqual(check_entry(df, None, [])["signal"], "NO_SETUP")

    def test_no_setup_when_price_not_retraced(self):
        df = make_df([{"open": 99.9, "high": 100.0, "low": 99.8, "close": 99.6}])
        ob = {"top": 100.0, "bottom": 99.7, "type": "BULL"}
        fvg_active = [{"type": "BULL", "top": 100.5, "bottom": 99.5, "active": True, "fill_pct": 10.0}]
        result = check_entry(df, ob, fvg_active)
        self.assertEqual(result["signal"], "NO_SETUP")


class TestScoring(unittest.TestCase):
    def _criteria(self, met: int) -> dict:
        keys = ["htf_fvg", "session", "stop_hunt", "displacement", "ob_detected", "rr_ok"]
        return {k: (i < met) for i, k in enumerate(keys)}

    def test_full_score_long(self):
        result = calculate_score(self._criteria(6), "LONG")
        self.assertEqual(result, {"signal": "LONG", "score": 3, "confidence": 100.0})

    def test_five_of_six_short(self):
        result = calculate_score(self._criteria(5), "SHORT")
        self.assertEqual(result["signal"], "SHORT")
        self.assertEqual(result["score"], -2)
        self.assertAlmostEqual(result["confidence"], 83.3, places=1)

    def test_four_of_six_long(self):
        result = calculate_score(self._criteria(4), "LONG")
        self.assertEqual(result["score"], 1)
        self.assertEqual(result["signal"], "LONG")

    def test_below_threshold_is_no_setup(self):
        result = calculate_score(self._criteria(3), "LONG")
        self.assertEqual(result["signal"], "NO_SETUP")
        self.assertEqual(result["score"], 0)

    def test_entry_no_setup_overrides_high_criteria_count(self):
        result = calculate_score(self._criteria(6), "NO_SETUP")
        self.assertEqual(result["signal"], "NO_SETUP")
        self.assertEqual(result["score"], 0)


if __name__ == "__main__":
    unittest.main()
