"""Ace×BEPを評価表に加える変更(年別成績・walkforward表)と、複数シグナル点灯時の根拠列の注記のテスト。
fujiko.py は読み込み時に初期化が走るため、必要な定数・関数だけをASTで取り出す。ネットワークには触れない。"""
import ast
import os
import unittest

import numpy as np
import pandas as pd

import sizing
import walkforward as wf

FUJIKO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fujiko.py")
WANTED_VARS = {"YEAR_BUCKETS", "YEARLY_SIGNAL_COLS", "ACE_BEP_NOTE"}
WANTED_FUNCS = {"build_yearly_results", "sizing_cells"}


def _ns():
    with open(FUJIKO_PATH, encoding="utf-8") as f:
        body = [n for n in ast.parse(f.read()).body
                if (isinstance(n, ast.FunctionDef) and n.name in WANTED_FUNCS)
                or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in WANTED_VARS for t in n.targets))]
    ns = {"sizing": sizing}
    exec(compile(ast.Module(body=body, type_ignores=[]), FUJIKO_PATH, "exec"), ns)
    return ns


def _info(label, skip=False, shares=100, loss=1000.0):
    return {"label": label, "reason": f"{label}の根拠", "source": "出所",
            "pos": {"skip": skip, "shares": shares, "loss": loss}}


class SizingCells(unittest.TestCase):
    def setUp(self):
        self.ns = _ns()
        self.f = self.ns["sizing_cells"]

    def test_note_added_when_ace_bep_also_lit(self):
        shares, loss, reason = self.f([_info("Ace", shares=300, loss=2500.0), _info("Ace×BEP", shares=999, loss=9.0)])
        self.assertEqual((shares, loss), (300, -2500))          # 株数・損切りは先頭(Ace)の分のまま
        self.assertIn("Ace×BEPも点灯(回数少なめ29回)", reason)
        self.assertIn("Aceの根拠 / Ace×BEPの根拠", reason)   # 既存の根拠は残る
        self.assertTrue(reason.endswith("【出どころ】出所"))

    def test_no_note_without_ace_bep(self):
        _, _, reason = self.f([_info("Ace"), _info("King")])
        self.assertNotIn("Ace×BEPも点灯", reason)

    def test_no_note_when_only_ace_bep(self):
        _, _, reason = self.f([_info("Ace×BEP")])
        self.assertNotIn("Ace×BEPも点灯", reason)

    def test_always_three_cells_and_unchanged_cases(self):
        self.assertEqual(len(self.f([_info("Ace"), _info("Ace×BEP")])), 3)
        self.assertEqual(self.f([]), ["－", "－", "シグナル非点灯(ファンダメンタルズ上位として掲載)"])
        shares, loss, _ = self.f([_info("King", skip=True), _info("Ace×BEP")])
        self.assertEqual((shares, loss), ("見送り", "－"))


class YearlyResults(unittest.TestCase):
    def test_ace_bep_included_and_signal_sets_unchanged(self):
        ns = _ns()
        idx = pd.bdate_range("2022-12-01", "2026-06-30")
        eq = pd.Series(1_000_000 * np.exp(np.arange(len(idx)) * 0.0003), index=idx)
        pr = {c: {"equity": eq} for c in ("Ace_Start", "King_Start", "Polygraph_Start", "Ace_with_BEP_Start")}
        out = ns["build_yearly_results"](pr)
        self.assertEqual(list(out), ["Ace_Start", "King_Start", "Ace_with_BEP_Start"])   # ポリグラフは従来どおり対象外
        self.assertEqual(list(out["Ace_with_BEP_Start"]), ["2023年", "2024年", "2025年以降"])
        self.assertIsNotNone(out["Ace_with_BEP_Start"]["2024年"])


class OosTableAceBep(unittest.TestCase):
    def test_ace_bep_block_has_baseline_only(self):
        idx = pd.bdate_range("2022-01-04", "2026-06-30")
        eq = pd.Series(1_000_000 * np.exp(np.arange(len(idx)) * 0.0004), index=idx)

        def res(cols):
            return {"cols": cols, "desc": "d", "res": {"sims": {c: {"equity": eq, "n_trades": 5} for c in cols}}}
        ak = ["Ace_Start", "King_Start"]
        oos = {"baseline": res(ak + ["Ace_with_BEP_Start"]), "A": res(ak), "B": res(["King_Start"]),
               "C1": res(ak), "C2": res(ak)}
        table = wf.oos_table(oos, eq, eq, 1_000_000)
        self.assertEqual(list(table), ["Ace", "King", "Ace×BEP"])
        self.assertEqual([r["name"] for r in table["Ace×BEP"]],
                         ["1306買い持ち", "日経平均(^N225)買い持ち", "baseline"])
        self.assertEqual([r["name"] for r in table["Ace"]][2:], ["baseline", "A", "C1", "C2"])   # 案A・C1・C2はAce・Kingのまま
        self.assertEqual([r["name"] for r in table["King"]][2:], ["baseline", "A", "B", "C1", "C2"])


if __name__ == "__main__":
    unittest.main()
