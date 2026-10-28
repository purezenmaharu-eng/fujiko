import os
import unittest

import numpy as np
import pandas as pd

import build_watchlist as bw
import walkforward as wf


def prices(n=1000, start=100.0, drift=0.0008, vol=2_000_000, end="2022-01-03", seed=0):
    idx = pd.bdate_range(end=pd.Timestamp(end) - pd.Timedelta(days=1), periods=n)
    close = pd.Series(start * np.exp(np.arange(n) * drift), index=idx)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": float(vol)})


def fy_rows(**over):
    return dict(over)


class RebalanceDates(unittest.TestCase):
    def test_first_trading_day_of_each_quarter(self):
        days = pd.bdate_range("2021-12-01", "2022-12-31")
        d = wf.rebalance_dates(days, "2022-01-01", pd.Timestamp("2022-12-31"))
        self.assertEqual([x.strftime("%m-%d") for x in d], ["01-03", "04-01", "07-01", "10-03"])


class FinData(unittest.TestCase):
    def rows(self):
        common = dict(ibd_current=100.0, ibd_noncurrent=100.0, split_adjustment_factor=1.0)
        return [
            fy_rows(submit_date="2021-06-24 15:00", treasury_shares_as_of_date="2021-03-31", revenue=1000, operating_income=100,
                    net_income=50, gross_profit=300, total_assets=1000, net_assets=400, total_liabilities=600,
                    current_assets=500, current_liabilities=400, cf_operating=80,
                    shares_issued_fiscal_year_end=100, treasury_shares_count=0, **common),
            fy_rows(submit_date="2022-06-23 15:00", treasury_shares_as_of_date="2022-03-31", revenue=1200, operating_income=140,
                    net_income=80, gross_profit=420, total_assets=1000, net_assets=500, total_liabilities=500,
                    current_assets=600, current_liabilities=400, cf_operating=120,
                    shares_issued_fiscal_year_end=100, treasury_shares_count=5,
                    **{**common, "ibd_current": 50.0, "ibd_noncurrent": 50.0}),
            # 同じ期の訂正(選定日より後に提出 → 先読みになるので使ってはいけない)
            fy_rows(submit_date="2022-08-01 09:00", treasury_shares_as_of_date="2022-03-31", revenue=1200, operating_income=140,
                    net_income=-999, total_assets=1000, net_assets=500, total_liabilities=500,
                    cf_operating=120, shares_issued_fiscal_year_end=100, treasury_shares_count=5, **common),
        ]

    def test_uses_only_filings_before_asof_and_passes_calc_f_score(self):
        fy = wf.parse_edinetdb_rows(self.rows())
        fin = wf.fin_data_asof(fy, "2022-07-01")
        latest = fin["fiscalYears"]["2022-03-31"]
        self.assertAlmostEqual(latest["roa"], 0.08)          # 訂正前のNP=80
        self.assertAlmostEqual(latest["currentRatio"], 1.5)
        self.assertAlmostEqual(latest["debtToEquityRatio"], 0.2)   # 有利子負債100/純資産500
        self.assertEqual(latest["sharesOutstanding"], 95)
        f = bw.calc_f_score(fin)
        self.assertEqual(f["maxScore"], 9)                   # 9項目すべて判定できる
        self.assertEqual(f["score"], 9)
        self.assertGreaterEqual(f["ratio"], bw.F_SCORE_RATIO_THRESHOLD)

    def test_split_does_not_look_like_dilution(self):
        rows = self.rows()[:2]
        rows[0]["split_adjustment_factor"] = 5.0             # 5分割前の期 → 株数を5倍して単位をそろえる
        rows[1]["shares_issued_fiscal_year_end"] = 500
        rows[1]["treasury_shares_count"] = 0
        rows[0]["shares_issued_fiscal_year_end"] = 100
        fin = wf.fin_data_asof(wf.parse_edinetdb_rows(rows), "2022-07-01")
        ys = fin["fiscalYears"]
        self.assertEqual(ys["2021-03-31"]["sharesOutstanding"], 500)
        self.assertLessEqual(ys["2022-03-31"]["sharesOutstanding"], ys["2021-03-31"]["sharesOutstanding"])

    def test_missing_treasury_compares_issued_only_and_missing_debt_uses_liabilities(self):
        rows = self.rows()[:2]
        rows[0]["treasury_shares_count"] = None
        for r in rows:
            r["ibd_current"] = r["ibd_noncurrent"] = None
        ys = wf.fin_data_asof(wf.parse_edinetdb_rows(rows), "2022-07-01")["fiscalYears"]
        self.assertEqual(ys["2022-03-31"]["sharesOutstanding"], 100)   # 自己株式は引かない
        self.assertAlmostEqual(ys["2022-03-31"]["debtToEquityRatio"], 1.0)   # 負債合計500/純資産500

    def test_before_second_filing_is_data_shortage(self):
        fy = wf.parse_edinetdb_rows(self.rows())
        self.assertIsNone(wf.fin_data_asof(fy, "2022-01-01"))   # 1期分しか提出されていない

    def test_stale_filing_is_data_shortage(self):
        fy = wf.parse_edinetdb_rows(self.rows())
        self.assertIsNone(wf.fin_data_asof(fy, "2024-06-01"))

    def test_empty(self):
        self.assertIsNone(wf.fin_data_asof(wf.parse_edinetdb_rows([]), "2022-01-01"))


class EdinetDbFetch(unittest.TestCase):
    def test_resumes_stops_at_limit_and_stops_when_remaining_low(self):
        import os
        import tempfile
        from unittest import mock

        calls = []

        def fake_get(path, params, key, state):
            state["calls"] += 1
            calls.append(path)
            state["remaining"] = 90 - len(calls)
            if path == "/companies":
                return {"data": [{"sec_code": "1111", "edinet_code": "E1"}, {"sec_code": "22220", "edinet_code": "E2"},
                                 {"sec_code": "3333", "edinet_code": "E3"}]}
            return {"data": [{"fiscal_year": 2021, "submit_date": "2021-06-01 10:00", "revenue": 1}]}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(wf, "EDB_DIR", tmp), \
                mock.patch.object(wf, "_edb_get", fake_get):
            tabs, missing = wf.fetch_fy_tables(["1111.T", "2222.T", "3333.T", "9999.T"], "k", daily_limit=3)
            self.assertEqual(calls, ["/companies", "/companies/E1/financials", "/companies/E2/financials"])
            self.assertEqual(missing, ["3333.T"])                  # 上限3回で止まる
            self.assertTrue(tabs["9999.T"].empty)                  # 変換表に無い銘柄はAPIを呼ばない
            calls.clear()
            tabs, missing = wf.fetch_fy_tables(["1111.T", "2222.T", "3333.T", "9999.T"], "k", daily_limit=3)
            self.assertEqual(calls, ["/companies/E3/financials"])  # 続きから(取得済みは飛ばす)
            self.assertEqual(missing, [])
            self.assertEqual(len(tabs), 4)

    def test_low_remaining_header_stops(self):
        import tempfile
        from unittest import mock

        def fake_get(path, params, key, state):
            state["calls"] += 1
            state["remaining"] = 20 if path != "/companies" else 25
            if path == "/companies":
                return {"data": [{"sec_code": "1111", "edinet_code": "E1"}, {"sec_code": "2222", "edinet_code": "E2"}]}
            return {"data": []}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(wf, "EDB_DIR", tmp), \
                mock.patch.object(wf, "_edb_get", fake_get):
            _, missing = wf.fetch_fy_tables(["1111.T", "2222.T"], "k", daily_limit=40)
            self.assertEqual(missing, ["2222.T"])                  # 1銘柄取って残り20 → 停止


class LossAndSector(unittest.TestCase):
    F = {"score": 8, "maxScore": 9, "ratio": 0.889}

    def test_loss_is_rejected_regardless_of_score(self):
        fs = {"A.T": {**self.F, "net_income": -1.0}, "B.T": {**self.F, "net_income": 5.0},
              "C.T": {**self.F, "net_income": 0.0}, "D.T": {**self.F}, "E.T": None}   # 純利益不明(None)は赤字扱いにしない
        lost = []
        final, unknown = wf.apply_f_gate(list(fs), "2022-01-03", lambda t, a: fs[t], bw, 6, loss_out=lost)
        self.assertEqual(final, ["B.T", "D.T"])
        self.assertEqual(sorted(lost), ["A.T", "C.T"])
        self.assertEqual(unknown, 1)

    def test_loss_only_counted_when_gate_passed(self):
        low = {"score": 2, "maxScore": 9, "ratio": 0.222, "net_income": -1.0}
        lost = []
        final, _ = wf.apply_f_gate(["A.T"], "2022-01-03", lambda t, a: low, bw, 6, loss_out=lost)
        self.assertEqual((final, lost), ([], []))

    def test_loss_uses_only_filings_before_asof(self):
        rows = FinData().rows()   # 2022-08-01 提出の訂正は純利益-999(赤字)。2022-07-01 時点では見えてはいけない
        fy = wf.parse_edinetdb_rows(rows)
        before = bw.calc_f_score(wf.fin_data_asof(fy, "2022-07-01"))
        self.assertFalse(wf.is_loss(before))
        after = bw.calc_f_score(wf.fin_data_asof(fy, "2022-09-01"))
        self.assertTrue(wf.is_loss(after))

    def test_sector_exclusion(self):
        s33 = {"A.T": "7050", "B.T": "8050", "C.T": "3050", "D.T": "7200"}
        keep, drop = wf.split_excluded_sectors(["A.T", "B.T", "C.T", "D.T", "E.T"], s33, bw.EXCLUDED_S33)
        self.assertEqual(keep, ["C.T", "E.T"])            # 業種不明(E)は残す
        self.assertEqual(drop, {"A.T": "7050", "B.T": "8050", "D.T": "7200"})
        self.assertEqual(wf.split_excluded_sectors(["A.T"], None, bw.EXCLUDED_S33), (["A.T"], {}))


class TickersFile(unittest.TestCase):
    def test_parse(self):
        text = "﻿# コメント\n7203\n\n6758.T  ソニーグループ\n130a\n72030\n7203\nXYZ\n8306,三菱UFJ銀行\n"
        tickers, names, bad = wf.parse_tickers_file(text)
        self.assertEqual(tickers, ["7203.T", "6758.T", "130A.T", "8306.T"])   # 重複除去・入力順・5桁と小文字を正規化
        self.assertEqual(names, {"6758.T": "ソニーグループ", "8306.T": "三菱UFJ銀行"})
        self.assertEqual(bad, ["XYZ"])

    def test_name_exclusion(self):
        names = {"A.T": "みずほ銀行", "B.T": "〇〇証券", "C.T": "△△保険", "D.T": "◇◇フィナンシャル金融", "E.T": "□□不動産",
                 "F.T": "トヨタ自動車"}
        keep, drop = wf.split_excluded_names(["A.T", "B.T", "C.T", "D.T", "E.T", "F.T", "G.T"], names)
        self.assertEqual(keep, ["F.T", "G.T"])                 # 名前が分からない銘柄(G)は残す
        self.assertEqual(sorted(drop), ["A.T", "B.T", "C.T", "D.T", "E.T"])

    def test_options_can_be_combined(self):
        a = wf.build_parser().parse_args(["--tickers-file", "codes.txt", "--limit", "150", "--edinetdb-daily-limit", "40",
                                          "--partial", "--cache-only"])
        self.assertEqual((a.tickers_file, a.limit, a.edinetdb_daily_limit, a.partial), ("codes.txt", 150, 40, True))
        self.assertEqual(wf.build_parser().parse_args([]).tickers_file, "")

    def test_load_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "codes.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("1301 極洋\n1332\n")
            self.assertEqual(wf.load_tickers_file(path), (["1301.T", "1332.T"], {"1301.T": "極洋"}))
            with open(path, "w", encoding="utf-8") as f:
                f.write("# のみ\n")
            with self.assertRaises(SystemExit):
                wf.load_tickers_file(path)


class Select(unittest.TestCase):
    def test_stages_and_no_lookahead(self):
        up = prices()
        down = prices(start=400.0, drift=-0.0008)
        thin = prices(vol=10)
        # 選定日以降だけ大暴落しても、選定には影響しない
        future = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 2_000_000.0},
                              index=pd.bdate_range("2022-01-03", periods=50))
        up_future = pd.concat([up, future])
        px = {"UP.T": up_future, "DOWN.T": down, "THIN.T": thin}
        surv, final, st = wf.select_watchlist("2022-01-03", px, bw, fscore_fn=lambda t, a: {"ratio": 1.0})
        self.assertEqual(surv, ["UP.T"])
        self.assertEqual(final, ["UP.T"])
        self.assertEqual((st["対象"], st["流動性通過"], st["トレンド通過"], st["Fスコア合格"]), (3, 2, 1, 1))

    def test_fscore_shortage_excluded(self):
        px = {"UP.T": prices()}
        _, final, st = wf.select_watchlist("2022-01-03", px, bw, fscore_fn=lambda t, a: None)
        self.assertEqual(final, [])
        self.assertEqual(st["Fスコア判定不能"], 1)

    def test_stale_ticker_skipped(self):
        px = {"OLD.T": prices(end="2021-06-01")}
        _, _, st = wf.select_watchlist("2022-01-03", px, bw)
        self.assertEqual(st["対象"], 0)


class Equity(unittest.TestCase):
    def test_align_and_span_return(self):
        days = pd.bdate_range("2022-01-03", periods=6)
        eq = pd.Series([1_100_000.0, 1_210_000.0], index=days[2:4])
        a = wf.align_equity(eq, days, 1_000_000)
        self.assertEqual(list(a.iloc[:2]), [1_000_000.0] * 2)
        self.assertEqual(a.iloc[-1], 1_210_000.0)
        self.assertAlmostEqual(wf.span_return(a, days[0], days[4], 1_000_000), 21.0)
        self.assertAlmostEqual(wf.span_return(a, days[4], days[6 - 1] + pd.Timedelta(days=1), 1_000_000), 0.0)



class RegimeOkSeries(unittest.TestCase):
    def test_above_and_below_ma(self):
        idx = pd.bdate_range("2021-01-01", periods=260)
        up = pd.Series(np.linspace(100, 200, 260), index=idx)
        self.assertTrue(wf.regime_ok_series(up).iloc[-1])           # 上昇中は終値が200日線より上
        self.assertFalse(wf.regime_ok_series(up[::-1].set_axis(idx)).iloc[-1])   # 下落中は下

    def test_not_enough_history_is_false(self):
        idx = pd.bdate_range("2021-01-01", periods=250)
        ok = wf.regime_ok_series(pd.Series(np.linspace(100, 200, 250), index=idx))
        self.assertFalse(ok.iloc[:199].any())                      # 200本そろうまでは見送り
        self.assertTrue(ok.iloc[199:].all())

    def test_no_lookahead(self):
        # その日の判定は「その日の終値」と「その日までの200日線」だけで決まる: 後ろのデータを変えても過去の判定は変わらない
        idx = pd.bdate_range("2021-01-01", periods=300)
        rng = np.random.default_rng(1)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 300)), index=idx)
        full = wf.regime_ok_series(close)
        cut = 250
        changed = close.copy()
        changed.iloc[cut:] = changed.iloc[cut:] * 3 + 500           # 未来を大きく書き換える
        after = wf.regime_ok_series(changed)
        pd.testing.assert_series_equal(full.iloc[:cut], after.iloc[:cut])
        # 手計算: cut-1日目の判定 = その日の終値 > 直近200本(その日を含む)の平均
        d = cut - 1
        self.assertEqual(bool(full.iloc[d]), bool(close.iloc[d] > close.iloc[d - 199:d + 1].mean()))
        # 切り出して計算しても同じ(未来を使っていない)
        pd.testing.assert_series_equal(full.iloc[:cut], wf.regime_ok_series(close.iloc[:cut]))


class JudgeCandidate(unittest.TestCase):
    base = {"final_return_pct": 50.0, "sharpe": 1.0, "max_drawdown_pct": -15.0}

    def m(self, ret, sharpe, mdd):
        return {"final_return_pct": ret, "sharpe": sharpe, "max_drawdown_pct": mdd}

    def test_meets_all(self):
        self.assertTrue(wf.judge_candidate(self.base, self.m(50.0, 1.0, -15.0)))   # 同値は「以上」「悪化なし」
        self.assertTrue(wf.judge_candidate(self.base, self.m(80.0, 1.5, -10.0)))

    def test_each_condition_fails(self):
        self.assertFalse(wf.judge_candidate(self.base, self.m(49.9, 1.5, -10.0)))  # 騰落率が下
        self.assertFalse(wf.judge_candidate(self.base, self.m(80.0, 0.9, -10.0)))  # シャープが下
        self.assertFalse(wf.judge_candidate(self.base, self.m(80.0, 1.5, -15.1)))  # 最大DDが悪化

    def test_missing_is_false(self):
        self.assertFalse(wf.judge_candidate(None, self.m(80.0, 1.5, -10.0)))
        self.assertFalse(wf.judge_candidate(self.base, None))

if __name__ == "__main__":
    unittest.main()
