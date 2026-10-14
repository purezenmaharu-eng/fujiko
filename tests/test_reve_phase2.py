"""reve.py フェーズ2(履歴の蓄積・連続上昇の判定・LINE/シート用の整形)のテスト。ネットワークとシートはモック"""
import os
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reve  # noqa: E402


def pt(per_end, score, source="J-Quants", **kw):
    base = {"per_end": pd.Timestamp(per_end), "disc_date": None, "source": source, "rev_yoy": 0.3, "ni_yoy": 0.3,
            "fcf": 100.0, "fcf_yoy": 0.3, "roe": 12.0, "roe_chg": 2.0, "score": score}
    base.update(kw)
    return base


class StreakTest(unittest.TestCase):
    def test_counts_consecutive_quarterly_rises(self):
        pts = [pt("2025-03-31", 0.1), pt("2025-06-30", 0.3), pt("2025-09-30", 0.5), pt("2025-12-31", 0.9)]
        self.assertEqual(reve.count_streak(pts), 3)

    def test_a_fall_resets_the_streak(self):
        pts = [pt("2025-03-31", 0.1), pt("2025-06-30", 0.5), pt("2025-09-30", 0.4), pt("2025-12-31", 0.9)]
        self.assertEqual(reve.count_streak(pts), 1)

    def test_a_missing_quarter_breaks_the_streak(self):
        # 2025-06-30 が抜けている。間をまたいで「連続」と数えてはいけない
        pts = [pt("2025-03-31", 0.1), pt("2025-09-30", 0.5), pt("2025-12-31", 0.9)]
        self.assertEqual(reve.count_streak(pts), 1)

    def test_points_without_dates_still_work(self):
        self.assertEqual(reve.count_streak([{"score": 1}, {"score": 2}, {"score": 3}]), 2)


class MergeTest(unittest.TestCase):
    def test_stored_scores_fill_older_gaps_and_sort(self):
        fresh = [pt("2025-09-30", 0.5), pt("2025-12-31", 0.9)]
        merged = reve.merge_points(fresh, {"2025-03-31": 0.1, "2025-06-30": 0.3})
        self.assertEqual([p["per_end"].strftime("%Y-%m-%d") for p in merged],
                         ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"])
        self.assertEqual(reve.count_streak(merged), 3)

    def test_fresh_value_wins_over_stored_for_the_same_period(self):
        merged = reve.merge_points([pt("2025-12-31", 0.9)], {"2025-12-31": 5.0})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["score"], 0.9)

    def test_bad_stored_values_are_ignored(self):
        merged = reve.merge_points([pt("2025-12-31", 0.9)], {"not-a-date": 1.0, "2025-09-30": "x"})
        self.assertEqual(len(merged), 1)


class EvaluateWithHistoryTest(unittest.TestCase):
    def _run(self, stored):
        df = pd.DataFrame({"per_end": [pd.Timestamp("2025-12-31")]})
        points = [pt("2025-09-30", 1.2), pt("2025-12-31", 2.1)]
        close = pd.Series(range(300), index=pd.date_range("2024-01-01", periods=300))
        with mock.patch.object(reve, "fetch_jquants_statements", return_value=df), \
                mock.patch.object(reve, "score_points_from_jquants", return_value=points), \
                mock.patch.object(reve, "latest_point_from_yfinance", return_value=None), \
                mock.patch.object(reve, "eps_ttm_series", return_value=pd.DataFrame()), \
                mock.patch.object(reve, "multiple_expansion", return_value=(15.0, 1.5, True, True)):
            return reve.evaluate_reve("1234.T", "key", close, stored_history=stored)

    def test_without_history_the_result_is_provisional_and_unlabelled(self):
        r = self._run(None)
        self.assertEqual(r["streak"], 1)
        self.assertTrue(r["streak_provisional"])
        self.assertIsNone(r["label"])

    def test_history_lifts_the_streak_to_a_buy_candidate(self):
        stored = {"2025-03-31": 0.2, "2025-06-30": 0.6}
        r = self._run(stored)
        self.assertEqual(r["streak"], 3)
        self.assertEqual(r["label"], "監視候補")  # 3連続・スコア>=1.0・マルチプル拡大 → 監視候補(4連続で買い候補)
        r2 = self._run({"2024-12-31": 0.0, "2025-03-31": 0.2, "2025-06-30": 0.6})
        self.assertEqual(r2["streak"], 4)
        self.assertEqual(r2["label"], "強い買い候補")
        self.assertFalse(r2["streak_provisional"])


class _FakeWs:
    def __init__(self, records=None):
        self.records = records or []
        self.appended = []
        self.header = None

    def get_all_records(self):
        return self.records

    def append_rows(self, rows):
        self.appended += rows

    def append_row(self, row):
        self.header = row


class _FakeSh:
    def __init__(self, ws=None):
        self.ws = ws
        self.created = None

    def worksheet(self, name):
        if self.ws is None:
            raise RuntimeError("not found")
        return self.ws

    def add_worksheet(self, title, rows, cols):
        self.created = (title, rows, cols)
        self.ws = _FakeWs()
        return self.ws


def _result(history, **kw):
    r = {"label": None, "score": 1.0, "streak": 0, "streak_provisional": True, "history": history}
    r.update(kw)
    return r


class HistorySheetTest(unittest.TestCase):
    def test_load_history_parses_rows(self):
        ws = _FakeWs([{"銘柄": "1234.T", "決算期末": "2025-12-31", "スコア": 1.5, "ソース": "J-Quants", "記録日": "x"},
                      {"銘柄": "", "決算期末": "2025-12-31", "スコア": 1.5},
                      {"銘柄": "1234.T", "決算期末": "2025-09-30", "スコア": "bad"}])
        self.assertEqual(reve.load_history(_FakeSh(ws)), {"1234.T": {"2025-12-31": 1.5}})

    def test_load_history_without_the_sheet_is_empty(self):
        self.assertEqual(reve.load_history(_FakeSh(None)), {})

    def test_only_new_periods_are_written(self):
        res = {"1234.T": _result([{"per_end": "2025-09-30", "score": 1.0, "source": "J-Quants"},
                                  {"per_end": "2025-12-31", "score": 1.5, "source": "J-Quants"}])}
        rows = reve.new_history_rows(res, {"1234.T": {"2025-09-30": 1.0}}, "2026/10/15")
        self.assertEqual(rows, [["1234.T", "2025-12-31", 1.5, "J-Quants", "2026/10/15"]])

    def test_save_creates_the_sheet_with_header_and_appends(self):
        sh = _FakeSh(None)
        res = {"1234.T": _result([{"per_end": "2025-12-31", "score": 1.5, "source": "J-Quants"}])}
        n = reve.save_history(sh, res, {}, "2026/10/15", append_rows=lambda f, rows: f(rows))
        self.assertEqual(n, 1)
        self.assertEqual(sh.created[0], reve.HISTORY_SHEET_NAME)
        self.assertEqual(sh.ws.header, reve.HISTORY_HEADERS)
        self.assertEqual(len(sh.ws.appended), 1)

    def test_nothing_new_means_no_write(self):
        sh = _FakeSh(None)
        res = {"1234.T": _result([{"per_end": "2025-12-31", "score": 1.5, "source": "J-Quants"}])}
        self.assertEqual(reve.save_history(sh, res, {"1234.T": {"2025-12-31": 1.5}}, "2026/10/15"), 0)
        self.assertIsNone(sh.created)


class FormatTest(unittest.TestCase):
    def setUp(self):
        self.res = {
            "1111.T": _result([], label="買い候補", score=1.4, streak=4, streak_provisional=False),
            "2222.T": _result([], label="強い買い候補", score=2.3, streak=5, streak_provisional=False),
            "3333.T": _result([], label="監視候補", score=1.1, streak=2),
            "4444.T": _result([], label=None, score=0.2, streak=0),
        }
        self.names = {"1111.T": "A社", "2222.T": "B社", "3333.T": "C社", "4444.T": "D社"}

    def test_line_tag(self):
        self.assertEqual(reve.line_tag(self.res["1111.T"]), "REVE:買い候補(4Q)")
        self.assertEqual(reve.line_tag(self.res["3333.T"]), "REVE:監視候補(2Q*)")
        self.assertEqual(reve.line_tag(self.res["4444.T"]), "")
        self.assertEqual(reve.line_tag(None), "")

    def test_line_section_orders_strongest_first_and_skips_unlabelled(self):
        text = reve.line_section(self.res, self.names)
        lines = text.split("\n")
        self.assertIn("3銘柄", lines[0])
        self.assertTrue(lines[1].startswith("強い買い候補 B社"))
        self.assertTrue(lines[2].startswith("買い候補 A社"))
        self.assertTrue(lines[3].startswith("監視候補 C社"))
        self.assertNotIn("D社", text)

    def test_line_section_respects_the_limit(self):
        text = reve.line_section(self.res, self.names, limit=1)
        self.assertEqual(len(text.split("\n")), 2)
        self.assertIn("上位1件", text)

    def test_sheet_cells(self):
        self.assertEqual(reve.sheet_cells(None), ["－", "－", "－"])
        self.assertEqual(reve.sheet_cells(self.res["1111.T"]), ["買い候補", 1.4, 4])
        self.assertEqual(reve.sheet_cells(self.res["3333.T"]), ["監視候補(暫定)", 1.1, 2])
        self.assertEqual(reve.sheet_cells(self.res["4444.T"]), ["－", 0.2, 0])


if __name__ == "__main__":
    unittest.main()
