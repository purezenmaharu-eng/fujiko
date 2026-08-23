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
# スプレッドシートへの書き込み(「監視銘柄」タブを丸ごと置き換え)
# ============================================================
WATCHLIST_HEADERS = [
    "更新日", "銘柄コード", "銘柄名", "市場", "平均売買代金(百万円/日)",
    "現在値", "40週MA", "3年前値", "Fスコア", "Fスコア達成率%",
]


def write_watchlist(rows):
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
    if not creds_json or not spreadsheet_id:
        print("⚠️ スプレッドシート設定未完了 → 書き込みスキップ")
        return
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet("監視銘柄")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="監視銘柄", rows=1000, cols=len(WATCHLIST_HEADERS))
    ws.append_row(WATCHLIST_HEADERS)
    try:
        ws.freeze(rows=1)
    except Exception as e:
        print(f"⚠️ 見出し固定に失敗(処理は継続): {e}")
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"✅ 「監視銘柄」タブに{len(rows)}銘柄を書き込みました")


# ============================================================
# メイン処理
# ============================================================
def main():
    today_str = date.today().strftime("%Y/%m/%d")
    print("🔍 監視銘柄リスト構築を開始します(株おじさん式・四半期実行)")

    tickers, name_map, market_segment_map = get_all_tickers()
    if not tickers:
        print("❌ ティッカー一覧の取得に失敗したため処理を中止します")
        return

    # プライム/スタンダード/グロースのみ対象(その他区分は除外)
    valid_segments = {"プライム", "スタンダード", "グロース"}
    tickers = [t for t in tickers if market_segment_map.get(t) in valid_segments]
    print(f"📋 対象ユニバース: {len(tickers)}銘柄(プライム/スタンダード/グロースのみ)")

    START = (date.today() - pd.Timedelta(days=365 * 4 + 60)).strftime("%Y-%m-%d")
    END = (date.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    liquidity_survivors = []  # (ticker, avg_value)
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
            liquidity_survivors.append((ticker, avg_value, trend_detail))
        except Exception as e:
            failed.append((ticker, str(e)))

    if failed:
        print(f"⚠️ 取得失敗/データなし {len(failed)}件(スキップ)")
    print(f"✅ 流動性+長期トレンド通過: {len(liquidity_survivors)}銘柄")

    # Fスコア判定はクォータ制約があるため、流動性が高い順に上限まで処理
    liquidity_survivors.sort(key=lambda x: x[1], reverse=True)
    if len(liquidity_survivors) > MAX_FSCORE_TICKERS:
        dropped = len(liquidity_survivors) - MAX_FSCORE_TICKERS
        print(f"⚠️ ラジ株ナビ日次クォータ({RADIKABUNAVI_DAILY_LIMIT}件)の制約により、"
              f"流動性下位{dropped}銘柄をFスコア判定の対象から除外します(次回実行時に再評価される可能性あり)")
        candidates = liquidity_survivors[:MAX_FSCORE_TICKERS]
    else:
        candidates = liquidity_survivors

    print(f"📚 Fスコア判定中({len(candidates)}銘柄)...")
    final_rows = []
    for ticker, avg_value, trend_detail in candidates:
        if _radikabunavi_disabled:
            print("⏹️ ラジ株ナビが利用不可のため、残りのFスコア判定を打ち切ります")
            break
        code = ticker.replace(".T", "")
        fin = radikabunavi_call_tool("get_edinet_financial_data", {
            "code": code,
            "metrics": ["roa", "cashFlowFromOperations", "netIncome", "debtToEquityRatio",
                        "currentRatio", "sharesOutstanding", "grossProfitMargin", "assetTurnover"],
        })
        f_score = calc_f_score(fin)
        if not f_score:
            continue
        if f_score["ratio"] < F_SCORE_RATIO_THRESHOLD:
            continue
        final_rows.append([
            today_str,
            code,
            name_map.get(ticker, ticker),
            market_segment_map.get(ticker, "－"),
            round(avg_value / 1_000_000, 1),
            trend_detail.get("currentPrice", "－"),
            trend_detail.get("ma40", "－"),
            trend_detail.get("pastPrice", "－"),
            f"{f_score['score']}/{f_score['maxScore']}",
            round(f_score["ratio"] * 100, 1),
        ])
        time.sleep(1.0)

    print(f"✅ 最終監視銘柄数: {len(final_rows)}銘柄")
    write_watchlist(final_rows)


if __name__ == "__main__":
    main()
