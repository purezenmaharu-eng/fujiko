"""build_watchlist.py の空キー・取得失敗・日次上限まわりのテスト(ネットワークとスプレッドシートはモック)"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build_watchlist as bw  # noqa: E402


def _records(n):
    return [{"四半期": "2026-Q4", "銘柄コード": f"{1000 + i}", "済み": "FALSE"} for i in range(n)]


def _good_response():
    fy = {str(y): {"roa": 0.05, "cashFlowFromOperations": 10, "netIncome": 5,
                   "debtToEquityRatio": 0.5, "currentRatio": 1.5,
                   "sharesOutstanding": 100, "grossProfitMargin": 0.3, "assetTurnover": 0.8}
          for y in (2024, 2025)}
    text = json.dumps({"fiscalYears": fy})
    return {"result": {"content": [{"type": "text", "text": text}]}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.patches = [
            mock.patch.object(bw, "RADIKABUNAVI_API_KEY", "test-key"),
            mock.patch.object(bw, "_radikabunavi_disabled", False),
            mock.patch.object(bw, "_radikabunavi_call_count", 0),
            mock.patch.object(bw, "_radikabunavi_session_id", "sid"),
            mock.patch.object(bw, "_cache_read", return_value=None),
            mock.patch.object(bw, "_cache_write"),
            mock.patch.object(bw.radikabu_usage_logger, "log_radikabu_usage"),
            mock.patch.object(bw.time, "sleep"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)


class EmptyKeyTest(_Base):
    def test_empty_key_exits_nonzero_without_api_or_sheet(self):
        with mock.patch.object(bw, "RADIKABUNAVI_API_KEY", ""), \
                mock.patch.object(bw, "_radikabunavi_request") as req, \
                mock.patch.object(bw, "_open_spreadsheet") as open_sh:
            with self.assertRaises(SystemExit) as cm:
                bw.main()
        self.assertNotEqual(cm.exception.code, 0)
        req.assert_not_called()
        open_sh.assert_not_called()

    def test_call_tool_raises_when_key_empty(self):
        with mock.patch.object(bw, "RADIKABUNAVI_API_KEY", ""), \
                mock.patch.object(bw, "_radikabunavi_request") as req:
            with self.assertRaises(bw.RadikabuKeyMissingError):
                bw.radikabunavi_call_tool("get_edinet_financial_data", {"code": "1000"})
        req.assert_not_called()


class FailedFetchTest(_Base):
    def test_failed_fetch_stays_pending(self):
        records = _records(3)
        # 1件目: 通信エラー(未処理のまま)、2件目: 成功、3件目: APIエラー(未処理のまま)
        responses = [Exception("boom"), _good_response(), {"error": {"message": "x"}}]
        with mock.patch.object(bw, "_radikabunavi_ensure_session"), \
                mock.patch.object(bw, "_radikabunavi_request", side_effect=responses), \
                mock.patch.object(bw, "update_work_sheet_rows") as upd:
            bw.score_pending_candidates(mock.Mock(), records)
        updates = upd.call_args[0][2]
        self.assertEqual(set(updates.keys()), {1})
        for u in updates.values():
            self.assertNotIn("データ不足", u["判定"])

    def test_auth_error_still_disables_and_stops(self):
        import requests
        resp = mock.Mock(status_code=401)
        err = requests.exceptions.HTTPError(response=resp)
        with mock.patch.object(bw, "_radikabunavi_ensure_session"), \
                mock.patch.object(bw, "_radikabunavi_request", side_effect=err) as req, \
                mock.patch.object(bw, "update_work_sheet_rows") as upd:
            bw.score_pending_candidates(mock.Mock(), _records(5))
        self.assertEqual(req.call_count, 1)
        self.assertTrue(bw._radikabunavi_disabled)
        self.assertEqual(upd.call_args[0][2], {})
        bw._radikabunavi_disabled = False


class DailyLimitTest(_Base):
    def test_limit_is_50_and_derived(self):
        self.assertEqual(bw.RADIKABUNAVI_DAILY_LIMIT, 50)
        self.assertEqual(bw.MAX_FSCORE_TICKERS, bw.RADIKABUNAVI_DAILY_LIMIT // bw.CALLS_PER_TICKER)

    def test_does_not_call_more_than_limit(self):
        records = _records(120)
        with mock.patch.object(bw, "_radikabunavi_ensure_session"), \
                mock.patch.object(bw, "_radikabunavi_request", return_value=_good_response()) as req, \
                mock.patch.object(bw, "update_work_sheet_rows") as upd:
            bw.score_pending_candidates(mock.Mock(), records)
        self.assertEqual(req.call_count, 50)
        self.assertEqual(len(upd.call_args[0][2]), 50)


if __name__ == "__main__":
    unittest.main()
