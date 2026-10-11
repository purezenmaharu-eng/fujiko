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


def _fy(**kw):
    base = {"roa": 0.05, "cashFlowFromOperations": 10, "netIncome": 5,
            "debtToEquityRatio": 0.5, "currentRatio": 1.5,
            "sharesOutstanding": 100, "grossProfitMargin": 0.3, "assetTurnover": 0.8}
    base.update(kw)
    return base


def _worse_prior():
    """前期が全指標で今期より悪い(ΔROA・DE・流動比率・粗利率・回転率がすべて改善扱い)"""
    return _fy(roa=0.01, debtToEquityRatio=0.9, currentRatio=1.0,
               grossProfitMargin=0.2, assetTurnover=0.5)


def _response(latest, prior=None):
    fy = {"2024": prior if prior is not None else _fy(), "2025": latest}
    text = json.dumps({"fiscalYears": fy})
    return {"result": {"content": [{"type": "text", "text": text}]}}


class ExcludedS33Test(unittest.TestCase):
    def test_excluded_codes_defined(self):
        self.assertEqual(bw.EXCLUDED_S33, {"7050", "7100", "7150", "7200", "8050"})

    def test_financial_and_realestate_removed(self):
        import pandas as pd
        df = pd.DataFrame({
            "Code": ["10010", "10020", "10030", "10040", "10050", "10060", "10070"],
            "CoName": ["銀行", "証券", "保険", "他金融", "不動産", "電気機器", "その他"],
            "S33": ["7050", "7100", "7150", "7200", "8050", "3650", "9999"],
            "MarketCodeName": ["プライム"] * 7,
        })
        client = mock.Mock()
        client.get_list.return_value = df
        with mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "k"}), \
                mock.patch.object(bw.jquantsapi, "ClientV2", return_value=client):
            tickers, names, _ = bw.get_all_tickers()
        self.assertEqual(tickers, ["1006.T"])
        self.assertEqual(names["1006.T"], "電気機器")


class ClassifyFScoreTest(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(bw.classify_f_score(4, 9), "不合格")
        self.assertEqual(bw.classify_f_score(5, 9), "次点")
        self.assertEqual(bw.classify_f_score(6, 9), "合格")
        self.assertEqual(bw.classify_f_score(9, 9), "合格")

    def test_reduced_denominator_uses_ratio(self):
        # 4/7 = 0.571 → 5/9(0.556)以上6/9(0.667)未満 → 次点
        self.assertEqual(bw.classify_f_score(4, 7), "次点")
        # 5/7 = 0.714 → 合格 / 3/7 = 0.429 → 不合格
        self.assertEqual(bw.classify_f_score(5, 7), "合格")
        self.assertEqual(bw.classify_f_score(3, 7), "不合格")
        # 2/3 = 6/9ちょうど → 合格(丸め誤差に左右されない)
        self.assertEqual(bw.classify_f_score(2, 3), "合格")

    def test_zero_denominator(self):
        self.assertEqual(bw.classify_f_score(0, 0), "不合格")


class NetIncomeTest(_Base):
    def _run(self, latest, prior=None):
        prior = prior if prior is not None else _worse_prior()
        records = _records(1)
        with mock.patch.object(bw, "_radikabunavi_ensure_session"), \
                mock.patch.object(bw, "_radikabunavi_request", return_value=_response(latest, prior)), \
                mock.patch.object(bw, "update_work_sheet_rows") as upd:
            bw.score_pending_candidates(mock.Mock(), records)
        return upd.call_args[0][2]

    def test_calc_f_score_returns_net_income(self):
        fin = json.loads(_response(_fy(netIncome=-3))["result"]["content"][0]["text"])
        self.assertEqual(bw.calc_f_score(fin)["net_income"], -3.0)
        fin = json.loads(_response(_fy(netIncome=None))["result"]["content"][0]["text"])
        self.assertIsNone(bw.calc_f_score(fin)["net_income"])

    def test_negative_net_income_fails_and_is_done(self):
        u = self._run(_fy(netIncome=-1))
        self.assertEqual(u[0]["判定"], "不合格(赤字)")
        self.assertEqual(u[0]["済み"], "TRUE")

    def test_zero_net_income_fails(self):
        u = self._run(_fy(netIncome=0))
        self.assertEqual(u[0]["判定"], "不合格(赤字)")

    def test_none_net_income_does_not_fail(self):
        u = self._run(_fy(netIncome=None))
        self.assertEqual(u[0]["判定"], "合格")

    def test_positive_net_income_passes(self):
        u = self._run(_fy(netIncome=5))
        self.assertEqual(u[0]["判定"], "合格")

    def test_loss_row_is_not_reprocessed(self):
        records = _records(1)
        with mock.patch.object(bw, "_radikabunavi_ensure_session"), \
                mock.patch.object(bw, "_radikabunavi_request", return_value=_response(_fy(netIncome=-1))) as req, \
                mock.patch.object(bw, "update_work_sheet_rows") as upd:
            bw.score_pending_candidates(mock.Mock(), records)
            # 「済み」を書き戻した状態で再実行しても、APIは呼ばれない
            records[0].update(upd.call_args[0][2][0])
            calls = req.call_count
            bw.score_pending_candidates(mock.Mock(), records)
        self.assertEqual(req.call_count, calls)

    def test_next_verdict_for_five_of_nine(self):
        # 9項目中5つ達成: ROA>0, CFO>0, ΔROA>0, CFO>NI, 希薄化なし。DE/流動比率/粗利率/回転率は悪化
        prior = _fy(roa=0.01, debtToEquityRatio=0.4, currentRatio=1.6,
                    grossProfitMargin=0.4, assetTurnover=0.9)
        u = self._run(_fy(), prior)
        self.assertEqual(u[0]["Fスコア"], "5/9")
        self.assertEqual(u[0]["判定"], "次点")


class SecondaryWatchlistTest(unittest.TestCase):
    def _records(self):
        base = {"市場": "プライム", "平均売買代金(百万円/日)": 500, "現在値": 1, "40週MA": 1, "3年前値": 1,
                "Fスコア": "x", "Fスコア達成率%": 1}
        return [
            dict(base, 銘柄コード="1001", 銘柄名="合格社", 判定="合格"),
            dict(base, 銘柄コード="1002", 銘柄名="次点社", 判定="次点"),
            dict(base, 銘柄コード="1003", 銘柄名="赤字社", 判定="不合格(赤字)"),
            dict(base, 銘柄コード="1004", 銘柄名="不合格社", 判定="不合格"),
        ]

    def _sheet(self):
        sheets = {}
        sh = mock.Mock()

        def worksheet(name):
            if name not in sheets:
                import gspread
                raise gspread.exceptions.WorksheetNotFound(name)
            return sheets[name]

        def add_worksheet(title, rows, cols):
            sheets[title] = mock.Mock()
            return sheets[title]

        sh.worksheet.side_effect = worksheet
        sh.add_worksheet.side_effect = add_worksheet
        return sh, sheets

    def _codes(self, ws):
        rows = ws.append_rows.call_args[0][0]
        return [r[1] for r in rows]

    def test_next_only_in_secondary_tab(self):
        sh, sheets = self._sheet()
        bw.write_secondary_watchlist(sh, self._records())
        self.assertEqual(set(sheets), {"監視銘柄_次点"})
        self.assertEqual(self._codes(sheets["監視銘柄_次点"]), ["1002"])

    def test_next_not_in_final_tab(self):
        sh, sheets = self._sheet()
        bw.write_final_watchlist(sh, self._records())
        self.assertEqual(set(sheets), {"監視銘柄"})
        self.assertEqual(self._codes(sheets["監視銘柄"]), ["1001"])

    def test_main_writes_both_when_all_done(self):
        recs = [dict(r, 四半期="2026-Q4", 済み="TRUE") for r in self._records()]
        sh = mock.Mock()
        with mock.patch.object(bw, "RADIKABUNAVI_API_KEY", "k"), \
                mock.patch.object(bw, "current_quarter_label", return_value="2026-Q4"), \
                mock.patch.object(bw, "_open_spreadsheet", return_value=sh), \
                mock.patch.object(bw, "read_work_sheet", return_value=(mock.Mock(), recs)), \
                mock.patch.object(bw, "score_pending_candidates", return_value=0), \
                mock.patch.object(bw, "write_final_watchlist") as wf, \
                mock.patch.object(bw, "write_secondary_watchlist") as ws2:
            bw.main()
        wf.assert_called_once()
        ws2.assert_called_once()


if __name__ == "__main__":
    unittest.main()
