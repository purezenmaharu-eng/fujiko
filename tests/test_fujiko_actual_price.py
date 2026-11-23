"""fujiko.py の資金管理バックテストが、購入可否を当時の実株価(生の始値×分割比の累積)で判定することのテスト。
ネットワークには触れない(yfinance は偽物)。fujiko.py は読み込み時に初期化が走るため、必要な関数だけをASTで取り出す。
"""
import ast
import os
import sys
import unittest
from unittest import mock

import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import sizing  # noqa: E402

FUJIKO_PATH = os.path.join(HERE, "fujiko.py")


def _source():
    with open(FUJIKO_PATH, encoding="utf-8") as f:
        return f.read()


def _load_fetch(fake_yf):
    nodes = [n for n in ast.parse(_source()).body if isinstance(n, ast.FunctionDef) and n.name == "fetch_raw_opens"]
    ns = {"yf": fake_yf, "pd": pd}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns["fetch_raw_opens"]


def _raw_download(tickers):
    """yfinance(group_by="ticker")の返り値の形: 列が (ticker, 項目) の MultiIndex。B は全部欠損(取れなかった銘柄)"""
    idx = pd.bdate_range("2024-01-01", periods=3)
    cols, data = [], {}
    for t in tickers:
        for k in ("Open", "Stock Splits"):
            cols.append((t, k))
        opn = [float("nan")] * 3 if t == "B" else [800.0, 800.0, 800.0]
        data[(t, "Open")] = opn
        data[(t, "Stock Splits")] = [0.0, 0.0, 5.0] if t == "A" else [0.0, 0.0, 0.0]   # A は最終日に1→5分割
    return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_tuples(cols))


class TestFujikoActualPrice(unittest.TestCase):
    def test_fetch_raw_opens_skips_failed_ticker(self):
        fake = mock.Mock()
        fake.download.side_effect = lambda part, **kw: _raw_download(part)
        out = _load_fetch(fake)(["A", "B"], "2023-01-01", "2026-11-12")
        self.assertEqual(list(out), ["A"])                      # 全部欠損のBは含めない
        kw = fake.download.call_args.kwargs
        self.assertFalse(kw["auto_adjust"])                      # 調整なしで取る
        self.assertTrue(kw["actions"])                           # 分割を取る

    def test_actual_price_reaches_simulate_portfolio(self):
        # 調整済みの始値800円・最終日に1→5分割 → 分割前のエントリーの当時の実株価は 800×5=4000円
        # 調整済み800円なら100株=8万円で買えるが、実株価4000円では100株=40万円 > 資金100万の20%(20万円) → 見送り
        fake = mock.Mock()
        fake.download.side_effect = lambda part, **kw: _raw_download(part)
        raw = _load_fetch(fake)(["A"], "2023-01-01", "2026-11-12")
        idx = pd.bdate_range("2024-01-01", periods=3)
        base = {"ticker": "A", "entry_date": idx[0], "exit_date": idx[2], "entry_price": 800.0,
                "stop_price": 760.0, "pnl_pct": 0.0}
        closes = {"A": pd.Series([800.0] * 3, index=idx)}
        trades = [dict(base)]
        self.assertEqual(sizing.attach_actual_entry_prices(trades, raw), 1)
        self.assertAlmostEqual(trades[0]["actual_entry_price"], 4000.0)
        self.assertEqual(sizing.simulate_portfolio(trades, closes, 1_000_000)["n_trades"], 0)       # 修正後: 見送り
        self.assertEqual(sizing.simulate_portfolio([dict(base)], closes, 1_000_000)["n_trades"], 1)  # 付けなければ従来どおり買える

    def test_main_attaches_before_simulating(self):
        src = _source()
        i_attach = src.index("sizing.attach_actual_entry_prices(trs, _raw_opens)")
        i_sim = src.index("sizing.simulate_portfolio(bt_trades[col]")
        self.assertLess(i_attach, i_sim)                         # simulate_portfolio へ渡す前に実株価を付ける


if __name__ == "__main__":
    unittest.main()
