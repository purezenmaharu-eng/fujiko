"""日次タブ・サマリーへの追記で、同じ日の再実行が重複を作らないことのテスト。
シート・API・ネットワークには触れない(偽物のシートだけを使う)。
fujiko.py はモジュール読み込み時に初期化が走るため、必要な関数だけをASTで取り出して検証する。
"""
import ast
import io
import os
import unittest
from contextlib import redirect_stdout

FUJIKO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fujiko.py")
WANTED_FUNCS = {"_drop_existing_rows", "_summary_row_exists", "_append_daily_rows"}


def _load_namespace():
    with open(FUJIKO_PATH, encoding="utf-8") as f:
        source = f.read()
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in WANTED_FUNCS]
    ns = {"_sheets_call_with_retry": lambda func, *a, **kw: func(*a, **kw)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns


NS = _load_namespace()
DAY = "2026/10/29"


def _rows(prefix, n):
    return [[DAY, f"{prefix}{i}", "プライム", "－"] for i in range(n)]


class FakeWs:
    def __init__(self, rows=()):
        self.rows = [list(r) for r in rows]
        self.append_calls = []

    def get_values(self, rng):
        return [r[:2] for r in self.rows]

    def append_rows(self, rows, value_input_option=None):
        assert rows, "空の append_rows は呼ばない"
        self.append_calls.append(len(rows))
        self.rows.extend(rows)


def _run(ws, rows, is_new=False):
    buf = io.StringIO()
    with redirect_stdout(buf):
        n = NS["_append_daily_rows"](ws, rows, is_new)
    return n, buf.getvalue()


class DailyRowsTest(unittest.TestCase):
    def test_empty_tab_gets_all_60(self):
        ws = FakeWs([["日付", "銘柄名"]])
        n, out = _run(ws, _rows("J", 60))
        self.assertEqual((n, ws.append_calls), (60, [60]))
        self.assertNotIn("重複スキップ", out)

    def test_new_sheet_appends_all_without_reading(self):
        ws = FakeWs()
        ws.get_values = lambda rng: self.fail("新規タブでは読まない")
        n, _ = _run(ws, _rows("J", 60), is_new=True)
        self.assertEqual(n, 60)

    def test_same_60_again_appends_nothing(self):
        ws = FakeWs([["日付", "銘柄名"]])
        rows = _rows("J", 60)
        _run(ws, rows)
        n, out = _run(ws, rows)
        self.assertEqual((n, ws.append_calls), (0, [60]))
        self.assertIn("重複スキップ: 60行", out)
        self.assertEqual(len(ws.rows), 61)

    def test_us_30_after_jp_30(self):
        ws = FakeWs([["日付", "銘柄名"]])
        _run(ws, _rows("J", 30))
        n, out = _run(ws, _rows("U", 30))
        self.assertEqual((n, ws.append_calls), (30, [30, 30]))
        self.assertNotIn("重複スキップ", out)

    def test_partial_tab_gets_only_missing(self):
        ws = FakeWs([["日付", "銘柄名"]] + _rows("J", 25))
        n, out = _run(ws, _rows("J", 60))
        self.assertEqual((n, ws.append_calls), (35, [35]))
        self.assertIn("重複スキップ: 25行", out)
        self.assertEqual(len(ws.rows), 61)

    def test_whitespace_is_ignored_and_other_date_is_not_duplicate(self):
        ws = FakeWs([[f" {DAY} ", " J0 "], ["2026/10/28", "J1"]])
        n, _ = _run(ws, [[DAY, "J0"], [DAY, "J1"]])
        self.assertEqual(n, 1)
        self.assertEqual(ws.rows[-1][:2], [DAY, "J1"])


class SummaryTest(unittest.TestCase):
    def test_same_date_and_market_exists(self):
        ex = [["日付", "Ace銘柄数", "King銘柄数", "ポリグラフ銘柄数", "Ace×BEP銘柄数", "市場"], [DAY, "3", "1", "2", "0", "JP"]]
        f = NS["_summary_row_exists"]
        self.assertTrue(f(ex, DAY, "JP"))
        self.assertFalse(f(ex, DAY, "US"))
        self.assertFalse(f(ex, "2026/10/30", "JP"))
        self.assertFalse(f([[DAY, "3", "1"]], DAY, "JP"))


if __name__ == "__main__":
    unittest.main()
