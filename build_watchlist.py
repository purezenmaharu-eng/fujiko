"""
フジコ投資法 - 監視銘柄リスト構築スクリプト(Stage A)
============================================================
株おじさんの銘柄選定手法(note記事シリーズ)を参考に、日次のフジコ(Ace/King/Polygraph/BEP)を
「タイミング計測ツール」として正しく機能させるための事前選別ステージ。

株おじさんの記事の考え方:
  フジコやMA戦略はタイミングツール。銘柄選定と混同すると理解が難しくなる。
  銘柄選定は「事業立地・政治経済動向・ファンダメンタル・テクニカル(長期トレンド)」の
  4視点で行い、そこで絞り込んだ監視銘柄リストに対してのみ、タイミングツールを適用する。

本スクリプトが実装するのは、上記4視点のうち自動化可能な部分(ファンダメンタル・
テクニカルの長期トレンド・流動性)。「事業立地」「政治経済動向」は定性判断のため対象外。

処理フロー(クォータ制約を踏まえた順序):
  1. 流動性フィルタ(yfinance、無料、全銘柄に適用)
     直近20日平均売買代金 >= 3億円/日(株おじさん記事の「数億円/日以上」という目安を採用)
  2. 長期上昇トレンドフィルタ(yfinance週足、無料、1の通過銘柄に適用)
     週足40週線が右肩上がり + 現在値が40週線以上 + 現在値が3年前より高い
     (記事の「週足対数10年チャート+月足RSIで数年スパンの右肩上がり確認」をシンプルなルールで代用)
  3. Fスコア判定(EDINET、ラジ株ナビAPI経由、クォータ制約あり、1・2の通過銘柄だけに適用)
     ピオトロスキー式9項目のうち達成率6/9以上(記事の「Fスコア6点以上が目安」を採用)
  4. 結果をスプレッドシートの「監視銘柄」タブに書き込み

実行頻度: 四半期(年4回)を想定。株おじさんの記事の「四半期指標:年4回見直し」「本格見直し:
年1〜2回」に合わせ、日次のdaily.ymlとは別の専用ワークフロー(quarterly_watchlist.yml)から
四半期に一度だけ実行する。

日次のfujiko.py側は、このスクリプトが書き込んだ「監視銘柄」タブを読み込み、Ace/King/
Polygraph/BEPのスキャン対象をこのリストに限定する(fujiko.py側の対応は別途実施)。
"""
import os
import json
import time
import hashlib
from datetime import date
import numpy as np
import pandas as pd
import yfinance as yf
import requests
import jquantsapi
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# ラジ株ナビMCP設定(fujiko.pyと同じ接続パターンを踏襲)
# ============================================================
RADIKABUNAVI_MCP_URL = "https://radikabunavi.com/mcp"
RADIKABUNAVI_API_KEY = os.environ.get("RADIKABUNAVI_API_KEY", "")
RADIKABUNAVI_DAILY_LIMIT = 150
CALLS_PER_TICKER = 1  # build_watchlistはget_edinet_financial_dataのみ呼ぶ(get_stock_scoreは呼ばない)
MAX_FSCORE_TICKERS = RADIKABUNAVI_DAILY_LIMIT // CALLS_PER_TICKER

_radikabunavi_session_id = None
_radikabunavi_disabled = False

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
_CACHE_DATE = date.today().strftime("%Y-%m-%d")


def _post_with_429_retry(url, label, max_retries=3, **kwargs):
    resp = None
    for attempt in range(max_retries):
        resp = requests.post(url, **kwargs)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else 3 * (2 ** attempt)
            except ValueError:
                wait = 3 * (2 ** attempt)
            print(f"⚠️ {label}: 429 Too Many Requests → {wait:.0f}秒待機してリトライ({attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        return resp
    return resp


def _radikabunavi_request(method, params=None, request_id=1):
    global _radikabunavi_session_id
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if RADIKABUNAVI_API_KEY:
        headers["Authorization"] = f"Bearer {RADIKABUNAVI_API_KEY}"
    if _radikabunavi_session_id:
        headers["Mcp-Session-Id"] = _radikabunavi_session_id
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    resp = _post_with_429_retry(
        RADIKABUNAVI_MCP_URL, "ラジ株ナビ",
        json=payload, headers=headers, timeout=30,
    )
    resp.raise_for_status()
    if "Mcp-Session-Id" in resp.headers:
        _radikabunavi_session_id = resp.headers["Mcp-Session-Id"]
    content_type = resp.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise RuntimeError("ラジ株ナビ: SSEレスポンスにdataが見つかりません")
    if not resp.text.strip():
        return {}
    return resp.json()


def _radikabunavi_ensure_session():
    global _radikabunavi_session_id
    if _radikabunavi_session_id:
        return
    _radikabunavi_request("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "fujiko-watchlist-bot", "version": "1.0"},
    }, request_id=1)
    try:
        _radikabunavi_request("notifications/initialized", {}, request_id=2)
    except Exception:
        pass


def _cache_key(tool_name, arguments):
    arg_hash = hashlib.md5(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:12]
    return os.path.join(_CACHE_DIR, f"{_CACHE_DATE}_{tool_name}_{arg_hash}.json")


def _cache_read(tool_name, arguments):
    path = _cache_key(tool_name, arguments)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _cache_write(tool_name, arguments, data):
    if data is None:
        return
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_key(tool_name, arguments)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def radikabunavi_call_tool(tool_name, arguments):
    """ラジ株ナビMCPのツールを呼び出し、結果(dict)を返す。失敗時はNone。"""
    global _radikabunavi_disabled
    if not RADIKABUNAVI_API_KEY or _radikabunavi_disabled:
        return None
    cached = _cache_read(tool_name, arguments)
    if cached is not None:
        return cached
    try:
        _radikabunavi_ensure_session()
        result = _radikabunavi_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, request_id=3)
        if "error" in result:
            print(f"⚠️ ラジ株ナビAPIエラー({tool_name}, {arguments}): {result['error']}")
            return None
        content = result.get("result", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                try:
                    parsed = json.loads(block["text"])
                    _cache_write(tool_name, arguments, parsed)
                    return parsed
                except json.JSONDecodeError:
                    return {"raw_text": block["text"]}
        return None
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            print(f"❌ ラジ株ナビ認証エラー({status}) → 以降のEDINET取得をスキップします")
            _radikabunavi_disabled = True
        elif status == 429:
            print("❌ ラジ株ナビ: 429が解消しないため日次/月次クォータ超過と判断 → 以降のEDINET取得をスキップします")
            _radikabunavi_disabled = True
        else:
            print(f"⚠️ ラジ株ナビ呼び出し失敗({tool_name}, {arguments}): {e}")
        return None
    except Exception as e:
        print(f"⚠️ ラジ株ナビ呼び出し失敗({tool_name}, {arguments}): {e}")
        return None


# ============================================================
# 日本株ティッカー一覧取得(fujiko.pyのget_all_tickersと同じロジック)
# ============================================================
def get_all_tickers():
    try:
        api_key = os.environ.get("JQUANTS_API_KEY", "")
        if not api_key:
            raise RuntimeError("JQUANTS_API_KEY未設定")
        cli = jquantsapi.ClientV2(api_key=api_key)
        df_list = cli.get_list()
        df_stocks = df_list[df_list['S33'] != '9999'].copy()
        tickers = [str(code)[:-1] + ".T" for code in df_stocks['Code'].astype(str)]
        names = df_stocks['CoName'].tolist()
        name_map = dict(zip(tickers, names))
        TSE_MARKET_CODE_NAMES = {
            "111": "プライム", "112": "スタンダード", "113": "グロース",
            "0111": "プライム", "0112": "スタンダード", "0113": "グロース",
            "0105": "その他", "0106": "その他", "0107": "その他",
            "0109": "その他", "0110": "その他",
        }
        market_segment_map = {}
        for col in ["MarketCodeName", "MarketCode", "Market", "MarketName", "Mkt", "MktName", "S19", "S19Name"]:
            if col in df_stocks.columns:
                raw_values = df_stocks[col].astype(str).tolist()
                converted = [TSE_MARKET_CODE_NAMES.get(v, v) for v in raw_values]
                market_segment_map = dict(zip(tickers, converted))
                break
        print(f"✅ J-Quants: {len(tickers)}銘柄取得成功(ETF除外済)")
        return tickers, name_map, market_segment_map
    except Exception as e:
        print(f"⚠️ J-Quants取得失敗({e}) → 監視銘柄リスト構築を中止します")
        return [], {}, {}


# ============================================================
# Stage A-1: 流動性フィルタ
# ============================================================
LIQUIDITY_THRESHOLD_YEN = 300_000_000  # 3億円/日(株おじさん記事の「数億円/日以上」を採用)
LIQUIDITY_LOOKBACK_DAYS = 20


def check_liquidity(daily_df):
    """直近20日平均売買代金が閾値以上か。データ不足時はFalse(除外)。"""
    if daily_df is None or len(daily_df) < LIQUIDITY_LOOKBACK_DAYS:
        return False, None
    recent = daily_df.tail(LIQUIDITY_LOOKBACK_DAYS)
    avg_value = float((recent["Close"] * recent["Volume"]).mean())
    return avg_value >= LIQUIDITY_THRESHOLD_YEN, avg_value


# ============================================================
# Stage A-2: 長期上昇トレンドフィルタ(週足ベース、シンプルなルール)
# ============================================================
TREND_YEARS_BACK = 3
TREND_MA_WEEKS = 40


def check_long_term_trend(daily_df):
    """日足データを週足にリサンプルし、以下を判定:
      - 40週MAが直近5週で右肩上がり(下向きでない)
      - 現在値が40週MA以上
      - 現在値が3年前の値より高い
    データ不足時はFalse(除外)。"""
    if daily_df is None or daily_df.empty:
        return False, {}
    weekly = daily_df["Close"].resample("W").last().dropna()
    min_weeks_needed = TREND_MA_WEEKS + 5
    if len(weekly) < min_weeks_needed:
        return False, {}
    ma40 = weekly.rolling(TREND_MA_WEEKS).mean()
    if ma40.iloc[-1] is None or pd.isna(ma40.iloc[-1]) or pd.isna(ma40.iloc[-5]):
        return False, {}
    ma_rising = ma40.iloc[-1] >= ma40.iloc[-5]
    current_price = weekly.iloc[-1]
    above_ma = current_price >= ma40.iloc[-1]

    weeks_back = TREND_YEARS_BACK * 52
    if len(weekly) > weeks_back:
        past_price = weekly.iloc[-(weeks_back + 1)]
    else:
        past_price = weekly.iloc[0]  # 3年分のデータが無ければ取得できる最古値と比較
    higher_than_past = current_price > past_price

    passed = bool(ma_rising and above_ma and higher_than_past)
    detail = {
        "currentPrice": round(float(current_price), 1),
        "ma40": round(float(ma40.iloc[-1]), 1),
        "pastPrice": round(float(past_price), 1),
        "maRising": bool(ma_rising),
        "aboveMa": bool(above_ma),
        "higherThanPast": bool(higher_than_past),
    }
    return passed, detail


# ============================================================
# Stage A-3: Fスコア(ピオトロスキー式9項目、EDINETデータの直近2期から算出)
# ============================================================
F_SCORE_RATIO_THRESHOLD = 6 / 9  # 記事の「6点以上」を9項目換算の達成率として採用


def calc_f_score(fin_data):
    """ピオトロスキー式Fスコアを算出する。
    戻り値: dict(score, maxScore, ratio) or None(データ不足時)"""
    if not fin_data:
        return None
    try:
        fiscal_years = fin_data.get("fiscalYears") or {}
        if not isinstance(fiscal_years, dict) or len(fiscal_years) < 2:
            return None
        years_sorted = sorted(fiscal_years.keys())
        latest = fiscal_years.get(years_sorted[-1]) or {}
        prior = fiscal_years.get(years_sorted[-2]) or {}

        checks = []

        def add_check(value):
            if value is not None:
                checks.append(bool(value))

        # 1. ROA > 0(収益性)
        roa = latest.get("roa")
        if roa is not None:
            add_check(float(roa) > 0)

        # 2. 営業CF > 0(収益性)
        cfo = latest.get("cashFlowFromOperations")
        if cfo is not None:
            add_check(float(cfo) > 0)

        # 3. ΔROA > 0(収益性の改善)
        roa_prior = prior.get("roa")
        if roa is not None and roa_prior is not None:
            add_check(float(roa) > float(roa_prior))

        # 4. 営業CF > 当期純利益(利益の質)
        ni = latest.get("netIncome")
        if cfo is not None and ni is not None:
            add_check(float(cfo) > float(ni))

        # 5. DEレシオ低下(レバレッジ改善)
        de = latest.get("debtToEquityRatio")
        de_prior = prior.get("debtToEquityRatio")
        if de is not None and de_prior is not None:
            add_check(float(de) < float(de_prior))

        # 6. 流動比率上昇(流動性改善)
        cr = latest.get("currentRatio")
        cr_prior = prior.get("currentRatio")
        if cr is not None and cr_prior is not None:
            add_check(float(cr) > float(cr_prior))

        # 7. 希薄化なし(発行済株式数が増えていない)
        so = latest.get("sharesOutstanding")
        so_prior = prior.get("sharesOutstanding")
        if so is not None and so_prior is not None:
            add_check(float(so) <= float(so_prior))

        # 8. 粗利率上昇(効率性改善)
        gm = latest.get("grossProfitMargin")
        gm_prior = prior.get("grossProfitMargin")
        if gm is not None and gm_prior is not None:
            add_check(float(gm) > float(gm_prior))

        # 9. 総資産回転率上昇(効率性改善)
        at = latest.get("assetTurnover")
        at_prior = prior.get("assetTurnover")
        if at is not None and at_prior is not None:
            add_check(float(at) > float(at_prior))

        if not checks:
            return None
        score = sum(1 for c in checks if c)
        max_score = len(checks)
        ratio = score / max_score
        return {"score": score, "maxScore": max_score, "ratio": round(ratio, 3)}
    except Exception as e:
        print(f"⚠️ Fスコア算出失敗: {e}")
        return None




# ============================================================
# スプレッドシート構成
# ============================================================
# 「監視銘柄_作業用」タブ: 流動性+長期トレンドを通過した候補全件を保持する作業台帳。
#   Fスコア判定はクォータ制約(150件/日)により複数日に分けて進めるため、
#   「済み」列で処理状況を管理し、四半期の初回実行で候補を書き込んだ後、
#   翌日以降の実行では未処理分だけを追加でFスコア判定していく。
# 「監視銘柄」タブ: 全候補のFスコア判定が完了した時点で、合格銘柄のみを書き出す最終成果物。
#   日次のfujiko.pyはこのタブだけを読む。
WORK_SHEET_NAME = "監視銘柄_作業用"
FINAL_SHEET_NAME = "監視銘柄"

WORK_HEADERS = [
    "四半期", "銘柄コード", "銘柄名", "市場", "平均売買代金(百万円/日)",
    "現在値", "40週MA", "3年前値", "済み", "Fスコア", "Fスコア達成率%", "判定",
]
FINAL_HEADERS = [
    "更新日", "銘柄コード", "銘柄名", "市場", "平均売買代金(百万円/日)",
    "現在値", "40週MA", "3年前値", "Fスコア", "Fスコア達成率%",
]


def current_quarter_label():
    """現在日付から '2026-Q3' のような四半期ラベルを生成する"""
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{q}"


def _open_spreadsheet():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
    if not creds_json or not spreadsheet_id:
        print("⚠️ スプレッドシート設定未完了")
        return None
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(spreadsheet_id)


def read_work_sheet(sh):
    """「監視銘柄_作業用」タブを読み込む。存在しない場合はNoneを返す。"""
    try:
        ws = sh.worksheet(WORK_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return None, None
    records = ws.get_all_records()
    return ws, records


def write_work_sheet_fresh(sh, rows):
    """流動性+長期トレンド通過候補で「監視銘柄_作業用」タブを丸ごと作り直す(四半期の初回実行時)"""
    try:
        ws = sh.worksheet(WORK_SHEET_NAME)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORK_SHEET_NAME, rows=max(len(rows) + 10, 100), cols=len(WORK_HEADERS))
    ws.append_row(WORK_HEADERS)
    try:
        ws.freeze(rows=1)
    except Exception as e:
        print(f"⚠️ 見出し固定に失敗(処理は継続): {e}")
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    return ws


def update_work_sheet_rows(ws, records, updates):
    """処理済みになった行を「監視銘柄_作業用」タブに書き戻す。
    updates: {row_index(0始まり、recordsに対応) -> {済み, Fスコア, Fスコア達成率%, 判定}}"""
    if not updates:
        return
    col_index = {name: i + 1 for i, name in enumerate(WORK_HEADERS)}
    cell_updates = []
    for row_idx, values in updates.items():
        sheet_row = row_idx + 2  # ヘッダー行の次から
        for col_name, value in values.items():
            cell_updates.append({
                "range": gspread.utils.rowcol_to_a1(sheet_row, col_index[col_name]),
                "values": [[value]],
            })
    if cell_updates:
        ws.batch_update(cell_updates, value_input_option="USER_ENTERED")


def write_final_watchlist(sh, records):
    """全候補の処理が完了した時点で、合格銘柄のみを「監視銘柄」タブに書き出す"""
    today_str = date.today().strftime("%Y/%m/%d")
    rows = []
    for rec in records:
        if str(rec.get("判定", "")).strip() != "合格":
            continue
        rows.append([
            today_str,
            rec.get("銘柄コード", ""),
            rec.get("銘柄名", ""),
            rec.get("市場", ""),
            rec.get("平均売買代金(百万円/日)", ""),
            rec.get("現在値", ""),
            rec.get("40週MA", ""),
            rec.get("3年前値", ""),
            rec.get("Fスコア", ""),
            rec.get("Fスコア達成率%", ""),
        ])
    try:
        ws = sh.worksheet(FINAL_SHEET_NAME)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=FINAL_SHEET_NAME, rows=max(len(rows) + 10, 100), cols=len(FINAL_HEADERS))
    ws.append_row(FINAL_HEADERS)
    try:
        ws.freeze(rows=1)
    except Exception as e:
        print(f"⚠️ 見出し固定に失敗(処理は継続): {e}")
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"✅ 「{FINAL_SHEET_NAME}」タブに{len(rows)}銘柄を書き込みました(全候補の判定完了)")


# ============================================================
# Phase 1: 流動性+長期トレンドのスクリーニング(四半期の初回実行時のみ)
# ============================================================
def screen_liquidity_and_trend():
    """全銘柄に対して流動性+長期トレンドフィルタを適用し、通過候補のリストを返す。
    戻り値: list of [四半期, 銘柄コード, 銘柄名, 市場, 平均売買代金, 現在値, 40週MA, 3年前値, 済み, Fスコア, 達成率%, 判定]"""
    quarter = current_quarter_label()
    tickers, name_map, market_segment_map = get_all_tickers()
    if not tickers:
        print("❌ ティッカー一覧の取得に失敗したため処理を中止します")
        return []

    valid_segments = {"プライム", "スタンダード", "グロース"}
    tickers = [t for t in tickers if market_segment_map.get(t) in valid_segments]
    print(f"📋 対象ユニバース: {len(tickers)}銘柄(プライム/スタンダード/グロースのみ)")

    START = (date.today() - pd.Timedelta(days=365 * 4 + 60)).strftime("%Y-%m-%d")
    END = (date.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    survivors = []
    failed = []
    print("🚀 流動性・長期トレンドのスクリーニング中(yfinance)...")
    for ticker in tickers:
        try:
            df = yf.download(ticker, start=START, end=END, auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty:
                failed.append((ticker, "データなし"))
                continue
            liquid_ok, avg_value = check_liquidity(df)
            if not liquid_ok:
                continue
            trend_ok, trend_detail = check_long_term_trend(df)
            if not trend_ok:
                continue
            survivors.append((ticker, avg_value, trend_detail))
        except Exception as e:
            failed.append((ticker, str(e)))

    if failed:
        print(f"⚠️ 取得失敗/データなし {len(failed)}件(スキップ)")
    print(f"✅ 流動性+長期トレンド通過: {len(survivors)}銘柄")

    # 流動性が高い順に並べ、Fスコア判定を流動性上位から進められるようにする
    survivors.sort(key=lambda x: x[1], reverse=True)

    rows = []
    for ticker, avg_value, trend_detail in survivors:
        code = ticker.replace(".T", "")
        rows.append([
            quarter, code, name_map.get(ticker, ticker), market_segment_map.get(ticker, "－"),
            round(avg_value / 1_000_000, 1),
            trend_detail.get("currentPrice", "－"), trend_detail.get("ma40", "－"),
            trend_detail.get("pastPrice", "－"),
            "FALSE", "", "", "",
        ])
    return rows


# ============================================================
# Phase 2: Fスコア判定(未処理分から日次クォータ上限まで処理)
# ============================================================
def score_pending_candidates(ws, records):
    """「済み」がFALSEの行から、ラジ株ナビの日次クォータ上限まで処理し、結果を書き戻す。
    戻り値: 今回処理できた件数"""
    pending_indices = [i for i, rec in enumerate(records) if str(rec.get("済み", "")).strip().upper() != "TRUE"]
    if not pending_indices:
        print("✅ 全候補のFスコア判定が完了済みです")
        return 0

    print(f"📚 Fスコア判定中: 未処理{len(pending_indices)}件のうち最大{MAX_FSCORE_TICKERS}件を処理します")
    updates = {}
    processed = 0
    for idx in pending_indices:
        if processed >= MAX_FSCORE_TICKERS:
            break
        if _radikabunavi_disabled:
            print("⏹️ ラジ株ナビが利用不可のため、残りのFスコア判定を打ち切ります(次回実行で継続)")
            break
        rec = records[idx]
        code = str(rec.get("銘柄コード", "")).strip()
        if not code:
            continue
        fin = radikabunavi_call_tool("get_edinet_financial_data", {
            "code": code,
            "metrics": ["roa", "cashFlowFromOperations", "netIncome", "debtToEquityRatio",
                        "currentRatio", "sharesOutstanding", "grossProfitMargin", "assetTurnover"],
        })
        f_score = calc_f_score(fin)
        processed += 1
        if not f_score:
            # データ不足で判定不能な銘柄は「済み」にして再処理を避け、不合格扱いにする
            updates[idx] = {"済み": "TRUE", "Fスコア": "－", "Fスコア達成率%": "－", "判定": "不合格(データ不足)"}
            continue
        verdict = "合格" if f_score["ratio"] >= F_SCORE_RATIO_THRESHOLD else "不合格"
        updates[idx] = {
            "済み": "TRUE",
            "Fスコア": f"{f_score['score']}/{f_score['maxScore']}",
            "Fスコア達成率%": round(f_score["ratio"] * 100, 1),
            "判定": verdict,
        }
        time.sleep(1.0)

    update_work_sheet_rows(ws, records, updates)
    print(f"✅ 今回のFスコア判定: {len(updates)}件処理")
    return len(updates)


# ============================================================
# メイン処理
# ============================================================
def main():
    print("🔍 監視銘柄リスト構築を開始します(株おじさん式・四半期実行・複数日分割処理対応)")
    quarter = current_quarter_label()

    sh = _open_spreadsheet()
    if sh is None:
        print("❌ スプレッドシートに接続できないため処理を中止します")
        return

    ws, records = read_work_sheet(sh)
    existing_quarter = records[0].get("四半期") if records else None

    if ws is None or not records or existing_quarter != quarter:
        # 新しい四半期の初回実行、またはタブ未作成 → Phase 1からやり直す
        print(f"🆕 新しい四半期({quarter})の監視銘柄リスト構築を開始します(流動性+長期トレンドを再スクリーニング)")
        rows = screen_liquidity_and_trend()
        if not rows:
            print("⚠️ 流動性+長期トレンド通過銘柄が0件のため処理を中止します")
            return
        ws = write_work_sheet_fresh(sh, rows)
        _, records = read_work_sheet(sh)
    else:
        print(f"📋 既存の作業用リスト({quarter})を継続処理します(候補{len(records)}件)")

    score_pending_candidates(ws, records)

    # 処理後、全件済みになったかを再チェックして最終タブを更新
    _, records_after = read_work_sheet(sh)
    if records_after and all(str(r.get("済み", "")).strip().upper() == "TRUE" for r in records_after):
        write_final_watchlist(sh, records_after)
    else:
        remaining = sum(1 for r in (records_after or []) if str(r.get("済み", "")).strip().upper() != "TRUE")
        print(f"⏳ 未処理が{remaining}件残っています。翌日以降の実行で継続します"
              f"(「{FINAL_SHEET_NAME}」タブは全件処理完了まで更新されません)")


if __name__ == "__main__":
    main()
