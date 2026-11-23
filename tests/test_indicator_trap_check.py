"""indicator_trap_check(指標の罠チェック: ROE DuPont 3分解のレバレッジ警告と低PBR資産毀損チェック)のテスト。
ネットワーク・シートには触れない。fujiko.py は読み込み時に初期化が走るため、関数だけをASTで取り出す。
"""
import ast
import io
import os
import unittest
from contextlib import redirect_stdout

FUJIKO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fujiko.py")


def _load():
    with open(FUJIKO_PATH, encoding="utf-8") as f:
        source = f.read()
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "indicator_trap_check"]
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns["indicator_trap_check"]


check = _load()


def fin(equity=20.0, equity_ratio=40.0, net_income=10.0, net_sales=100.0, bps=100.0):
    """equity/自己資本比率から総資産を逆算する(equity=20・比率40% → 総資産50 → レバレッジ2.5x)"""
    return {"fiscalYears": {"latest": {"netIncome": net_income, "netSales": net_sales, "equity": equity,
                                       "equityRatio": equity_ratio, "bps": bps}}}


def score(pseudo_price):
    return {"idealPrice": {"pseudoPrice": pseudo_price}}


class TestIndicatorTrapCheck(unittest.TestCase):
    def test_no_warning_for_healthy_company(self):
        r = check(fin(), score(150.0))
        self.assertEqual(r["warnings"], [])
        self.assertEqual(r["dupont"], {"margin": 10.0, "turnover": 2.0, "leverage": 2.5})

    def test_high_leverage_warns_at_5x(self):
        r = check(fin(equity_ratio=20.0), score(150.0))          # 総資産100・自己資本20 → ちょうど5.0x
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("高レバレッジ", r["warnings"][0])
        self.assertEqual(r["dupont"]["leverage"], 5.0)
        self.assertEqual(check(fin(equity_ratio=21.0), score(150.0))["warnings"], [])   # 4.8x は警告なし

    def test_low_pbr_warns_below_half(self):
        r = check(fin(), score(40.0))                            # PBR 0.40
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("低PBR(0.40倍)", r["warnings"][0])
        self.assertEqual(check(fin(), score(50.0))["warnings"], [])   # ちょうど0.5は警告しない

    def test_both_warnings(self):
        r = check(fin(equity_ratio=10.0), score(30.0))
        self.assertEqual(len(r["warnings"]), 2)

    def test_missing_data_passes_without_error(self):
        empty = {"warnings": [], "dupont": None}
        self.assertEqual(check(None, None), empty)
        self.assertEqual(check({}, {}), empty)
        self.assertEqual(check({"fiscalYears": {}}, None), empty)
        # BPS・擬似株価が取れない → 低PBRは判定しない(DuPontは出る)
        r = check(fin(bps=None), score(None))
        self.assertEqual(r["warnings"], [])
        self.assertIsNotNone(r["dupont"])
        self.assertEqual(check(fin(bps=0), score(40.0))["warnings"], [])   # BPS 0以下は判定しない

    def test_bad_values_do_not_raise(self):
        with redirect_stdout(io.StringIO()):
            r = check(fin(equity="abc"), score(40.0))
        self.assertEqual(r["warnings"], [])                      # 例外は握りつぶして空の警告を返す


if __name__ == "__main__":
    unittest.main()
