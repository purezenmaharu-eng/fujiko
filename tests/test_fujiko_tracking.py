"""fujiko.py のシグナル的中率トラッキング(遡り登録・失敗フラグ)のテスト。
シート・API・ネットワークには触れない(偽物のシートと小さなDataFrameだけを使う)。

fujiko.py はモジュール読み込み時に初期化が走るため、必要な定数・関数だけをASTで取り出して検証する。
"""
import ast
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

import numpy as np
import pandas as pd

FUJIKO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fujiko.py")
WANTED_VARS = {
    "TRACKING_HEADERS", "TRACKING_SIGNAL_COLUMNS", "TRACKING_HOLD_DAYS", "TRACKING_HIT_THRESHOLD_PCT",
    "TRACKING_LOOKBACK_DAYS", "TRACKING_REQUIRED_HEADERS", "_tracking_failed",
    "TRACKING_PRICE_CHUNK_SIZE", "TRACKING_PRICE_CHUNK_WAIT_SEC", "TRACKING_MIN_CALENDAR_DAYS",
}
WANTED_FUNCS = {
    "_tracking_register_new_signals", "_tracking_get_records", "_tracking_resolve_pending_signals", "run_signal_tracking",
    "_tracking_market_of", "_tracking_pending_tickers", "_tracking_close_series", "fetch_tracking_prices",
    "_tracking_fetch_missing_prices",
}


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
    ns = {"os": os, "pd": pd, "np": np, "time": mock.Mock(), "yf": mock.Mock(), "MARKET": "JP"}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns


def _df(n=8, lit=(), close_nan=()):
    """n営業日分の1銘柄のDataFrame。lit は (行位置, シグナル列名) のリスト"""
    idx = pd.bdate_range("2026-10-01", periods=n)
    df = pd.DataFrame({"Close": [100.0 + i for i in range(n)], "Ticker": "AAA"}, index=idx)
    for col in ("Ace_Start", "King_Start", "Polygraph_Start", "Ace_with_BEP_Start"):
        df[col] = False
    for i, col in lit:
        df.loc[idx[i], col] = True
    for i in close_nan:
        df.loc[idx[i], "Close"] = np.nan
    return df


class _Base(unittest.TestCase):
    def setUp(self):
        self.ns = _load_namespace()
        self.ws = mock.Mock()
        self.ns.update(
            sizing=mock.Mock(build_signal_info=mock.Mock(return_value={
                "pos": {"skip": False, "shares": 100, "loss": 1000.0}, "reason": "理由", "source": "出所"})),
            FUJIKO_CAPITAL=1000000,
            get_market_label=lambda t: "JP",
            _sheets_call_with_retry=lambda fn, *a, **k: fn(*a, **k),
        )
        self.register = self.ns["_tracking_register_new_signals"]
        self.val = {"AAA": {"radi": {"verdict": "買い"}, "evy": {"discountPct": 25}}}

    def run_register(self, df, keys=None, valuations=None):
        keys = set() if keys is None else keys
        n, past = self.register(self.ws, df, {"AAA": "テスト"}, keys, valuations if valuations is not None else self.val)
        rows = self.ws.append_rows.call_args[0][0] if self.ws.append_rows.called else []
        return n, past, rows, keys


class RegisterTests(_Base):
    def test_複数日の点灯が全部登録される(self):
        df = _df(lit=[(3, "Ace_Start"), (5, "King_Start"), (7, "Ace_Start")])
        n, past, rows, _ = self.run_register(df)
        self.assertEqual((n, past), (3, 2))
        self.assertEqual([r[4] for r in rows], ["2026/10/06", "2026/10/08", "2026/10/12"])  # 古い順
        self.assertEqual([r[2] for r in rows], ["Ace", "King", "Ace"])

    def test_遡り範囲より古い点灯は登録されない(self):
        df = _df(lit=[(2, "Ace_Start")])  # 8行中、直近5行は位置3〜7
        n, _, _, _ = self.run_register(df)
        self.assertEqual(n, 0)
        self.ws.append_rows.assert_not_called()

    def test_登録済みキーは登録されない(self):
        df = _df(lit=[(5, "Ace_Start"), (7, "Ace_Start")])
        keys = {("AAA", "Ace", "2026/10/08")}
        n, past, rows, _ = self.run_register(df, keys)
        self.assertEqual((n, past), (1, 0))
        self.assertEqual(rows[0][4], "2026/10/12")

    def test_過去日はバリュエーション列が空欄で最新行は値が入る(self):
        df = _df(lit=[(5, "Ace_Start"), (7, "Ace_Start")])
        _, _, rows, _ = self.run_register(df)
        past_row, latest_row = rows
        self.assertEqual((past_row[6], past_row[7]), ("", ""))
        self.assertEqual((latest_row[6], latest_row[7]), ("買い", 25))

    def test_Closeが欠けた行は飛ばされる(self):
        df = _df(lit=[(5, "Ace_Start"), (7, "Ace_Start")], close_nan=[5])
        n, _, rows, _ = self.run_register(df)
        self.assertEqual(n, 1)
        self.assertEqual(rows[0][4], "2026/10/12")

    def test_同じキーを同一実行内で二重登録しない(self):
        df = _df(lit=[(7, "Ace_Start")])
        combined = pd.concat([df, df])  # 同じ銘柄・同じ日付が重複して入っている異常系
        n, _, rows, keys = self.run_register(combined)
        self.assertEqual(n, 1)
        self.assertIn(("AAA", "Ace", "2026/10/12"), keys)

    def test_列の数と順番は従来のまま(self):
        df = _df(lit=[(7, "Ace_Start")])
        _, _, rows, _ = self.run_register(df)
        self.assertEqual(len(rows[0]), len(self.ns["TRACKING_HEADERS"]))
        self.assertEqual(rows[0][8:13], ["追跡中", "", "", "", ""])
        self.assertEqual(self.ws.append_rows.call_args[1], {"value_input_option": "RAW"})


class FailureFlagTests(_Base):
    def setUp(self):
        super().setUp()
        self.ns.update(DRY_RUN=False, _load_sheets_credentials=mock.Mock(), gspread=mock.Mock())

    def _run(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"GOOGLE_SHEETS_CREDENTIALS": "x", "SPREADSHEET_ID": "y"}), redirect_stdout(buf):
            result = self.ns["run_signal_tracking"](_df(), {}, {})
        return result, buf.getvalue()

    def test_例外で失敗フラグが立つ(self):
        self.ns["gspread"].authorize.side_effect = RuntimeError("boom")
        result, out = self._run()
        self.assertIsNone(result)
        self.assertTrue(self.ns["_tracking_failed"])
        self.assertIn("シグナル的中率トラッキング失敗", out)

    def test_成功時はフラグが立たない(self):
        sh = self.ns["gspread"].authorize.return_value.open_by_key.return_value
        ws = sh.worksheet.return_value
        ws.cell.return_value.value = "銘柄名"
        ws.row_count = 10
        ws.col_count = 16
        ws.row_values.return_value = self.ns["TRACKING_HEADERS"]
        ws.get_all_records.return_value = []
        result, out = self._run()
        self.assertEqual(result, (0, 0, 0), out)
        self.assertFalse(self.ns["_tracking_failed"])
        self.assertIn("新規登録0件 / 判定確定0件 / 的中0件", out)

    def test_DRY_RUNとシート未設定では失敗扱いにしない(self):
        self.ns.update(DRY_RUN=True)
        self.assertIsNone(self.ns["run_signal_tracking"](_df(), {}, {}))
        self.assertFalse(self.ns["_tracking_failed"])


def _rec(ticker="1111.T", lit="2026/08/03", close=100.0, status="追跡中"):
    return {"ティッカー": ticker, "点灯日": lit, "点灯日終値": close, "ステータス": status}


def _closes(start="2026-08-03", values=None):
    values = values if values is not None else [100.0 + i for i in range(12)]
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


class PendingTickersTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load_namespace()
        self.pending = self.ns["_tracking_pending_tickers"]
        self.today = "2026-10-29"

    def test_条件に合う銘柄だけ数える(self):
        records = [
            _rec("1111.T", "2026/08/03"), _rec("1111.T", "2026/07/28"),   # 同じ銘柄は最古の点灯日にまとめる
            _rec("2222.T", "2026/10/20"),                                    # 暦14日が未経過
            _rec("3333.T", "2026/08/03", status="完了"),                      # 追跡中でない
            _rec("AAPL", "2026/08/03"),                                       # 別市場
            _rec("4444.T", "2026/08/03"),                                     # combined_dfに既にある
            _rec("5555.T", "壊れた日付"),
            _rec("", "2026/08/03"),
        ]
        got = self.pending(records, "JP", known={"4444.T"}, today=self.today)
        self.assertEqual(list(got), ["1111.T"])
        self.assertEqual(got["1111.T"], pd.Timestamp("2026-07-28"))

    def test_米国の実行は米国だけ(self):
        got = self.pending([_rec("1111.T"), _rec("AAPL"), _rec("BRK-B")], "US", today=self.today)
        self.assertEqual(sorted(got), ["AAPL", "BRK-B"])


class FetchPricesTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load_namespace()
        self.fetch = self.ns["fetch_tracking_prices"]

    @staticmethod
    def _multi(tickers, missing=()):
        idx = pd.bdate_range("2026-08-03", periods=3)
        cols = {}
        for t in tickers:
            for f in ("Open", "Close"):
                cols[(t, f)] = [np.nan] * 3 if t in missing else [1.0, 2.0, 3.0]
        return pd.DataFrame(cols, index=idx)

    def _run(self, downloader, tickers, chunk_size=2):
        sleeps = []
        buf = io.StringIO()
        with redirect_stdout(buf):
            prices, failed = self.fetch(tickers, "2026-07-27", "2026-10-30", chunk_size=chunk_size,
                                        downloader=downloader, sleep=sleeps.append)
        return prices, failed, sleeps, buf.getvalue()

    def test_まとめ取得はチャンクごとに1回で待ち時間を挟む(self):
        calls = []

        def dl(tickers, **kw):
            calls.append(tickers)
            return self._multi(tickers)

        prices, failed, sleeps, _ = self._run(dl, ["A.T", "B.T", "C.T", "D.T", "E.T"])
        self.assertEqual(calls, [["A.T", "B.T"], ["C.T", "D.T"], ["E.T"]])
        self.assertEqual((len(prices), failed), (5, []))
        self.assertEqual(len(sleeps), 2)

    def test_チャンクが失敗したら1銘柄ずつ取り直す(self):
        calls = []

        def dl(tickers, **kw):
            calls.append(tickers)
            if isinstance(tickers, list):
                raise RuntimeError("boom")
            return self._multi([tickers])

        prices, failed, _, out = self._run(dl, ["A.T", "B.T"])
        self.assertEqual(calls, [["A.T", "B.T"], "A.T", "B.T"])
        self.assertEqual((sorted(prices), failed), (["A.T", "B.T"], []))
        self.assertIn("1銘柄ずつ", out)

    def test_取れない銘柄は警告して飛ばし例外は出さない(self):
        def dl(tickers, **kw):
            if isinstance(tickers, list):
                return self._multi(tickers, missing=("B.T",))
            raise RuntimeError("delisted")

        prices, failed, _, out = self._run(dl, ["A.T", "B.T"])
        self.assertEqual((sorted(prices), failed), (["A.T"], ["B.T"]))
        self.assertIn("取得できなかった銘柄 1件", out)

    def test_重複日付は最後を残し欠損は除く(self):
        idx = pd.DatetimeIndex(["2026-08-03", "2026-08-03", "2026-08-04", "2026-08-05"])
        df = pd.DataFrame({"Close": [1.0, 2.0, np.nan, 4.0]}, index=idx)
        s = self.ns["_tracking_close_series"](df, "A.T")
        self.assertEqual(list(s), [2.0, 4.0])


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load_namespace()
        self.ns["_sheets_call_with_retry"] = lambda fn, *a, **k: fn(*a, **k)
        self.ns["gspread"] = mock.Mock(Cell=lambda row, col, value: (row, col, value))
        self.resolve = self.ns["_tracking_resolve_pending_signals"]
        self.ws = mock.Mock()
        self.empty = pd.DataFrame({"Ticker": [], "Close": []})

    def _run(self, records, prices, **kw):
        stats = {}
        r = self.resolve(self.ws, self.empty, records, extra_prices=prices, stats=stats, **kw)
        return r, stats

    def test_10営業日後の騰落率が閾値以上なら的中(self):
        # 点灯日(位置0)の終値100、10営業日後(位置10)の終値を変えて閾値を確認
        for judge, hit in ((102.0, 1), (101.99, 0)):
            self.ws.reset_mock()
            vals = [100.0] * 10 + [judge, 100.0]
            r, stats = self._run([_rec("1111.T", "2026/08/03", 100.0)], {"1111.T": _closes(values=vals)})
            self.assertEqual(r, (1, hit), judge)
            cells = self.ws.update_cells.call_args[0][0]
            self.assertEqual([c[1] for c in cells], [9, 10, 11, 12, 13])
            self.assertEqual(cells[0][2], "完了")
            self.assertEqual(cells[1][2], "2026/08/17")
            self.assertEqual(cells[4][2], "的中" if hit else "不的中")

    def test_読み飛ばし条件ごとの件数(self):
        prices = {
            "1111.T": _closes(),                        # 正常に判定される
            "2222.T": _closes(values=[100.0] * 5),      # 10営業日が未経過
            "3333.T": _closes(start="2026-08-04"),      # 点灯日(08/03)が価格データにない
            "4444.T": _closes(values=[100.0] * 10 + [np.nan, 100.0]),  # 判定日の終値が欠損
        }
        # 欠損は取り出し時に除かれるため、ここでは直接NaN入りのSeriesを渡して判定日欠損の分岐を確認する
        records = [
            _rec("1111.T"), _rec("2222.T"), _rec("3333.T"), _rec("4444.T"),
            _rec("9999.T"),                              # 価格データなし
            _rec("1111.T", close=""),                    # 点灯日終値が空
            _rec("1111.T", status="完了"),               # 追跡中でないので数えない
        ]
        r, stats = self._run(records, prices)
        self.assertEqual(r[0], 1)
        self.assertEqual(stats, {"10営業日が未経過": 1, "点灯日が価格データにない": 1,
                                 "判定日の終値が欠損": 1, "価格データなし": 1, "行の値が不足": 1})

    def test_dry_runはシートに書かない(self):
        r, _ = self._run([_rec()], {"1111.T": _closes()}, dry_run=True)
        self.assertEqual(r[0], 1)
        self.ws.update_cells.assert_not_called()

    def test_extra_pricesに無い銘柄はcombined_dfを使う(self):
        idx = pd.bdate_range("2026-08-03", periods=12)
        df = pd.DataFrame({"Close": [100.0] * 10 + [103.0, 100.0], "Ticker": "1111.T"}, index=idx)
        r = self.resolve(self.ws, df, [_rec("1111.T")], extra_prices={})
        self.assertEqual(r, (1, 1))


class FetchMissingPricesTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load_namespace()
        self.run = self.ns["_tracking_fetch_missing_prices"]

    def test_対象がなければ取得を呼ばない(self):
        fetcher = mock.Mock()
        self.assertEqual(self.run([_rec("1111.T", status="完了")], set(), "JP", today="2026-10-29", fetcher=fetcher), {})
        fetcher.assert_not_called()

    def test_最古の点灯日の7日前から取得する(self):
        fetcher = mock.Mock(return_value=({"1111.T": _closes()}, []))
        with redirect_stdout(io.StringIO()):
            got = self.run([_rec("1111.T", "2026/08/03"), _rec("2222.T", "2026/07/28")], {"2222.T"}, "JP",
                           today="2026-10-29", fetcher=fetcher)
        self.assertEqual(list(got), ["1111.T"])
        self.assertEqual(fetcher.call_args[0], (["1111.T"], "2026-07-27", "2026-10-30"))

    def test_取得で例外が出ても空dictを返す(self):
        fetcher = mock.Mock(side_effect=RuntimeError("boom"))
        with redirect_stdout(io.StringIO()) as buf:
            got = self.run([_rec()], set(), "JP", today="2026-10-29", fetcher=fetcher)
        self.assertEqual(got, {})
        self.assertIn("監視銘柄分の判定は続けます", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
