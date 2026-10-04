"""sizing.py のテスト(株数の計算・100株単位の切り捨て・上限20%・ポートフォリオ再現)"""
import os
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sizing


class CalcPositionTest(unittest.TestCase):
    def test_basic_one_percent_risk(self):
        # 資金100万→許容損失1万円。損切り幅50円 → 200株(エントリー1000円→20万円=ちょうど上限20%)
        p = sizing.calc_position(1000, 950, 1_000_000)
        self.assertEqual(p["shares"], 200)
        self.assertEqual(p["loss"], 10_000)
        self.assertFalse(p["skip"])

    def test_rounds_down_to_100(self):
        # 10000/37 = 270.2株 → 200株
        p = sizing.calc_position(500, 463, 1_000_000)
        self.assertEqual(p["shares"], 200)
        self.assertEqual(p["loss"], 200 * 37)

    def test_below_100_is_skipped(self):
        # 10000/150 = 66株 → 見送り
        p = sizing.calc_position(2000, 1850, 1_000_000)
        self.assertTrue(p["skip"])
        self.assertEqual(p["shares"], 0)
        self.assertIn("100株", p["reason"])

    def test_cap_20_percent(self):
        # 損切り幅が小さく1%ルールだと1000株(100万円)になるが、上限20%=20万円=200株
        p = sizing.calc_position(1000, 990, 1_000_000)
        self.assertEqual(p["shares"], 200)
        self.assertTrue(p["capped"])
        self.assertLessEqual(p["amount"], 200_000)

    def test_cap_rounds_down_lot(self):
        # 上限20万円 ÷ 1300円 = 153株 → 100株
        p = sizing.calc_position(1300, 1299, 1_000_000)
        self.assertEqual(p["shares"], 100)

    def test_invalid_stop(self):
        self.assertTrue(sizing.calc_position(1000, 1000, 1_000_000)["skip"])
        self.assertTrue(sizing.calc_position(1000, 1100, 1_000_000)["skip"])
        self.assertTrue(sizing.calc_position(1000, float("nan"), 1_000_000)["skip"])
        self.assertTrue(sizing.calc_position(None, None, 1_000_000)["skip"])


class CapitalTest(unittest.TestCase):
    def test_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sizing.get_capital(), 1_000_000)

    def test_env_value_and_blank_secret(self):
        with mock.patch.dict(os.environ, {"FUJIKO_CAPITAL": "3,000,000"}):
            self.assertEqual(sizing.get_capital(), 3_000_000)
        for bad in ("", "abc", "0", "-5"):  # 未設定のSecretは空文字で入る
            with mock.patch.dict(os.environ, {"FUJIKO_CAPITAL": bad}):
                self.assertEqual(sizing.get_capital(), 1_000_000)


class SimulatePortfolioTest(unittest.TestCase):
    def _closes(self, ticker, start, prices):
        idx = pd.bdate_range(start, periods=len(prices))
        return pd.Series(prices, index=idx)

    def test_single_win_and_loss(self):
        c = self._closes("A", "2024-01-01", [1000] * 6)
        idx = c.index
        trades = [
            {"ticker": "A", "entry_date": idx[0], "exit_date": idx[2], "entry_price": 1000,
             "stop_price": 950, "pnl_pct": 10.0},   # 200株=20万円 → +2万円
            {"ticker": "A", "entry_date": idx[3], "exit_date": idx[5], "entry_price": 1000,
             "stop_price": 950, "pnl_pct": -5.0},   # 資金102万 → 204→200株 → -1万円
        ]
        r = sizing.simulate_portfolio(trades, {"A": c}, 1_000_000)
        self.assertEqual(r["n_trades"], 2)
        self.assertAlmostEqual(r["equity"].iloc[-1], 1_000_000 + 20_000 - 10_000, places=0)
        self.assertEqual(r["max_concurrent"], 1)
        self.assertLess(r["max_drawdown_pct"], 0)

    def test_overlap_same_ticker_skipped_and_concurrency(self):
        a = self._closes("A", "2024-01-01", [1000] * 6)
        b = self._closes("B", "2024-01-01", [1000] * 6)
        i = a.index
        trades = [
            {"ticker": "A", "entry_date": i[0], "exit_date": i[4], "entry_price": 1000, "stop_price": 950, "pnl_pct": 0.0},
            {"ticker": "A", "entry_date": i[1], "exit_date": i[3], "entry_price": 1000, "stop_price": 950, "pnl_pct": 0.0},
            {"ticker": "B", "entry_date": i[1], "exit_date": i[3], "entry_price": 1000, "stop_price": 950, "pnl_pct": 0.0},
        ]
        r = sizing.simulate_portfolio(trades, {"A": a, "B": b}, 1_000_000)
        self.assertEqual(r["n_trades"], 2)
        self.assertEqual(r["n_skipped"], 1)
        self.assertEqual(r["max_concurrent"], 2)

    def test_cash_limit_and_lot_skip(self):
        # 6銘柄が同日に20%ずつ買うと6本目は現金不足(100万の20%×5=100万で使い切り)
        idx = pd.bdate_range("2024-01-01", periods=3)
        closes = {f"T{k}": pd.Series([1000] * 3, index=idx) for k in range(6)}
        trades = [{"ticker": f"T{k}", "entry_date": idx[0], "exit_date": idx[2], "entry_price": 1000,
                   "stop_price": 950, "pnl_pct": 0.0} for k in range(6)]
        r = sizing.simulate_portfolio(trades, closes, 1_000_000)
        self.assertEqual(r["n_trades"], 5)
        self.assertEqual(r["max_concurrent"], 5)
        self.assertEqual(r["n_skipped"], 1)

    def test_no_trades(self):
        r = sizing.simulate_portfolio([], {}, 1_000_000)
        self.assertEqual(r["n_trades"], 0)


if __name__ == "__main__":
    unittest.main()
