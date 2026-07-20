import os
import json
import time
import io
import numpy as np
import pandas as pd
import yfinance as yf
import requests
import jquantsapi
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

# ============================================================
# ラジ株ナビMCP設定 (EDINETベース財務データ・業績予想)
# ============================================================
RADIKABUNAVI_MCP_URL = "https://radikabunavi.com/mcp"
RADIKABUNAVI_API_KEY = os.environ.get("RADIKABUNAVI_API_KEY", "")

# Gemini設定 (ファンダメンタルズ解説コメント生成)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.0-flash"

def _post_with_429_retry(url, label, max_retries=5, **kwargs):
    """429(レート制限)時にRetry-Afterヘッダー(なければ指数バックオフ)で待ってからリトライする"""
    resp = None
    for attempt in range(max_retries):
        resp = requests.post(url, **kwargs)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else min(60, 5 * (2 ** attempt))
            except ValueError:
                wait = min(60, 5 * (2 ** attempt))
            print(f"⚠️ {label}: 429 Too Many Requests → {wait:.0f}秒待機してリトライ({attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        return resp
    return resp  # 最後まで429だった場合はそのまま返す(呼び出し元でエラーログに残る)

# ============================================================
# 131銘柄リスト
# ============================================================
TICKER_NAME_MAP = {
    "2173.T": "博展", "7080.T": "スポーツフィールド", "7120.T": "SHINKO", "5285.T": "ヤマックス",
    "7608.T": "エスケイジャパン", "5843.T": "ニッポンインシュア", "3565.T": "アセンテック",
    "7373.T": "アイドマ・ホールディングス", "6083.T": "ERIホールディングス", "6200.T": "インソース",
    "7792.T": "コラントッテ", "4374.T": "ROBOT PAYMENT", "6547.T": "グリーンズ", "4012.T": "アクシス",
    "9560.T": "プログリット", "7033.T": "マネジメントソリューションズ", "5592.T": "くすりの窓口",
    "3371.T": "ソフトクリエイトHD", "9346.T": "コベース", "7187.T": "ジェイリース", "4486.T": "ユナイトアンドグロウ",
    "3922.T": "PR TIMES", "4270.T": "BeeX", "137A.T": "Voluntas", "4932.T": "アルマード",
    "3921.T": "ネオジャパン", "2180.T": "サニーサイドアップグループ", "5575.T": "Globee", "4495.T": "アイキューブドシステムズ",
    "3771.T": "システムリサーチ", "4482.T": "ウィルズ", "3989.T": "シェアリングテクノロジー", "9343.T": "アイビス",
    "4396.T": "システムサポート", "2924.T": "イフジ産業", "6086.T": "シンプロメンテ", "4058.T": "シグマクシスHD",
    "6037.T": "楽待", "6195.T": "ホープ", "3679.T": "じげん", "4492.T": "ゼネテック", "4377.T": "ワンキャリア",
    "156A.T": "マツキヨコカラ", "3093.T": "トレジャー・ファクトリー", "6099.T": "エラン",
    "7059.T": "コプロ・ホールディングス", "5038.T": "eWeLL", "9564.T": "FCE", "3496.T": "アズーム",
    "7134.T": "みずほリース", "3484.T": "テンポイノベーション", "4415.T": "ブロードエンタープライズ",
    "4441.T": "トビラシステムズ", "6231.T": "木村工機", "4475.T": "HENNGE", "3984.T": "ユーザーローカル",
    "3939.T": "カナミックネットワーク", "4323.T": "日本システム技術", "9554.T": "AViC", "9556.T": "INTLOOP",
    "4493.T": "サイバーセキュリティクラウド", "7082.T": "ジモティー", "9325.T": "ファイズHD",
    "4431.T": "スマレジ", "4417.T": "グローバルセキュリティエキスパート", "3692.T": "FFRIセキュリティ",
    "5032.T": "ANYCOLOR", "5273.T": "三谷セキサン", "2767.T": "円谷フィールズHD", "5290.T": "ベルテクスコーポレーション",
    "2124.T": "ジェイエイシーリクルートメント", "8057.T": "内田洋行", "4776.T": "サイボウズ", "2317.T": "システナ",
    "3854.T": "アイル", "6331.T": "三菱化工機", "1952.T": "新日本空調", "6196.T": "ストライク",
    "3399.T": "丸千代山岡家", "3733.T": "ソフトウェア・サービス", "4674.T": "クレスコ", "3153.T": "八洲電機",
    "6226.T": "守谷輸送機工業", "3076.T": "トーエル", "4507.T": "塩野義製薬", "2127.T": "日本M&AセンターHD",
    "8136.T": "サンリオ", "4848.T": "フルキャストHD", "8739.T": "スパークス・グループ", "7609.T": "ダイトロン",
    "4194.T": "ビジョナル", "9552.T": "M&A総研ホールディングス", "2726.T": "パルグループHD", "6532.T": "ベイカレント・コンサルティング",
    "3762.T": "テクマトリックス", "9746.T": "TKC", "4390.T": "アイ・ピー・エス", "7218.T": "田中精密工業",
    "1969.T": "高砂熱学工業", "7003.T": "三井E&S", "4768.T": "大塚商会", "4290.T": "プレステージ・インターナショナル",
    "7936.T": "アシックス", "4071.T": "プラスアルファ・コンサルティング", "2780.T": "コメ兵ホールディングス",
    "9697.T": "カプコン", "6857.T": "アドバンテスト", "4021.T": "日産化学", "6920.T": "レーザーテック",
    "3064.T": "MonotaRO", "4413.T": "ボードルア", "7611.T": "ハイデイ日高", "6946.T": "日本アビオニクス",
    "3445.T": "RS Technologies", "6055.T": "ジャパンマテリアル", "7906.T": "ヨネックス", "8061.T": "西華産業",
    "7734.T": "理研計器", "8697.T": "日本取引所グループ", "8919.T": "カチタス", "3697.T": "SHIFT",
    "2371.T": "カカクコム", "6544.T": "ジャパンエレベーターサービスHD", "5334.T": "日本特殊陶業",
    "6777.T": "santec holdings", "5805.T": "SWCC", "4527.T": "ロート製薬", "2157.T": "コシダカHD",
    "3769.T": "GMOペイメントゲートウェイ", "4568.T": "第一三共", "9766.T": "コナミグループ"
}

# ============================================================
# 市場設定(日本株/米国株の切り替え)
# ============================================================
MARKET = os.environ.get("MARKET", "JP").upper()  # "JP" または "US"

# 米国株フォールバック用(S&P500取得失敗時の主要銘柄リスト)
US_FALLBACK_MAP = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon",
    "NVDA": "NVIDIA", "META": "Meta Platforms", "TSLA": "Tesla", "BRK-B": "Berkshire Hathaway",
    "AVGO": "Broadcom", "JPM": "JPMorgan Chase", "LLY": "Eli Lilly", "V": "Visa",
    "UNH": "UnitedHealth", "XOM": "Exxon Mobil", "MA": "Mastercard", "COST": "Costco",
    "HD": "Home Depot", "PG": "Procter & Gamble", "JNJ": "Johnson & Johnson", "NFLX": "Netflix",
    "ABBV": "AbbVie", "BAC": "Bank of America", "CRM": "Salesforce", "ORCL": "Oracle",
    "KO": "Coca-Cola", "MRK": "Merck", "AMD": "Advanced Micro Devices", "PEP": "PepsiCo",
    "ADBE": "Adobe", "WMT": "Walmart",
}

def get_us_tickers():
    """S&P500構成銘柄をWikipediaから取得(失敗時は主要30銘柄で代替)"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FujikoBot/1.0; +https://github.com/purezenmaharu-eng/fujiko)"}
        resp = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=headers, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        df = tables[0]
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        names = df["Security"].tolist()
        name_map = dict(zip(tickers, names))
        print(f"✅ S&P500: {len(tickers)}銘柄取得成功")
        return tickers, name_map
    except Exception as e:
        print(f"⚠️ S&P500リスト取得失敗({e}) → 主要30銘柄を使用")
        return list(US_FALLBACK_MAP.keys()), US_FALLBACK_MAP

def chart_url(ticker):
    """銘柄チャートへのリンク(TradingViewに統一)"""
    if MARKET == "US":
        # 取引所(NASDAQ/NYSE等)が銘柄ごとに異なるため、自動解決される/symbols/形式を使用
        return f"https://www.tradingview.com/symbols/{ticker}/"
    code = ticker.replace(".T", "")
    return f"https://www.tradingview.com/chart/?symbol=TSE%3A{code}"

# ============================================================
# LINE通知設定 (GAS経由)
# ============================================================
# 公開リポジトリにURLを直書きしないよう、GitHub Secrets経由で読み込む
GAS_URL = os.environ.get("GAS_URL", "")
GAS_TOKEN = os.environ.get("GAS_TOKEN", "")  # GAS側で照合する合言葉(なりすまし防止)

def send_line(message):
    if not GAS_URL:
        print("⚠️ GAS_URL未設定 → LINE通知スキップ")
        return
    try:
        res = requests.post(GAS_URL, json={"message": message, "token": GAS_TOKEN}, timeout=10)
        if res.status_code == 200:
            print("✅ LINE送信完了")
        else:
            print(f"❌ LINE送信失敗: {res.status_code}")
    except Exception as e:
        print(f"❌ LINE送信エラー: {e}")

# ============================================================
# ラジ株ナビMCP経由でEDINET財務データ・業績予想を取得
# ============================================================
# MCP(Model Context Protocol) Streamable HTTPトランスポートでJSON-RPCリクエストを送る。
# セッションIDはサーバーが発行するMcp-Session-Idヘッダーを使い回す。
_radikabunavi_session_id = None
_radikabunavi_disabled = False  # 認証エラー等で使用不可と判定したら以降スキップ

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
        # SSE形式("data: {...}"行)の場合はdata行のJSONを取り出す
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
        "clientInfo": {"name": "fujiko-bot", "version": "1.0"},
    }, request_id=1)
    try:
        _radikabunavi_request("notifications/initialized", {}, request_id=2)
    except Exception:
        pass  # 通知はレスポンス不要な場合があるため失敗しても無視

def radikabunavi_call_tool(tool_name, arguments):
    """ラジ株ナビMCPのツールを呼び出し、結果(dict)を返す。失敗時はNone"""
    global _radikabunavi_disabled
    if not RADIKABUNAVI_API_KEY or _radikabunavi_disabled:
        return None
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
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return {"raw_text": block["text"]}
        return None
    except requests.exceptions.HTTPError as e:
        # 401/403等の認証エラーは以降呼び出しても無駄なので停止する
        if e.response is not None and e.response.status_code in (401, 403):
            print(f"❌ ラジ株ナビ認証エラー({e.response.status_code}) → 以降のEDINET取得をスキップします")
            _radikabunavi_disabled = True
        else:
            print(f"⚠️ ラジ株ナビ呼び出し失敗({tool_name}, {arguments}): {e}")
        return None
    except Exception as e:
        print(f"⚠️ ラジ株ナビ呼び出し失敗({tool_name}, {arguments}): {e}")
        return None

def get_fundamental_data(ticker):
    """EDINET財務データ(推移+会社予想)と決算短信ベースの業績予想を取得"""
    code = ticker.replace(".T", "")
    fin = radikabunavi_call_tool("get_edinet_financial_data", {
        "code": code,
        "metrics": ["netSales", "operatingIncome", "netIncome", "salesGrowth", "operatingMargin"],
    })
    forecast = radikabunavi_call_tool("get_earnings_forecast", {"code": code})
    return fin, forecast

def generate_gemini_commentary(name, ticker, fin_data, forecast_data):
    """決算・業績データをもとに、Geminiで短い解説コメントを生成する"""
    if not GEMINI_API_KEY:
        return ""
    if not fin_data and not forecast_data:
        return ""
    try:
        prompt = (
            f"以下は日本株「{name}」({ticker})の決算・業績データです。\n"
            "これをもとに、日本語で40〜60字程度の一言コメントを作成してください。\n"
            "条件:\n"
            "- 売上高・利益の直近の伸び率と、通期予想との比較(増収増益/減収減益、サプライズの有無)を踏まえること\n"
            "- 「買い」「売り」など断定的な投資判断は書かず、事実ベースの短評にすること\n"
            "- 絵文字や記号装飾は使わず、文章のみで出力すること\n\n"
            f"財務データ(JSON): {json.dumps(fin_data, ensure_ascii=False)[:2000]}\n"
            f"業績予想(JSON): {json.dumps(forecast_data, ensure_ascii=False)[:1500]}\n"
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        resp = _post_with_429_retry(
            url, "Gemini",
            json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text.replace("\n", " ")
    except Exception as e:
        print(f"⚠️ Gemini解説生成失敗({name}): {e}")
        return ""

def build_fundamental_commentaries(tickers, ticker_name_map):
    """点灯銘柄それぞれについてEDINETデータを取得しGemini解説を生成する。ticker→コメント文字列の辞書を返す"""
    commentaries = {}
    if not RADIKABUNAVI_API_KEY:
        print("⚠️ RADIKABUNAVI_API_KEY未設定 → ファンダメンタルズ解説をスキップ")
        return commentaries
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY未設定 → ファンダメンタルズ解説をスキップ")
        return commentaries
    print(f"📚 ファンダメンタルズ解説生成中({len(tickers)}銘柄)...")
    for ticker in tickers:
        name = ticker_name_map.get(ticker, ticker)
        fin, forecast = get_fundamental_data(ticker)
        comment = generate_gemini_commentary(name, ticker, fin, forecast)
        if comment:
            commentaries[ticker] = comment
        time.sleep(4)  # Gemini無料枠のレート制限(RPM)を考慮した間隔
    print(f"✅ 解説生成完了({len(commentaries)}/{len(tickers)}銘柄)")
    return commentaries

# ============================================================
# スプレッドシートへの履歴書き込み
# ============================================================
def _sheets_call_with_retry(func, *args, max_retries=4, **kwargs):
    """Google Sheets APIのクォータ超過(429)時に待機して自動リトライする"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            is_quota_error = "429" in str(e) or "Quota exceeded" in str(e)
            if is_quota_error and attempt < max_retries - 1:
                wait = 20 * (attempt + 1)
                print(f"⚠️ Sheets APIクォータ超過、{wait}秒待ってリトライします...({attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise

def write_to_spreadsheet(today, ace_stocks, king_stocks, poly_stocks, bep_stocks, commentaries=None):
    commentaries = commentaries or {}
    try:
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
        spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
        if not creds_json or not spreadsheet_id:
            print("⚠️ スプレッドシート設定未完了")
            return
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(spreadsheet_id)
        ws = sh.sheet1
        if ws.row_count == 0 or ws.cell(1, 1).value != "日付":
            _sheets_call_with_retry(ws.append_row, ["日付", "種別", "銘柄名", "市場", "解説"])

        # 1銘柄ずつAPI呼び出しすると書き込みクォータを超過するため、全行まとめて1回で送信
        rows_to_write = []
        for stock, ticker in ace_stocks:
            rows_to_write.append([today, "Ace", stock.replace("・", ""), get_market_label(ticker), commentaries.get(ticker, "")])
        for stock, ticker in king_stocks:
            rows_to_write.append([today, "King", stock.replace("・", ""), get_market_label(ticker), commentaries.get(ticker, "")])
        for stock, ticker in poly_stocks:
            rows_to_write.append([today, "ポリグラフ", stock.replace("・", ""), get_market_label(ticker), commentaries.get(ticker, "")])
        for stock, ticker in bep_stocks:
            rows_to_write.append([today, "Ace×BEP", stock.replace("・", ""), get_market_label(ticker), commentaries.get(ticker, "")])
        if rows_to_write:
            _sheets_call_with_retry(ws.append_rows, rows_to_write, value_input_option="RAW")

        # --- 日別サマリー(点灯銘柄数の推移を後から追える記録) ---
        try:
            ws_summary = sh.worksheet("サマリー")
        except gspread.exceptions.WorksheetNotFound:
            ws_summary = sh.add_worksheet(title="サマリー", rows=1000, cols=10)
            _sheets_call_with_retry(ws_summary.append_row, ["日付", "Ace銘柄数", "King銘柄数", "ポリグラフ銘柄数", "Ace×BEP銘柄数", "市場"])
        _sheets_call_with_retry(ws_summary.append_row, [today, len(ace_pairs), len(king_pairs), len(poly_pairs), len(bep_pairs), MARKET])

        print("✅ スプレッドシート書き込み完了")
    except Exception as e:
        print(f"❌ スプレッドシート書き込み失敗: {e}")

# ============================================================
# J-Quants APIで全上場銘柄コードを取得
# ============================================================
# 銘柄コードごとの市場区分(プライム/スタンダード/グロース等)。JP銘柄のみ使用
MARKET_SEGMENT_MAP = {}

def get_market_label(ticker):
    """出力用の市場ラベル。米国株は'US'、日本株は東証区分(プライム/スタンダード/グロース)、取得できない場合は'JP'"""
    if MARKET == "US":
        return "US"
    return MARKET_SEGMENT_MAP.get(ticker, "JP")

def get_all_tickers(ticker_name_map):
    global MARKET_SEGMENT_MAP
    try:
        api_key = os.environ.get("JQUANTS_API_KEY", "")
        if not api_key:
            print("⚠️ JQUANTS_API_KEY未設定 → 131銘柄リストを使用")
            return list(ticker_name_map.keys()), ticker_name_map
        cli = jquantsapi.ClientV2(api_key=api_key)
        df_list = cli.get_list()
        df_stocks = df_list[df_list['S33'] != '9999'].copy()
        print(f"🔍 J-Quants列名一覧(市場区分列の特定用): {list(df_stocks.columns)}")
        tickers = [str(code)[:-1] + ".T" for code in df_stocks['Code'].astype(str)]
        names = df_stocks['CoName'].tolist()
        name_map = dict(zip(tickers, names))
        # 市場区分(列名がAPIバージョンによって揺れる可能性があるため候補を順に試す)
        # 東証の市場区分コード→名称の対応(取得した列がコード数字だった場合の変換用)
        TSE_MARKET_CODE_NAMES = {
            "111": "プライム", "112": "スタンダード", "113": "グロース",
            "0111": "プライム", "0112": "スタンダード", "0113": "グロース",
            "0105": "その他", "0106": "その他", "0107": "その他",
            "0109": "その他", "0110": "その他",
        }
        for col in ["MarketCodeName", "MarketCode", "Market", "MarketName", "Mkt", "MktName", "S19", "S19Name"]:
            if col in df_stocks.columns:
                raw_values = df_stocks[col].astype(str).tolist()
                # 値が数字コードなら名称に変換、既に名称ならそのまま使う
                converted = [TSE_MARKET_CODE_NAMES.get(v, v) for v in raw_values]
                MARKET_SEGMENT_MAP = dict(zip(tickers, converted))
                break
        print(f"✅ J-Quants: {len(tickers)}銘柄取得成功(ETF除外済)")
        return tickers, name_map
    except Exception as e:
        print(f"⚠️ J-Quants取得失敗({e}) → 131銘柄リストを使用")
        return list(ticker_name_map.keys()), ticker_name_map

# ============================================================
# 関数定義
# ============================================================
def detect_bullish_ep(df, lookback=10):
    prev_close  = df["Close"].shift(1)
    prev_open   = df["Open"].shift(1)
    prev_close2 = df["Close"].shift(2)
    prev_high   = df["High"].shift(1)
    prev_volume = df["Volume"].shift(1)
    rolling_low   = df["Low"].rolling(lookback).min()
    rolling_high  = df["High"].rolling(lookback).max()
    rolling_range = (rolling_high - rolling_low).replace(0, np.nan)
    df["BEP_bullish"] = (
        (prev_close < prev_close2) &
        (df["Open"] <= prev_close) & (df["Close"] > prev_open) &
        (df["Close"] > prev_high) &
        (df["Volume"] > prev_volume) &
        ((df["Low"] - rolling_low) <= rolling_range * 0.3)
    ).fillna(False)
    return df

def calculate_rsi(close, period=14):
    """RSI(14) を Wilder方式の指数平滑で計算"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calculate_macd(close, fast=12, slow=26, signal=9):
    """MACD(12,26,9): MACDライン・シグナルライン・ヒストグラムを返す"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def calculate_base_indicators(df_stock):
    df = df_stock.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df["MA50"]  = df["Close"].rolling(50).mean()
    df["MA150"] = df["Close"].rolling(150).mean()
    df["MA200"] = df["Close"].rolling(200).mean()
    df["MA200_is_rising"] = df["MA200"].diff(20) > 0
    df["MA50_is_rising"]  = df["MA50"].diff(1) > 0
    df["High52"] = df["High"].rolling(250).max()
    df["Low52"]  = df["Low"].rolling(250).min()
    df["VolMA20"]   = df["Volume"].rolling(20).mean()
    df["VolumeVCP"] = (df["Volume"] - df["VolMA20"]) / df["VolMA20"]
    # ATR(Average True Range, 14日)
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14).mean()

    # --- RSI(14) ---
    df["RSI14"] = calculate_rsi(df["Close"], period=14)

    # --- MACD(12,26,9) ---
    df["MACD"], df["MACD_Signal"], df["MACD_Hist"] = calculate_macd(df["Close"])

    # --- トレンド判定(RSIとMACDの組み合わせ) ---
    # 📈上昇: MACD > シグナル かつ RSI > 50
    # 📉下降: MACD < シグナル かつ RSI < 50
    # ➡️中立: それ以外(どちらかが逆行しているケース)
    df["Trend"] = "➡️中立"
    df.loc[(df["MACD"] > df["MACD_Signal"]) & (df["RSI14"] > 50), "Trend"] = "📈上昇"
    df.loc[(df["MACD"] < df["MACD_Signal"]) & (df["RSI14"] < 50), "Trend"] = "📉下降"

    df = detect_bullish_ep(df)
    return df

def get_trend(df):
    """銘柄の直近トレンド判定(📈上昇/📉下降/➡️中立)を取得"""
    if df is None or df.empty or "Trend" not in df.columns:
        return "➡️中立"
    val = df["Trend"].iloc[-1]
    return val if pd.notna(val) else "➡️中立"

def calc_cross_sectional_rsr(combined_df, bench_close, perf_period=63):
    combined_df = combined_df.copy()
    perf_list = []
    for ticker, df in combined_df.groupby("Ticker"):
        df = df.copy()
        bench_aligned = bench_close.reindex(df.index).ffill()
        own_ret   = df["Close"].pct_change(perf_period)
        bench_ret = bench_aligned.pct_change(perf_period)
        df["RelPerf"] = own_ret - bench_ret
        perf_list.append(df)
    combined_df = pd.concat(perf_list)
    combined_df["RSR"] = (
        combined_df.groupby(combined_df.index)["RelPerf"]
        .transform(lambda x: (x.rank(pct=True) * 98 + 1).round())
        .fillna(0)
    )
    return combined_df

def calc_signals(combined_df, rsr_momentum_period=3):
    results = []
    for ticker, df in combined_df.groupby("Ticker"):
        df = df.copy()
        df["RSR_MA"]        = df["RSR"].rolling(10).mean()
        df["RSR_Mom"]       = df["RSR_MA"].diff(rsr_momentum_period)
        df["RSR_Mom_Slope"] = df["RSR_Mom"].diff(1)
        df["Phase"] = "None"
        df.loc[(df["RSR_Mom"] <  0) & (df["RSR_Mom_Slope"] >  0), "Phase"] = "水色"
        df.loc[(df["RSR_Mom"] >= 0) & (df["RSR_Mom_Slope"] >  0), "Phase"] = "濃いピンク"
        df.loc[(df["RSR_Mom"] >= 0) & (df["RSR_Mom_Slope"] <= 0), "Phase"] = "薄いピンク"
        df.loc[(df["RSR_Mom"] <  0) & (df["RSR_Mom_Slope"] <= 0), "Phase"] = "濃い青"
        base_7 = (
            (df["Close"] > df["MA150"]) & (df["Close"] > df["MA200"]) &
            (df["MA150"] > df["MA200"]) & df["MA200_is_rising"] &
            df["MA50_is_rising"] & (df["Close"] > df["MA50"]) &
            (df["Close"] >= df["Low52"] * 1.4) &   # 52週安値からの上昇率をより厳しく(旧1.3)
            (df["Close"] >= df["High52"] * 0.85)   # 52週高値により近い銘柄だけに(旧0.75)
        )
        df["Ace"]  = base_7 & (df["RSR"] >= 80)                     # 相対力を上位20%以内に(旧70)
        df["King"] = base_7 & (df["RSR"] >= 65) & (df["RSR"] < 80)  # Aceの一歩手前(旧60〜70)
        df["Polygraph"] = (
            (df["VolumeVCP"] > 1.0) &
            (df["RSR"] >= 85) &
            (df["RSR_Mom"] > 0) &
            (df["RSR_Mom"] > df["RSR_Mom"].shift(1)) &
            (df["Ace"])
        )
        df["Ace_with_BEP"] = df["Ace"] & df["BEP_bullish"]
        for col in ["Ace", "King", "Polygraph", "Ace_with_BEP", "BEP_bullish"]:
            df[f"{col}_Start"] = (df[col] == True) & (df[col].shift(1) == False)
        results.append(df)
    return pd.concat(results)

def backtest(combined_df, signal_col, ticker_name_map,
             atr_stop_mult=2.0, atr_profit_mult=4.0, max_hold_days=60, txn_cost_pct=0.2):
    all_returns, ticker_stats = [], {}
    for ticker, df in combined_df.groupby("Ticker"):
        df = df.reset_index(drop=True)
        sig_idx = np.where(df[signal_col] == True)[0]
        ticker_returns = []
        for idx in sig_idx:
            entry_idx = idx + 1  # シグナル点灯日の"翌営業日"にエントリー(ルックアヘッドバイアス回避)
            if entry_idx >= len(df): continue
            atr = df.iloc[idx]["ATR14"]  # シグナル点灯日時点で既知のATR(未来情報を使わない)
            if pd.isna(atr) or atr <= 0: continue
            buy_p = df.iloc[entry_idx]["Open"]  # 翌日の始値で購入
            stop_loss_pct = -(atr_stop_mult * atr / buy_p) * 100
            take_profit_pct = (atr_profit_mult * atr / buy_p) * 100
            exited = False
            for i in range(entry_idx, min(entry_idx + max_hold_days, len(df))):
                pnl = (df.iloc[i]["Close"] - buy_p) / buy_p * 100 - txn_cost_pct  # 往復取引コスト控除
                if pnl <= stop_loss_pct or pnl >= take_profit_pct:
                    all_returns.append(pnl); ticker_returns.append(pnl); exited = True; break
            if not exited:
                pnl = (df.iloc[min(entry_idx + max_hold_days - 1, len(df)-1)]["Close"] - buy_p) / buy_p * 100 - txn_cost_pct
                all_returns.append(pnl); ticker_returns.append(pnl)
        if ticker_returns:
            rets = np.array(ticker_returns)
            ticker_stats[ticker] = {
                "会社名": ticker_name_map.get(ticker, ticker),
                "シグナル回数": f"{len(rets)}回",
                "勝率": f"{np.sum(rets > 0) / len(rets) * 100:.1f}%",
                "平均リターン": f"{np.mean(rets):.2f}%",
                "_sort": np.mean(rets),
            }
    if all_returns:
        ov = np.array(all_returns)
        print(f"  [{signal_col}] 件数:{len(ov)} / 勝率:{np.sum(ov>0)/len(ov)*100:.1f}% / 平均:{np.mean(ov):.2f}%")
    else:
        print(f"  [{signal_col}] シグナル発生なし")
    return pd.DataFrame.from_dict(ticker_stats, orient="index")

# ============================================================
# メイン実行
# ============================================================
START = "2023-01-01"
END   = (date.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")  # 実行日の翌日を指定し、常に最新データまで取得
BENCH = "^GSPC" if MARKET == "US" else "1306.T"  # 米国株はS&P500、日本株はTOPIX連動ETF

if MARKET == "US":
    target_stocks, TICKER_NAME_MAP = get_us_tickers()
else:
    target_stocks, TICKER_NAME_MAP = get_all_tickers(TICKER_NAME_MAP)

print("🚀 データダウンロード開始...")
df_bench = yf.download(BENCH, start=START, end=END, auto_adjust=True, progress=False)
if isinstance(df_bench.columns, pd.MultiIndex):
    df_bench.columns = df_bench.columns.get_level_values(0)
bench_close = df_bench["Close"]

all_results, failed = [], []
for ticker in target_stocks:
    try:
        df_s = yf.download(ticker, start=START, end=END, auto_adjust=True, progress=False)
        if len(df_s) < 250:
            failed.append((ticker, "データ不足")); continue
        df_c = calculate_base_indicators(df_s)
        df_c["Ticker"] = ticker
        all_results.append(df_c)
    except Exception as e:
        failed.append((ticker, str(e)))

if failed:
    print(f"\n⚠️ 取得失敗/データ不足 {len(failed)}件")

print(f"\n✅ 有効銘柄: {len(all_results)}件")
print("📊 RSR算出中(全銘柄横断パーセンタイルランク)...")
combined_df = pd.concat(all_results)
combined_df = calc_cross_sectional_rsr(combined_df, bench_close)

print("📊 シグナル計算中...")
combined_df = calc_signals(combined_df)

# --- バックテスト ---
print("\n" + "="*60)
print("📈 バックテスト結果")
print("="*60)
signal_labels = {
    "Ace_Start":          "🅰️  Ace開始",
    "King_Start":         "👑 King開始",
    "Polygraph_Start":    "🎯 ポリグラフ開始",
    "Ace_with_BEP_Start": "🅰️🐢 Ace×BEP同時",
}
rankings = {}
for col, label in signal_labels.items():
    print(f"\n--- {label} ---")
    rankings[col] = backtest(combined_df, col, TICKER_NAME_MAP)

# --- 優秀銘柄ランキング ---
print("\n" + "="*60)
print("🏆 優秀銘柄ランキング TOP10 (Ace_Start基準)")
print("="*60)
if not rankings["Ace_Start"].empty:
    top10 = rankings["Ace_Start"].sort_values("_sort", ascending=False).head(10)
    print(top10[["会社名","シグナル回数","勝率","平均リターン"]].to_string())

# --- 現在シグナル点灯中 ---
print("\n" + "="*60)
print("🎯 直近3日以内にシグナル点灯中の銘柄")
print("="*60)
for col, label in signal_labels.items():
    print(f"\n{label}:")
    found = False
    for ticker, df in combined_df.groupby("Ticker"):
        if df[col].tail(3).any():
            print(f"  ・{TICKER_NAME_MAP.get(ticker, ticker)} ({ticker}) {get_trend(df)}")
            found = True
    if not found:
        print("  (該当なし)")

# ============================================================
# LINE通知送信
# ============================================================
print("\n📱 LINE通知送信中...")
today = date.today().strftime("%Y/%m/%d")
MARKET_LABEL = "🇺🇸 米国株" if MARKET == "US" else "🇯🇵 日本株"
msg = f"📊 {today} フジコシグナル({MARKET_LABEL})\n"
msg += "=" * 25 + "\n"

# --- 全件リスト(Web・スプレッドシート用) ---
ace_stocks_all  = [(f"・{TICKER_NAME_MAP.get(t, t)} {get_trend(df)}", t) for t, df in combined_df.groupby("Ticker") if df["Ace_Start"].tail(3).any()]
king_stocks_all = [(f"・{TICKER_NAME_MAP.get(t, t)} {get_trend(df)}", t) for t, df in combined_df.groupby("Ticker") if df["King_Start"].tail(3).any()]
poly_stocks_all = [(f"・{TICKER_NAME_MAP.get(t, t)} {get_trend(df)}", t) for t, df in combined_df.groupby("Ticker") if df["Polygraph_Start"].tail(3).any()]
bep_stocks_all  = [(f"・{TICKER_NAME_MAP.get(t, t)} {get_trend(df)}", t) for t, df in combined_df.groupby("Ticker") if df["Ace_with_BEP_Start"].tail(3).any()]

# ============================================================
# ファンダメンタルズ解説(EDINET財務データ + Gemini)
# ============================================================
# Ace/King/ポリグラフ/BEPで点灯中の全銘柄を対象に、重複を除いてまとめて取得する
_all_signaled_tickers = sorted(set(
    [t for _, t in ace_stocks_all] +
    [t for _, t in king_stocks_all] +
    [t for _, t in poly_stocks_all] +
    [t for _, t in bep_stocks_all]
))
fundamental_commentaries = build_fundamental_commentaries(_all_signaled_tickers, TICKER_NAME_MAP)

# --- LINE通知用(文字数制限があるため上位20件のみ、ヘッダーには正しい総数を表示) ---
# トレンド絵文字を先頭に置いて見やすく(市場タグはヘッダーで分かるため省略)
# 解説は文字数制限のため先頭20字程度に切り詰めて括弧内に付与
def _line_format(t, df):
    base = f"{get_trend(df)} {TICKER_NAME_MAP.get(t, t)} [{get_market_label(t)}]"
    comment = fundamental_commentaries.get(t, "")
    if comment:
        short_comment = comment[:20] + ("…" if len(comment) > 20 else "")
        base += f"\n   {short_comment}"
    return base

ace_pairs  = [(t, df) for t, df in combined_df.groupby("Ticker") if df["Ace_Start"].tail(3).any()]
ace_stocks = [_line_format(t, df) for t, df in ace_pairs[:20]]
msg += f"\n🅰️ Ace点灯中({len(ace_pairs)}銘柄、上位{len(ace_stocks)}件表示)\n"
msg += "\n".join(ace_stocks) if ace_stocks else "  (該当なし)"

king_pairs  = [(t, df) for t, df in combined_df.groupby("Ticker") if df["King_Start"].tail(3).any()]
king_stocks = [_line_format(t, df) for t, df in king_pairs[:20]]
msg += f"\n\n👑 King点灯中({len(king_pairs)}銘柄、上位{len(king_stocks)}件表示)\n"
msg += "\n".join(king_stocks) if king_stocks else "  (該当なし)"

poly_pairs  = [(t, df) for t, df in combined_df.groupby("Ticker") if df["Polygraph_Start"].tail(3).any()]
poly_stocks = [_line_format(t, df) for t, df in poly_pairs[:20]]
msg += f"\n\n🎯 ポリグラフ点灯中({len(poly_pairs)}銘柄、上位{len(poly_stocks)}件表示)\n"
msg += "\n".join(poly_stocks) if poly_stocks else "  (該当なし)"

bep_pairs  = [(t, df) for t, df in combined_df.groupby("Ticker") if df["Ace_with_BEP_Start"].tail(3).any()]
bep_stocks = [_line_format(t, df) for t, df in bep_pairs[:10]]
msg += f"\n\n🅰️🐢 Ace×BEP同時({len(bep_pairs)}銘柄、上位{len(bep_stocks)}件表示)\n"
msg += "\n".join(bep_stocks) if bep_stocks else "  (該当なし)"

send_line(msg)

# ============================================================
# HTML結果ページ生成 (GitHub Pages用)
# ============================================================
print("\n📄 HTMLページ生成中...")

def signal_table_html(stocks, title, emoji, commentaries=None):
    # stocks: [(name, ticker), ...]  name には既にトレンド絵文字が含まれる
    commentaries = commentaries or {}
    def _row(n, t):
        comment = commentaries.get(t, "")
        comment_html = f'<div class="commentary">{comment}</div>' if comment else ""
        return f'<li><a href="{chart_url(t)}" target="_blank" rel="noopener">{n}</a>{comment_html}</li>'
    rows = "".join(_row(n, t) for n, t in stocks) if stocks else "<li class='none'>該当なし</li>"
    return f"""
    <div class="card">
      <h2>{emoji} {title} ({len(stocks)}銘柄)</h2>
      <ul>{rows}</ul>
    </div>
    """

ace_list  = [(f"{TICKER_NAME_MAP.get(t, t)} {get_trend(df)} [{get_market_label(t)}]", t) for t, df in combined_df.groupby("Ticker") if df["Ace_Start"].tail(3).any()]
king_list = [(f"{TICKER_NAME_MAP.get(t, t)} {get_trend(df)} [{get_market_label(t)}]", t) for t, df in combined_df.groupby("Ticker") if df["King_Start"].tail(3).any()]
poly_list = [(f"{TICKER_NAME_MAP.get(t, t)} {get_trend(df)} [{get_market_label(t)}]", t) for t, df in combined_df.groupby("Ticker") if df["Polygraph_Start"].tail(3).any()]
bep_list  = [(f"{TICKER_NAME_MAP.get(t, t)} {get_trend(df)} [{get_market_label(t)}]", t) for t, df in combined_df.groupby("Ticker") if df["Ace_with_BEP_Start"].tail(3).any()]

top10_html = ""
if not rankings["Ace_Start"].empty:
    top10 = rankings["Ace_Start"].sort_values("_sort", ascending=False).head(10)
    rows = "".join(
        f'<tr><td><a href="{chart_url(ticker)}" target="_blank" rel="noopener">{r["会社名"]}</a></td>'
        f'<td>{r["シグナル回数"]}</td><td>{r["勝率"]}</td><td>{r["平均リターン"]}</td>'
        f'<td>{get_trend(combined_df[combined_df["Ticker"] == ticker])}</td><td>{get_market_label(ticker)}</td></tr>'
        for ticker, r in top10.iterrows()
    )
    top10_html = f"""
    <div class="card">
      <h2>🏆 優秀銘柄ランキング TOP10</h2>
      <table>
        <tr><th>会社名</th><th>シグナル回数</th><th>勝率</th><th>平均リターン</th><th>トレンド</th><th>市場</th></tr>
        {rows}
      </table>
    </div>
    """

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>フジコシグナル {MARKET_LABEL} {today}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
          background: #0f1117; color: #e8e8e8; margin: 0; padding: 16px; }}
  h1 {{ font-size: 1.3em; margin-bottom: 4px; }}
  .updated {{ color: #888; font-size: 0.85em; margin-bottom: 20px; }}
  .card {{ background: #1a1d27; border-radius: 12px; padding: 16px; margin-bottom: 16px;
           box-shadow: 0 2px 8px rgba(0,0,0,0.3); }}
  .card h2 {{ font-size: 1.05em; margin-top: 0; margin-bottom: 10px; }}
  ul {{ list-style: none; padding: 0; margin: 0; }}
  li {{ padding: 6px 0; border-bottom: 1px solid #2a2d3a; }}
  li:last-child {{ border-bottom: none; }}
  li.none {{ color: #666; }}
  li a {{ color: #e8e8e8; text-decoration: none; display: block; }}
  li a:hover {{ color: #4da6ff; text-decoration: underline; }}
  li .commentary {{ color: #9aa0ac; font-size: 0.82em; margin-top: 2px; line-height: 1.4; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
  th, td {{ text-align: left; padding: 6px 4px; border-bottom: 1px solid #2a2d3a; }}
  th {{ color: #888; font-weight: normal; }}
  td a {{ color: #e8e8e8; text-decoration: none; }}
  td a:hover {{ color: #4da6ff; text-decoration: underline; }}
</style>
</head>
<body>
  <h1>📊 フジコシグナル({MARKET_LABEL})</h1>
  <div class="updated">最終更新: {today}</div>

  {signal_table_html(ace_list, "Ace点灯中", "🅰️", fundamental_commentaries)}
  {signal_table_html(king_list, "King点灯中", "👑", fundamental_commentaries)}
  {signal_table_html(poly_list, "ポリグラフ点灯中", "🎯", fundamental_commentaries)}
  {signal_table_html(bep_list, "Ace×BEP同時", "🅰️🐢", fundamental_commentaries)}
  {top10_html}

</body>
</html>"""

output_filename = "index_us.html" if MARKET == "US" else "index.html"
with open(output_filename, "w", encoding="utf-8") as f:
    f.write(html)

print(f"✅ {output_filename} 生成完了")

# ============================================================
# スプレッドシートに履歴を書き込む
# ============================================================
write_to_spreadsheet(today, ace_stocks_all, king_stocks_all, poly_stocks_all, bep_stocks_all, fundamental_commentaries)
