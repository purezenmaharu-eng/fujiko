# -*- coding: utf-8 -*-
"""日付シート(日次の掲載シート)の見た目を整える。

以前は fujiko.py の write_to_spreadsheet() に直書きされ、次の2つの弱点があった。
  1) 装飾を1回のbatch_updateにまとめていたため、1つでもリクエストが失敗すると全部(列幅・折り返し・
     縞模様・色分け)が無効になり、失敗理由もログに残りにくかった。
  2) 装飾は「その日の最初の1回(新規シート)」にしか行われず、失敗した日のシートは一生そのままだった。
さらに、新規シートの列数が10なのに15列ぶん指定していたこと、縞模様の headerColor が
「ヘッダー行」ではなく先頭のデータ行を青く塗っていたことも直した。

ここでは「リクエストを作る関数(純粋関数、テストしやすい)」と
「適用する関数(グループごとに独立して実行し、失敗理由を表示する)」を分けている。
"""

GREEN = {"red": 0.85, "green": 0.94, "blue": 0.85}
RED = {"red": 0.98, "green": 0.85, "blue": 0.85}
YELLOW = {"red": 1.0, "green": 0.96, "blue": 0.80}
HEADER_BLUE = {"red": 0.85, "green": 0.89, "blue": 0.96}

# 列名 -> 列幅(px)。ここに無い列は既定幅のまま
COLUMN_WIDTHS = {
    "日付": 95, "銘柄名": 160, "市場": 90, "本日のテクニカル": 130,
    "Evy式適正価格": 110, "Evy式割安率%": 110,
    "株おじさん式理論株価": 130, "株おじさん式割安率%": 130, "株おじさん健全性": 130,
    "REVE判定": 110, "REVEスコア": 100, "REVE連続Q": 100,
    "解説": 340, "チャート": 110, "Yahoo!ファイナンス": 110,
    "推奨株数": 110, "損切り損失額": 110, "根拠・出どころ": 420,
}
WRAP_COLUMNS = ["解説", "根拠・出どころ"]

# 数値の割安率: 20%以上は緑、-10%未満は赤
NUMBER_RULE_COLUMNS = ["Evy式割安率%", "株おじさん式割安率%"]
# 文字列に含まれる語で色分け: 列名 -> [(語, 色), ...]
TEXT_RULE_COLUMNS = {
    "株おじさん健全性": [("健全", GREEN), ("注意", RED)],
    "REVE判定": [("買い候補", GREEN), ("監視候補", YELLOW)],
}


def _col_range(sheet_id, col, start_row=1):
    return {"sheetId": sheet_id, "startRowIndex": start_row,
            "startColumnIndex": col, "endColumnIndex": col + 1}


def build_layout_requests(sheet_id, headers):
    """列幅・折り返し・見出し行の強調"""
    reqs = []
    for i, name in enumerate(headers):
        width = COLUMN_WIDTHS.get(name)
        if width:
            reqs.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": width}, "fields": "pixelSize"}})
    for name in WRAP_COLUMNS:
        if name in headers:
            i = headers.index(name)
            reqs.append({"repeatCell": {
                "range": {"sheetId": sheet_id, "startColumnIndex": i, "endColumnIndex": i + 1},
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}})
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": HEADER_BLUE}},
        "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"}})
    return reqs


def build_banding_requests(sheet_id, n_cols):
    """縞模様。範囲は見出し行(0行目)から始める(headerColorは範囲の先頭行に塗られるため)"""
    return [{"addBanding": {"bandedRange": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": n_cols},
        "rowProperties": {
            "headerColor": HEADER_BLUE,
            "firstBandColor": {"red": 1, "green": 1, "blue": 1},
            "secondBandColor": {"red": 0.96, "green": 0.96, "blue": 0.96}}}}}]


def build_conditional_requests(sheet_id, headers):
    """色分けの条件付き書式。範囲全体に1回設定すれば、あとから追加される行にも効く"""
    reqs = []
    for name in NUMBER_RULE_COLUMNS:
        if name not in headers:
            continue
        rng = _col_range(sheet_id, headers.index(name))
        for cond, value, color in (("NUMBER_GREATER_THAN_EQ", "20", GREEN), ("NUMBER_LESS", "-10", RED)):
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [rng],
                "booleanRule": {"condition": {"type": cond, "values": [{"userEnteredValue": value}]},
                                "format": {"backgroundColor": color}}}}})
    for name, rules in TEXT_RULE_COLUMNS.items():
        if name not in headers:
            continue
        rng = _col_range(sheet_id, headers.index(name))
        for word, color in rules:
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [rng],
                "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": word}]},
                                "format": {"backgroundColor": color}}}}})
    return reqs


def build_cleanup_requests(sheet_meta):
    """既に付いている縞模様・条件付き書式を消すリクエスト(やり直しで二重にならないように)"""
    reqs = []
    sheet_id = sheet_meta.get("properties", {}).get("sheetId")
    for br in sheet_meta.get("bandedRanges", []) or []:
        reqs.append({"deleteBanding": {"bandedRangeId": br["bandedRangeId"]}})
    # 条件付き書式は番号で消すので、0番を件数ぶん繰り返す
    for _ in range(len(sheet_meta.get("conditionalFormats", []) or [])):
        reqs.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}})
    return reqs


def needs_decoration(sheet_meta):
    """縞模様も条件付き書式も付いていないシートは「装飾が未適用」とみなす"""
    return not (sheet_meta.get("bandedRanges") or sheet_meta.get("conditionalFormats"))


def _get_sheet_meta(sh, sheet_id):
    meta = sh.fetch_sheet_metadata(params={
        "fields": "sheets(properties.sheetId,bandedRanges.bandedRangeId,conditionalFormats.ranges)"})
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == sheet_id:
            return s
    return {"properties": {"sheetId": sheet_id}}


def ensure_decoration(sh, ws, headers, call=None, force=False):
    """未適用(または force=True)のシートに装飾をかける。グループごとに独立して実行し、
    失敗したグループは理由をログに出して他は続ける。戻り値: {グループ名: True/False}、適用不要ならNone。
    call: APIクォータ超過時の再試行ラッパ(_sheets_call_with_retry を渡す想定)"""
    call = call or (lambda f, *a, **k: f(*a, **k))
    sheet_id = ws.id
    n_cols = len(headers)
    meta = _get_sheet_meta(sh, sheet_id)
    if not force and not needs_decoration(meta):
        return None

    # 列数が足りないと「範囲がグリッド外」で全リクエストが失敗するので、先に広げる
    if ws.col_count < n_cols:
        ws.resize(cols=n_cols)

    cleanup = build_cleanup_requests(meta)
    groups = [
        ("既存装飾の削除", cleanup),
        ("列幅・折り返し・見出し", build_layout_requests(sheet_id, headers)),
        ("縞模様", build_banding_requests(sheet_id, n_cols)),
        ("色分け", build_conditional_requests(sheet_id, headers)),
    ]
    result = {}
    for name, reqs in groups:
        if not reqs:
            continue
        try:
            call(sh.batch_update, {"requests": reqs})
            result[name] = True
        except Exception as e:
            result[name] = False
            print(f"⚠️ シート装飾[{name}]に失敗(他の装飾は続行): {e}")
    ok = [k for k, v in result.items() if v]
    ng = [k for k, v in result.items() if not v]
    print(f"🎨 シート装飾: 成功={ok or 'なし'} / 失敗={ng or 'なし'}")
    return result
