"""fujiko.py のラジ株ナビ(任意の補助)まわりのテスト(ネットワーク・シート・LINEはモック)

fujiko.py はimportすると全処理が走るスクリプトのため、必要な定数・関数だけをASTで取り出して検証する。
"""
import ast
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

import numpy as np
import pandas as pd
import requests

FUJIKO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fujiko.py")
WANTED_VARS = {
    "RADIKABUNAVI_RUN_BUDGET", "_radikabunavi_call_count", "FUNDAMENTAL_SCREEN_MAX_PAGES",
    "FUJIKO_RADIKABUNAVI_DAILY_BUDGET",
}
WANTED_FUNCS = {"radikabunavi_call_tool", "select_deep_dive_tickers", "calc_signals"}


def _load_namespace():
    with open(FUJIKO_PATH, encoding="utf-8") as f:
        source = f.read()
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in WANTED_FUNCS:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in WANTED_VARS for t in node.targets):
            nodes.append(node)
    ns = {"json": json, "requests": requests, "os": os, "pd": pd, "np": np}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns, source


def _text_response(payload):
    return {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.ns, self.source = _load_namespace()
        self.request = mock.Mock(return_value=_text_response({"ok": True}))
        self.ns.update(
            SKIP_FUNDAMENTALS=False,
            RADIKABUNAVI_API_KEY="test-key",
            _radikabunavi_disabled=False,
            _cache_read=mock.Mock(return_value=None),
            _cache_write=mock.Mock(),
            _radikabunavi_ensure_session=mock.Mock(),
            _radikabunavi_request=self.request,
            radikabu_usage_logger=mock.Mock(),
        )

    def call(self, tool="get_stock_score", args=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = self.ns["radikabunavi_call_tool"](tool, args or {})
        return result, buf.getvalue()


class TestRunBudget(_Base):
    def test_defaults(self):
        self.assertEqual(self.ns["RADIKABUNAVI_RUN_BUDGET"], 10)
        self.assertEqual(self.ns["FUNDAMENTAL_SCREEN_MAX_PAGES"], 10)
        self.assertEqual(self.ns["FUJIKO_RADIKABUNAVI_DAILY_BUDGET"], 5)

    def test_does_not_exceed_budget_across_windows_a_and_b(self):
        for i in range(30):  # 窓口B
            self.call("screen_stocks", {"offset": i})
        for i in range(30):  # 窓口A
            self.call("get_stock_score", {"code": str(i)})
        self.assertEqual(self.request.call_count, 10)
        self.assertEqual(self.ns["_radikabunavi_call_count"], 10)

    def test_logs_skip_when_budget_exhausted(self):
        self.ns["_radikabunavi_call_count"] = 10
        result, out = self.call()
        self.assertIsNone(result)
        self.assertIn("予算上限に達したため省略", out)
        self.request.assert_not_called()

    def test_cache_hits_are_not_counted(self):
        self.ns["_cache_read"].return_value = {"cached": True}
        for i in range(100):
            result, _ = self.call("get_stock_score", {"code": str(i)})
            self.assertEqual(result, {"cached": True})
        self.assertEqual(self.ns["_radikabunavi_call_count"], 0)
        self.request.assert_not_called()


class TestAuthErrorStillStops(_Base):
    def _http_error(self, status):
        resp = mock.Mock()
        resp.status_code = status
        return requests.exceptions.HTTPError(response=resp)

    def test_401_disables_remaining_calls(self):
        self.request.side_effect = self._http_error(401)
        result, out = self.call()
        self.assertIsNone(result)
        self.assertIn("認証エラー", out)
        self.assertTrue(self.ns["_radikabunavi_disabled"])
        before = self.request.call_count
        self.call("get_edinet_financial_data", {"code": "7203"})
        self.assertEqual(self.request.call_count, before)

    def test_429_disables_remaining_calls(self):
        self.request.side_effect = self._http_error(429)
        self.call()
        self.assertTrue(self.ns["_radikabunavi_disabled"])


class TestEmptyKeyKeepsSignals(_Base):
    """ラジ株ナビは補助表示だけ。キーが空でもサインの判定・対象銘柄は変わらない"""

    def test_empty_key_makes_no_request_and_no_warning(self):
        self.ns["RADIKABUNAVI_API_KEY"] = ""
        result, out = self.call()
        self.assertIsNone(result)
        self.assertEqual(out, "")  # 警告を出さずに補助表示を空にする
        self.request.assert_not_called()

    def test_signals_are_computed_without_radikabunavi_key(self):
        self.ns["RADIKABUNAVI_API_KEY"] = ""
        n = 12
        df = pd.DataFrame({
            "Ticker": ["1111.T"] * n,
            "Close": [200.0] * n, "MA50": [150.0] * n, "MA150": [140.0] * n, "MA200": [130.0] * n,
            "MA200_is_rising": [True] * n, "MA50_is_rising": [True] * n,
            "Low52": [100.0] * n, "High52": [210.0] * n,
            "RSR": [50.0] + [90.0] * (n - 1),  # 2日目でAce条件を満たす
            "VolumeVCP": [1.5] * n, "BEP_bullish": [False] * n,
        })
        out = self.ns["calc_signals"](df)
        self.assertTrue(out["Ace_Start"].any())

    def test_no_fundamental_prefilter_narrows_target_stocks(self):
        tree = ast.parse(self.source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "screen_fundamentally_sound_stocks"]
        self.assertEqual(calls, [])  # 窓口Bの事前フィルタは呼ばれない
        self.assertNotIn("事前フィルタ未適用", self.source)

    def test_deep_dive_only_for_signal_tickers_up_to_budget(self):
        ranked = [(f"{1000 + i}.T", {}) for i in range(20)]
        signals = {f"{1000 + i}.T" for i in (3, 4, 5, 6, 7, 8, 9, 15)}
        picked = self.ns["select_deep_dive_tickers"](ranked, signals, self.ns["FUJIKO_RADIKABUNAVI_DAILY_BUDGET"])
        self.assertEqual(picked, ["1003.T", "1004.T", "1005.T", "1006.T", "1007.T"])
        self.assertEqual(self.ns["select_deep_dive_tickers"](ranked, set(), 5), [])


if __name__ == "__main__":
    unittest.main()
