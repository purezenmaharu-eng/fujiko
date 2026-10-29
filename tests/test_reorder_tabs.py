"""reorder_tabs.py のテスト。シート・ネットワークには触れない(偽物のシートを使う)。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reorder_tabs as rt


class FakeWs:
    def __init__(self, title):
        self.title = title


class TestReorder(unittest.TestCase):
    def test_is_date_tab(self):
        self.assertTrue(rt.is_date_tab("2026-10-29"))
        for t in ("追跡", "サマリー", "2026/10/29", "2026-10-2"):
            self.assertFalse(rt.is_date_tab(t))

    def test_new_index_after_leading_non_date(self):
        self.assertEqual(rt.new_date_tab_index(["追跡", "2026-10-28", "2026-10-27"]), 1)
        self.assertEqual(rt.new_date_tab_index(["2026-10-28"]), 0)
        self.assertEqual(rt.new_date_tab_index(["追跡", "サマリー"]), 2)

    def test_planned_order_keeps_non_date_in_place(self):
        titles = ["追跡", "2026-10-27", "サマリー", "2026-10-29", "2026-10-28", "監視銘柄"]
        self.assertEqual(rt.planned_order(titles),
                         ["追跡", "2026-10-29", "サマリー", "2026-10-28", "2026-10-27", "監視銘柄"])

    def test_already_sorted_unchanged(self):
        t = ["追跡", "2026-10-29", "2026-10-28"]
        self.assertEqual(rt.planned_order(t), t)

    def _run(self, titles, argv):
        sh = mock.Mock()
        sh.worksheets.return_value = [FakeWs(t) for t in titles]
        fake_gspread = mock.Mock()
        fake_gspread.authorize.return_value.open_by_key.return_value = sh
        fake_fujiko = mock.Mock()
        env = {"GOOGLE_SHEETS_CREDENTIALS": "x", "SPREADSHEET_ID": "y"}
        with mock.patch.dict(sys.modules, {"gspread": fake_gspread, "fujiko": fake_fujiko}), \
                mock.patch.dict(os.environ, env):
            rt.main(argv)
        return sh

    def test_dry_run_does_not_reorder(self):
        sh = self._run(["2026-10-27", "2026-10-28"], [])
        sh.reorder_worksheets.assert_not_called()

    def test_apply_reorders(self):
        sh = self._run(["追跡", "2026-10-27", "2026-10-28"], ["--apply"])
        got = [w.title for w in sh.reorder_worksheets.call_args[0][0]]
        self.assertEqual(got, ["追跡", "2026-10-28", "2026-10-27"])


if __name__ == "__main__":
    unittest.main()
