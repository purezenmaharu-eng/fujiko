"""fujiko.py の「シート書き込み失敗でジョブを失敗にする」動きのテスト。
シート・API・ネットワークには触れない(偽物のgspreadだけを使う)。

fujiko.py はモジュール読み込み時に初期化が走るため、必要な定数・関数だけをASTで取り出して検証する。
"""
import ast
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

FUJIKO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fujiko.py")
WANTED_VARS = {"_tracking_failed", "_sheet_write_failed"}
WANTED_FUNCS = {"write_to_spreadsheet", "_exit_if_output_failed"}


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
    ns = {"os": os, "sys": sys}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns


class _Base(unittest.TestCase):
    def setUp(self):
        self.ns = _load_namespace()
        self.gspread = mock.Mock()
        self.ns.update(DRY_RUN=False, gspread=self.gspread, _load_sheets_credentials=mock.Mock())
        env = mock.patch.dict(os.environ, {"GOOGLE_SHEETS_CREDENTIALS": "dummy", "SPREADSHEET_ID": "dummy"})
        env.start()
        self.addCleanup(env.stop)

    def run_day(self, order):
        """日次スクリプトの末尾と同じ並び: 書き込み前の処理 → write_to_spreadsheet → 後の処理(HTML出力など) → 最後の判定。
        終了コード(正常終了=0)と出力を返す"""
        buf = io.StringIO()
        code = 0
        with redirect_stdout(buf):
            try:
                order.append("before")
                self.ns["write_to_spreadsheet"]("2026/11/06", ["7203.T"], {})
                order.append("after")
                self.ns["_exit_if_output_failed"]()
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()


class SheetWriteFailureExitTest(_Base):
    def test_write_exception_exits_1_after_other_work(self):
        # (a) 書き込みが例外 → 他の処理は最後まで実行され、そのあと終了コード1
        self.gspread.authorize.side_effect = RuntimeError("quota exceeded")
        order = []
        code, out = self.run_day(order)
        self.assertEqual(order, ["before", "after"])
        self.assertEqual(code, 1)
        self.assertTrue(self.ns["_sheet_write_failed"])
        self.assertIn("❌ スプレッドシート書き込み失敗: quota exceeded", out)
        self.assertIn("::error::スプレッドシート書き込み", out)
        self.assertNotIn("トラッキング", out)

    def test_write_ok_exits_0(self):
        # (b) 書き込みが成功 → 終了コード0のまま(フラグは立たない)。ここでは日次タブ処理を偽物に差し替えて成功させる
        self.ns["write_to_spreadsheet"] = lambda *a, **k: None
        order = []
        code, out = self.run_day(order)
        self.assertEqual(order, ["before", "after"])
        self.assertEqual(code, 0)
        self.assertFalse(self.ns["_sheet_write_failed"])
        self.assertNotIn("::error::", out)

    def test_missing_settings_is_not_a_failure(self):
        # 設定未完了は従来どおり警告だけ(失敗扱いにしない)
        with mock.patch.dict(os.environ, {"SPREADSHEET_ID": ""}):
            code, out = self.run_day([])
        self.assertEqual(code, 0)
        self.assertFalse(self.ns["_sheet_write_failed"])
        self.assertIn("スプレッドシート設定未完了", out)

    def test_dry_run_is_not_a_failure(self):
        self.ns["DRY_RUN"] = True
        self.ns.update(sizing_cells=lambda x: "", radi_record_cells=lambda v, t: "")
        code, _ = self.run_day([])
        self.assertEqual(code, 0)
        self.assertFalse(self.ns["_sheet_write_failed"])


class TrackingOnlyFailureTest(_Base):
    def test_tracking_only_failure_keeps_old_behavior(self):
        # (c) 追跡だけが失敗 → 従来どおり同じメッセージで終了コード1(シート書き込みのメッセージは出ない)
        self.ns["write_to_spreadsheet"] = lambda *a, **k: None
        self.ns["_tracking_failed"] = True
        code, out = self.run_day([])
        self.assertEqual(code, 1)
        self.assertIn("::error::シグナル的中率トラッキングが失敗しました", out)
        self.assertNotIn("スプレッドシート書き込み", out)

    def test_both_failures_report_both_causes(self):
        self.ns["write_to_spreadsheet"] = lambda *a, **k: None
        self.ns["_tracking_failed"] = True
        self.ns["_sheet_write_failed"] = True
        code, out = self.run_day([])
        self.assertEqual(code, 1)
        self.assertIn("::error::シグナル的中率トラッキングが失敗しました", out)
        self.assertIn("::error::スプレッドシート書き込み", out)


if __name__ == "__main__":
    unittest.main()
