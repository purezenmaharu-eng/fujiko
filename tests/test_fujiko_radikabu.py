"""fujiko.py のラジ株ナビ予算・空キー安全策のテスト(ネットワーク・シート・LINEはモック)

fujiko.py はimportすると全処理が走るスクリプトのため、必要な定数・関数だけをASTで取り出して検証する。
"""
import ast
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

import requests

FUJIKO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fujiko.py")
WANTED_VARS = {
    "RADIKABUNAVI_RUN_BUDGET", "_radikabunavi_call_count", "FILTER_UNAPPLIED_NOTE",
    "_filter_unapplied", "RADIKABUNAVI_KEY_EMPTY_WARNING", "FUNDAMENTAL_SCREEN_MAX_PAGES",
    "FUJIKO_RADIKABUNAVI_DAILY_BUDGET",
}
WANTED_FUNCS = {"check_radikabu_key_at_start", "radikabunavi_call_tool"}


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
    ns = {"json": json, "requests": requests, "os": os}
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
        self.assertEqual(self.ns["RADIKABUNAVI_RUN_BUDGET"], 40)
        self.assertEqual(self.ns["FUNDAMENTAL_SCREEN_MAX_PAGES"], 10)
        self.assertEqual(self.ns["FUJIKO_RADIKABUNAVI_DAILY_BUDGET"], 15)

    def test_does_not_exceed_budget_across_windows_a_and_b(self):
        for i in range(30):  # 窓口B
            self.call("screen_stocks", {"offset": i})
        for i in range(30):  # 窓口A
            self.call("get_stock_score", {"code": str(i)})
        self.assertEqual(self.request.call_count, 40)
        self.assertEqual(self.ns["_radikabunavi_call_count"], 40)

    def test_logs_skip_when_budget_exhausted(self):
        self.ns["_radikabunavi_call_count"] = 40
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


class TestEmptyKeySafety(_Base):
    def test_warning_and_flag_when_key_empty(self):
        self.ns["RADIKABUNAVI_API_KEY"] = ""
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(self.ns["check_radikabu_key_at_start"]())
        self.assertIn("ラジ株ナビのキーが空です。ファンダメンタルズ事前フィルタは適用されません", buf.getvalue())
        self.assertTrue(self.ns["_filter_unapplied"])
        self.assertEqual(self.ns["FILTER_UNAPPLIED_NOTE"], "※事前フィルタ未適用")

    def test_no_warning_when_key_set(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertFalse(self.ns["check_radikabu_key_at_start"]())
        self.assertEqual(buf.getvalue(), "")
        self.assertFalse(self.ns["_filter_unapplied"])

    def test_note_is_attached_to_line_message_and_summary_row(self):
        # 出力組み立てはモジュール直下のため、フラグ連動の記述が両出力にあることをソースで確認する
        self.assertIn('msg = f"{FILTER_UNAPPLIED_NOTE}\\n" + msg', self.source)
        self.assertIn("_summary_row.append(FILTER_UNAPPLIED_NOTE)", self.source)


if __name__ == "__main__":
    unittest.main()
