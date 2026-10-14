"""sheet_format.py のテスト(Googleスプレッドシートはモック)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sheet_format as sf  # noqa: E402

HEADERS = ["日付", "銘柄名", "市場", "本日のテクニカル", "Evy式適正価格", "Evy式割安率%",
           "株おじさん式理論株価", "株おじさん式割安率%", "株おじさん健全性", "解説", "チャート",
           "Yahoo!ファイナンス", "推奨株数", "損切り損失額", "根拠・出どころ"]


def _max_col(requests):
    """リクエスト群が触る列の最大インデックス(終端は排他的なので -1 して比べる)"""
    m = -1
    for r in requests:
        for body in r.values():
            rng = body.get("range") or (body.get("bandedRange") or {}).get("range")
            if rng is None and "rule" in body:
                rng = body["rule"]["ranges"][0]
            if not rng:
                continue
            if "endColumnIndex" in rng:
                m = max(m, rng["endColumnIndex"] - 1)
            if "endIndex" in rng and rng.get("dimension") == "COLUMNS":
                m = max(m, rng["endIndex"] - 1)
    return m


class _FakeWs:
    def __init__(self, col_count=20, sheet_id=7):
        self.id = sheet_id
        self.col_count = col_count
        self.resized_to = None

    def resize(self, cols):
        self.resized_to = cols
        self.col_count = cols


class _FakeSh:
    def __init__(self, meta_sheet, fail_on=None):
        self.meta_sheet = meta_sheet
        self.calls = []
        self.fail_on = fail_on or set()

    def fetch_sheet_metadata(self, params=None):
        return {"sheets": [self.meta_sheet]}

    def batch_update(self, body):
        reqs = body["requests"]
        kind = next(iter(reqs[0]))
        self.calls.append(kind)
        if kind in self.fail_on:
            raise RuntimeError(f"boom {kind}")


def _bare_meta(sheet_id=7):
    return {"properties": {"sheetId": sheet_id}}


class RequestBuildTest(unittest.TestCase):
    def test_all_requests_stay_inside_the_grid(self):
        reqs = (sf.build_layout_requests(7, HEADERS) + sf.build_banding_requests(7, len(HEADERS))
                + sf.build_conditional_requests(7, HEADERS))
        self.assertLessEqual(_max_col(reqs), len(HEADERS) - 1)

    def test_banding_starts_at_header_row(self):
        band = sf.build_banding_requests(7, 15)[0]["addBanding"]["bandedRange"]
        self.assertEqual(band["range"]["startRowIndex"], 0)  # 先頭データ行が青く塗られる不具合の再発防止

    def test_conditional_rules_point_at_the_right_columns(self):
        reqs = sf.build_conditional_requests(7, HEADERS)
        cols = {r["addConditionalFormatRule"]["rule"]["ranges"][0]["startColumnIndex"] for r in reqs}
        self.assertEqual(cols, {HEADERS.index("Evy式割安率%"), HEADERS.index("株おじさん式割安率%"),
                                HEADERS.index("株おじさん健全性")})

    def test_unknown_columns_are_skipped_and_reve_columns_are_used_when_present(self):
        headers = HEADERS + ["REVE判定"]
        reqs = sf.build_conditional_requests(7, headers)
        words = [r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
                 for r in reqs]
        self.assertIn("買い候補", words)
        self.assertEqual(sf.build_conditional_requests(7, ["日付"]), [])

    def test_cleanup_deletes_every_existing_rule(self):
        meta = {"properties": {"sheetId": 7}, "bandedRanges": [{"bandedRangeId": 3}],
                "conditionalFormats": [{}, {}, {}]}
        reqs = sf.build_cleanup_requests(meta)
        self.assertEqual(sum("deleteBanding" in r for r in reqs), 1)
        self.assertEqual(sum("deleteConditionalFormatRule" in r for r in reqs), 3)

    def test_needs_decoration(self):
        self.assertTrue(sf.needs_decoration(_bare_meta()))
        self.assertFalse(sf.needs_decoration({"bandedRanges": [{"bandedRangeId": 1}]}))
        self.assertFalse(sf.needs_decoration({"conditionalFormats": [{}]}))


class EnsureDecorationTest(unittest.TestCase):
    def test_new_sheet_gets_all_groups(self):
        sh, ws = _FakeSh(_bare_meta()), _FakeWs()
        result = sf.ensure_decoration(sh, ws, HEADERS)
        self.assertEqual(set(result), {"列幅・折り返し・見出し", "縞模様", "色分け"})
        self.assertTrue(all(result.values()))

    def test_already_decorated_sheet_is_left_alone(self):
        sh = _FakeSh({"properties": {"sheetId": 7}, "bandedRanges": [{"bandedRangeId": 1}]})
        self.assertIsNone(sf.ensure_decoration(sh, _FakeWs(), HEADERS))
        self.assertEqual(sh.calls, [])

    def test_one_failure_does_not_cancel_the_others(self):
        sh = _FakeSh(_bare_meta(), fail_on={"addBanding"})
        result = sf.ensure_decoration(sh, _FakeWs(), HEADERS)
        self.assertFalse(result["縞模様"])
        self.assertTrue(result["色分け"])
        self.assertTrue(result["列幅・折り返し・見出し"])

    def test_narrow_sheet_is_widened_first(self):
        ws = _FakeWs(col_count=10)
        sf.ensure_decoration(_FakeSh(_bare_meta()), ws, HEADERS)
        self.assertEqual(ws.resized_to, len(HEADERS))

    def test_force_redecorates_after_cleanup(self):
        sh = _FakeSh({"properties": {"sheetId": 7}, "bandedRanges": [{"bandedRangeId": 1}],
                      "conditionalFormats": [{}]})
        result = sf.ensure_decoration(sh, _FakeWs(), HEADERS, force=True)
        self.assertIn("既存装飾の削除", result)
        self.assertEqual(sh.calls[0], "deleteBanding")  # 先に消してから付け直す


if __name__ == "__main__":
    unittest.main()
