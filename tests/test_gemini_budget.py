"""Gemini月額上限のテスト(Sheets・ネットワークは全てモック。本物には書かない)"""
import ast
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gemini_pricing  # noqa: E402

FUJIKO_PATH = os.path.join(ROOT, "fujiko.py")
WANTED_VARS = {
    "GEMINI_MODEL", "GEMINI_MONTHLY_CAP_YEN", "GEMINI_USAGE_SHEET", "GEMINI_USAGE_HEADERS",
    "_gemini_usage_state", "_gemini_disabled",
}
WANTED_FUNCS = {
    "_gemini_usage_now_jst", "_gemini_usage_fresh_state", "_gemini_usage_get_ws", "_gemini_usage_load",
    "_gemini_budget_exceeded", "_gemini_usage_add", "_gemini_usage_flush", "generate_gemini_commentary",
}


class _FakeNotFound(Exception):
    pass


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
    ns = {"json": json, "os": os, "gemini_pricing": gemini_pricing}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns


class _Base(unittest.TestCase):
    def setUp(self):
        self.ns = _load_namespace()
        self.ws = mock.Mock()
        self.ws.row_values.return_value = []
        self.gspread = mock.Mock()
        self.gspread.exceptions.WorksheetNotFound = _FakeNotFound
        self.sh = mock.Mock()
        self.sh.worksheet.return_value = self.ws
        self.gspread.authorize.return_value.open_by_key.return_value = self.sh
        self.now = mock.Mock()
        self.set_month("2026-11")
        self.post = mock.Mock()
        self.ns.update(
            DRY_RUN=False, gspread=self.gspread, _load_sheets_credentials=mock.Mock(),
            _gemini_usage_now_jst=lambda: self.now, GEMINI_API_KEY="k", SKIP_FUNDAMENTALS=False,
            _post_with_429_retry=self.post,
        )
        env = mock.patch.dict(os.environ, {"GOOGLE_SHEETS_CREDENTIALS": "x", "SPREADSHEET_ID": "y"})
        env.start()
        self.addCleanup(env.stop)

    def set_month(self, ym):
        self.now.strftime.side_effect = lambda fmt: ym if fmt == "%Y-%m" else ym + "-19 07:00:00"

    def quiet(self, name, *a):
        with redirect_stdout(io.StringIO()) as buf:
            r = self.ns[name](*a)
        return r, buf.getvalue()

    def ok_response(self, prompt=2000, cand=60, thoughts=0):
        r = mock.Mock(status_code=200)
        r.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "売上は増加。"}]}}],
            "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": cand, "thoughtsTokenCount": thoughts},
        }
        return r

    def commentary(self):
        return self.quiet("generate_gemini_commentary", "A社", "1234.T", {"x": 1}, None)


class TestCap(_Base):
    def test_under_cap_calls_gemini_and_counts(self):
        self.post.return_value = self.ok_response()
        text, _ = self.commentary()
        self.assertEqual(text, "売上は増加。")
        st = self.ns["_gemini_usage_state"]
        self.assertEqual((st["calls"], st["prompt"], st["output"]), (1, 2000, 60))
        self.assertAlmostEqual(st["cost_yen"], gemini_pricing.cost_yen(2000, 60, 0))

    def test_at_cap_skips_and_disables(self):
        self.ws.row_values.return_value = ["2026-11", "900", "1", "1", "100.0", "t"]
        text, out = self.commentary()
        self.assertEqual(text, "")
        self.post.assert_not_called()
        self.assertTrue(self.ns["_gemini_disabled"])
        self.assertIn("上限", out)

    def test_month_change_resets(self):
        self.ws.row_values.return_value = ["2026-10", "900", "1", "1", "999.0", "t"]
        self.post.return_value = self.ok_response()
        text, _ = self.commentary()
        self.assertEqual(text, "売上は増加。")
        self.assertEqual(self.ns["_gemini_usage_state"]["calls"], 1)

    def test_cap_reached_within_run(self):
        self.ws.row_values.return_value = ["2026-11", "5", "1", "1", "99.99", "t"]
        self.post.return_value = self.ok_response(prompt=1_000_000, cand=100_000)
        self.commentary()                      # 1回目は通る(上限未満だったため)
        self.post.reset_mock()
        text, _ = self.commentary()            # 2回目は上限超えで呼ばない
        self.assertEqual(text, "")
        self.post.assert_not_called()

    def test_thinking_tokens_counted_as_output(self):
        self.post.return_value = self.ok_response(prompt=0, cand=10, thoughts=90)
        self.commentary()
        self.assertEqual(self.ns["_gemini_usage_state"]["output"], 100)


class TestFlush(_Base):
    def test_flush_writes_once_with_totals(self):
        self.post.return_value = self.ok_response()
        self.commentary(); self.commentary()
        self.quiet("_gemini_usage_flush")
        self.assertEqual(self.ws.batch_update.call_count, 1)
        row = self.ws.batch_update.call_args.args[0][0]["values"][0]
        self.assertEqual(row[:4], ["2026-11", 2, 4000, 120])
        self.quiet("_gemini_usage_flush")  # dirtyが落ちているので2回目は書かない
        self.assertEqual(self.ws.batch_update.call_count, 1)

    def test_flush_without_usage_writes_nothing(self):
        self.quiet("_gemini_usage_flush")
        self.ws.batch_update.assert_not_called()

    def test_dry_run_does_not_write(self):
        self.ns["DRY_RUN"] = True
        self.post.return_value = self.ok_response()
        self.commentary()
        self.quiet("_gemini_usage_flush")
        self.ws.batch_update.assert_not_called()

    def test_missing_tab_created_on_write(self):
        new_ws = mock.Mock()
        self.sh.worksheet.side_effect = _FakeNotFound()
        self.sh.add_worksheet.return_value = new_ws
        self.post.return_value = self.ok_response()
        self.commentary()
        self.quiet("_gemini_usage_flush")
        self.assertEqual(self.sh.add_worksheet.call_args.kwargs["title"], "Gemini使用量")


class TestFailOpen(_Base):
    def test_unreadable_sheet_still_calls_gemini(self):
        self.sh.worksheet.side_effect = RuntimeError("boom")
        self.post.return_value = self.ok_response()
        text, _ = self.commentary()
        self.assertEqual(text, "売上は増加。")

    def test_write_error_does_not_raise(self):
        self.ws.batch_update.side_effect = RuntimeError("boom")
        self.post.return_value = self.ok_response()
        self.commentary()
        self.quiet("_gemini_usage_flush")

    def test_no_credentials_still_calls_gemini(self):
        with mock.patch.dict(os.environ, {"SPREADSHEET_ID": ""}):
            self.post.return_value = self.ok_response()
            text, _ = self.commentary()
        self.assertEqual(text, "売上は増加。")

    def test_missing_usage_metadata_does_not_raise(self):
        r = self.ok_response(); r.json.return_value.pop("usageMetadata")
        self.post.return_value = r
        text, _ = self.commentary()
        self.assertEqual(text, "売上は増加。")


if __name__ == "__main__":
    unittest.main()
