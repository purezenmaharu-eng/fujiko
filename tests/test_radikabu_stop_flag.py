"""ラジ株ナビ「実行をまたぐ停止フラグ」のテスト(Sheets・ネットワークは全てモック。本物には書かない)"""
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
    "RADIKABU_STOP_SHEET", "RADIKABU_STOP_HEADERS", "RADIKABU_STOP_THRESHOLD",
    "_radikabu_stop_state", "RADIKABUNAVI_RUN_BUDGET", "_radikabunavi_call_count",
}
WANTED_FUNCS = {
    "_radikabu_stop_now_jst", "_radikabu_stop_fresh_state", "_radikabu_stop_get_ws",
    "_radikabu_stop_load", "_radikabu_stop_is_active", "_radikabu_stop_record", "radikabunavi_call_tool",
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
    ns = {"json": json, "requests": requests, "os": os}
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
        self.set_today("2026-10-25")
        self.ns.update(
            DRY_RUN=False,
            gspread=self.gspread,
            _load_sheets_credentials=mock.Mock(),
            _radikabu_stop_now_jst=lambda: self.now,
        )
        env = mock.patch.dict(os.environ, {"GOOGLE_SHEETS_CREDENTIALS": "x", "SPREADSHEET_ID": "y"})
        env.start()
        self.addCleanup(env.stop)

    def set_today(self, ymd):
        self.now.strftime.side_effect = lambda fmt: ymd if fmt == "%Y-%m-%d" else ymd + " 07:00:00"

    def quiet(self, name, *a):
        with redirect_stdout(io.StringIO()) as buf:
            r = self.ns[name](*a)
        return r, buf.getvalue()

    def written(self):
        return [c.args[0][0]["values"][0] for c in self.ws.batch_update.call_args_list]


class TestCounting(_Base):
    def test_three_429_across_runs_sets_flag(self):
        # 1回目の実行: 0 → 1
        self.quiet("_radikabu_stop_record", "429")
        self.assertEqual(self.written()[-1][:3], ["2026-10-25", 1, "FALSE"])
        # 別の実行: シートに「1回」が残っている状態から再開
        self.ns["_radikabu_stop_state"] = None
        self.ws.row_values.return_value = ["2026-10-25", "1", "FALSE", "t"]
        self.quiet("_radikabu_stop_record", "429")
        self.assertEqual(self.written()[-1][:3], ["2026-10-25", 2, "FALSE"])
        self.ns["_radikabu_stop_state"] = None
        self.ws.row_values.return_value = ["2026-10-25", "2", "FALSE", "t"]
        _, out = self.quiet("_radikabu_stop_record", "429")
        self.assertEqual(self.written()[-1][:3], ["2026-10-25", 3, "TRUE"])
        self.assertIn("停止します", out)
        self.assertTrue(self.ns["_radikabu_stop_is_active"]())

    def test_success_resets_count(self):
        self.ws.row_values.return_value = ["2026-10-25", "2", "FALSE", "t"]
        self.quiet("_radikabu_stop_record", "ok")
        self.assertEqual(self.written()[-1][:3], ["2026-10-25", 0, "FALSE"])

    def test_success_with_zero_count_writes_nothing(self):
        self.quiet("_radikabu_stop_record", "ok")
        self.ws.batch_update.assert_not_called()

    def test_date_change_resets_state(self):
        self.ws.row_values.return_value = ["2026-10-24", "3", "TRUE", "t"]
        self.assertFalse(self.ns["_radikabu_stop_is_active"]())
        self.quiet("_radikabu_stop_record", "429")
        self.assertEqual(self.written()[-1][:3], ["2026-10-25", 1, "FALSE"])

    def test_flag_from_sheet_is_active(self):
        self.ws.row_values.return_value = ["2026-10-25", "3", "TRUE", "t"]
        self.assertTrue(self.ns["_radikabu_stop_is_active"]())


class TestFailOpen(_Base):
    def test_unreadable_sheet_does_not_stop(self):
        self.sh.worksheet.side_effect = RuntimeError("boom")
        self.assertFalse(self.ns["_radikabu_stop_is_active"]())

    def test_row_read_error_does_not_stop(self):
        self.ws.row_values.side_effect = RuntimeError("boom")
        self.assertFalse(self.ns["_radikabu_stop_is_active"]())

    def test_write_error_does_not_raise(self):
        self.ws.batch_update.side_effect = RuntimeError("boom")
        self.quiet("_radikabu_stop_record", "429")  # 例外が出ないこと

    def test_no_credentials_does_not_stop(self):
        with mock.patch.dict(os.environ, {"SPREADSHEET_ID": ""}):
            self.assertFalse(self.ns["_radikabu_stop_is_active"]())

    def test_missing_tab_is_not_created_on_read(self):
        self.sh.worksheet.side_effect = _FakeNotFound()
        self.assertFalse(self.ns["_radikabu_stop_is_active"]())
        self.sh.add_worksheet.assert_not_called()

    def test_missing_tab_is_created_on_write(self):
        new_ws = mock.Mock()
        self.sh.worksheet.side_effect = _FakeNotFound()
        self.sh.add_worksheet.return_value = new_ws
        self.quiet("_radikabu_stop_record", "429")
        self.assertEqual(self.sh.add_worksheet.call_args.kwargs["title"], "ラジ株ナビ停止状態")
        new_ws.append_row.assert_called_once()
        new_ws.batch_update.assert_called_once()


class TestDryRun(_Base):
    def test_dry_run_never_writes(self):
        self.ns["DRY_RUN"] = True
        _, out = self.quiet("_radikabu_stop_record", "429")
        self.ws.batch_update.assert_not_called()
        self.sh.add_worksheet.assert_not_called()
        self.assertIn("DRY_RUN", out)


class TestCallTool(_Base):
    def setUp(self):
        super().setUp()
        self.request = mock.Mock(return_value={"result": {"content": [{"type": "text", "text": '{"ok": true}'}]}})
        self.ns.update(
            SKIP_FUNDAMENTALS=False, RADIKABUNAVI_API_KEY="k", _radikabunavi_disabled=False,
            _cache_read=mock.Mock(return_value=None), _cache_write=mock.Mock(),
            _radikabunavi_ensure_session=mock.Mock(), _radikabunavi_request=self.request,
            radikabu_usage_logger=mock.Mock(),
        )

    def call(self):
        with redirect_stdout(io.StringIO()):
            return self.ns["radikabunavi_call_tool"]("get_stock_score", {"code": "1"})

    def test_flag_skips_call_and_budget(self):
        self.ws.row_values.return_value = ["2026-10-25", "3", "TRUE", "t"]
        self.assertIsNone(self.call())
        self.request.assert_not_called()
        self.ns["radikabu_usage_logger"].log_radikabu_usage.assert_not_called()
        self.assertEqual(self.ns["_radikabunavi_call_count"], 0)

    def test_429_after_retries_records_and_disables(self):
        resp = mock.Mock(status_code=429)
        self.request.side_effect = requests.exceptions.HTTPError(response=resp)
        self.assertIsNone(self.call())
        self.assertTrue(self.ns["_radikabunavi_disabled"])
        self.assertEqual(self.written()[-1][:3], ["2026-10-25", 1, "FALSE"])

    def test_other_http_error_is_not_counted(self):
        resp = mock.Mock(status_code=500)
        self.request.side_effect = requests.exceptions.HTTPError(response=resp)
        self.call()
        self.ws.batch_update.assert_not_called()

    def test_success_resets_count_and_returns_data(self):
        self.ws.row_values.return_value = ["2026-10-25", "2", "FALSE", "t"]
        self.assertEqual(self.call(), {"ok": True})
        self.assertEqual(self.written()[-1][:3], ["2026-10-25", 0, "FALSE"])

    def test_run_budget_still_applies(self):
        self.ns["_radikabunavi_call_count"] = self.ns["RADIKABUNAVI_RUN_BUDGET"]
        self.assertIsNone(self.call())
        self.request.assert_not_called()

    def test_sheet_failure_keeps_calling(self):
        self.sh.worksheet.side_effect = RuntimeError("boom")
        self.assertEqual(self.call(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
