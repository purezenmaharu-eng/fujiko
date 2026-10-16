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
}
WANTED_FUNCS = {"_tracking_register_new_signals", "_tracking_get_records", "_tracking_resolve_pending_signals", "run_signal_tracking"}


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
    ns = {"os": os, "pd": pd, "np": np}
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


if __name__ == "__main__":
    unittest.main()
