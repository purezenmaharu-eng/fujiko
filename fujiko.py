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
GEMINI_MODEL = "gemini-3.1-flash-lite"

def _post_with_429_retry(url, label, max_retries=3, **kwargs):
    """429(レート制限)時に短時間だけリトライする。それでも解消しない場合は
    一時的な詰まりではなく日次/月次クォータ超過とみなし、呼び出し元で判断できるよう
    最後のレスポンスをそのまま返す(呼び出し元がdisabledフラグを立てる)"""
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
    return resp  # 最後まで429だった場合はそのまま返す(呼び出し元でクォータ超過と判断)

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

# ============================================================
# LINE通知設定 (GAS経由)
# ============================================================
GAS_URL = os.environ.get("GAS_URL", "")
GAS_TOKEN = os.environ.get("GAS_TOKEN", "")

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
        pass

# テスト実行時にラジ株ナビ・Gemini呼び出しをスキップする環境変数フラグ
# GitHub Actions: 設定しない(本番実行) / ローカルテスト: SKIP_FUNDAMENTALS=1 で設定
SKIP_FUNDAMENTALS = os.environ.get("SKIP_FUNDAMENTALS", "").strip().lower() in ("1", "true", "yes")
if SKIP_FUNDAMENTALS:
    print("⚠️ SKIP_FUNDAMENTALS=true → ラジ株ナビ・Geminiの呼び出しをスキップします(テストモード)")

# ラジ株ナビAPIキャッシュ(同日の再実行でクォータを消費しない)
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
_CACHE_DATE = date.today().strftime("%Y-%m-%d")

def _cache_key(tool_name, arguments):
    """キャッシュ用のファイルパスを生成"""
    import hashlib
    arg_hash = hashlib.md5(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:12]
    return os.path.join(_CACHE_DIR, f"{_CACHE_DATE}_{tool_name}_{arg_hash}.json")

def _cache_read(tool_name, arguments):
    """キャッシュがあれば読み込んで返す。なければNone"""
    path = _cache_key(tool_name, arguments)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _cache_write(tool_name, arguments, data):
    """結果をキャッシュに保存"""
    if data is None:
        return
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_key(tool_name, arguments)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass  # キャッシュ書き込み失敗は無視

def radikabunavi_call_tool(tool_name, arguments):
    """ラジ株ナビMCPのツールを呼び出し、結果(dict)を返す。失敗時はNone。
    同日のキャッシュがあればAPIを呼ばずに再利用する。"""
    global _radikabunavi_disabled
    if SKIP_FUNDAMENTALS:
        return None
    if not RADIKABUNAVI_API_KEY or _radikabunavi_disabled:
        return None
    # --- キャッシュ確認 ---
    cached = _cache_read(tool_name, arguments)
    if cached is not None:
        return cached
    # --- API呼び出し ---
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

def get_fundamental_data(ticker):
    """EDINET財務データ(推移+会社予想)と6軸スコア+理想株価を取得(旧earnings_forecastの代替)"""
    code = ticker.replace(".T", "")
    fin = radikabunavi_call_tool("get_edinet_financial_data", {
        "code": code,
        "metrics": ["netSales", "operatingIncome", "netIncome",
                     "operatingMargin", "eps", "per", "bps"],
    })
    score = radikabunavi_call_tool("get_stock_score", {"code": code})
    return fin, score

# ============================================================
# Evy式バリュエーション(自前ロジック)
# ============================================================
def evy_valuation(fin_data, score_data):
    """EDINET財務データ(get_edinet_financial_data)から Evy式適正株価を算出する。
    実データ構造の注意点:
      - fin_data["fiscalYears"] は日付キー("2025-12-31"等)の辞書(リストではない)
      - 会社予想EPSは fin_data["companyForecast"]["forecast"]["eps"](無い場合はnull)
      - get_stock_scoreのidealPriceには forecastEps/epsAnchor は含まれない(get_ideal_price専用)ため参照しない
    戻り値: dict(fairPrice, basePER, anchorEPS, discountPct, label) or None"""
    if not fin_data:
        return None
    try:
        fiscal_years = fin_data.get("fiscalYears") or {}
        sorted_fy_keys = sorted(fiscal_years.keys())  # "YYYY-MM-DD"昇順→末尾が最新期

        # --- 直近実績EPS(fiscalYearsの最新期) ---
        latest_actual_eps = None
        if sorted_fy_keys:
            latest_actual_eps = fiscal_years[sorted_fy_keys[-1]].get("eps")

        # --- 会社予想EPS(companyForecast.forecast.eps。無ければnull) ---
        company_forecast = fin_data.get("companyForecast") or {}
        forecast_block = company_forecast.get("forecast") or {}
        forecast_eps = forecast_block.get("eps")

        # --- 確約EPS決定(会社予想優先、2倍超の増額予想は幾何平均で緩和) ---
        anchor_eps = None
        eps_source = None
        if forecast_eps and forecast_eps > 0:
            if latest_actual_eps and latest_actual_eps > 0 and forecast_eps > latest_actual_eps * 2:
                anchor_eps = (forecast_eps * latest_actual_eps) ** 0.5
                eps_source = "blended"
            else:
                anchor_eps = forecast_eps
                eps_source = "forecast"
        elif latest_actual_eps and latest_actual_eps > 0:
            anchor_eps = latest_actual_eps
            eps_source = "actual"
        if not anchor_eps or anchor_eps <= 0:
            return None

        # --- 基準PER(売上高3年CAGRで決定。fiscalYearsが4期未満ならスコアのgrowth軸から概算) ---
        sales_growth = None
        if len(sorted_fy_keys) >= 4:
            latest_sales = fiscal_years[sorted_fy_keys[-1]].get("netSales")
            past_sales = fiscal_years[sorted_fy_keys[-4]].get("netSales")  # 3期前
            if latest_sales and past_sales and past_sales > 0:
                sales_growth = ((latest_sales / past_sales) ** (1 / 3) - 1) * 100
        if sales_growth is None and score_data:
            axes = score_data.get("axes") or {}
            g_score = axes.get("growth")
            if g_score is not None:
                sales_growth = max(0, (g_score - 50) * 0.375)  # 粗い近似(50=0%、90=15%相当)
        if sales_growth is None:
            sales_growth = 0.0
        # 成長率→基準PERのマッピング(Evy式を参考に段階設定)
        if sales_growth >= 10:
            base_per = 20
        elif sales_growth >= 5:
            base_per = 18
        elif sales_growth >= 2:
            base_per = 16
        else:
            base_per = 14

        fair_price = round(base_per * anchor_eps, 1)

        # --- 擬似現在株価(get_stock_scoreのidealPrice.pseudoPriceを使用) ---
        pseudo_price = None
        if score_data:
            ip = score_data.get("idealPrice") or {}
            pseudo_price = ip.get("pseudoPrice")
        if not pseudo_price:
            return None
        discount_pct = round((fair_price - pseudo_price) / fair_price * 100, 1)
        if discount_pct >= 20:
            label = "割安"
        elif discount_pct >= -10:
            label = "適正"
        else:
            label = "割高"
        return {
            "fairPrice": fair_price,
            "basePER": base_per,
            "anchorEPS": round(anchor_eps, 2),
            "epsSource": eps_source,
            "pseudoPrice": round(pseudo_price, 1),
            "discountPct": discount_pct,
            "label": label,
        }
    except Exception as e:
        print(f"⚠️ Evy式バリュエーション算出失敗: {e}")
        return None

_gemini_disabled = False  # 429が解消しない場合、以降のGemini呼び出しをスキップ

def generate_gemini_commentary(name, ticker, fin_data, score_data):
    """決算・業績データと6軸スコアをもとに、Geminiで短い解説コメントを生成する"""
    global _gemini_disabled
    if SKIP_FUNDAMENTALS or not GEMINI_API_KEY or _gemini_disabled:
        return ""
    if not fin_data and not score_data:
        return ""
    try:
        # スコアデータから割安度判定・6軸を抽出(プロンプト用)
        score_summary = ""
        if score_data:
            axes = score_data.get("axes") or {}
            ip = score_data.get("idealPrice") or {}
            score_summary = (
                f"6軸スコア: 割安度{axes.get('valuation','-')}/稼ぐ力{axes.get('profitability','-')}"
                f"/成長性{axes.get('growth','-')}/安全性{axes.get('safety','-')}"
                f"/還元力{axes.get('shareholderReturn','-')}/事業独占力{axes.get('moat','-')}\n"
                f"理想株価判定: {ip.get('verdict','-')} (α値: {ip.get('alphaPct','-')}%)\n"
            )
        # fin_dataは全期間・全項目を含み巨大なため、直近3期分の主要指標だけに絞って渡す
        fin_summary = ""
        if fin_data:
            fiscal_years = fin_data.get("fiscalYears") or {}
            sorted_keys = sorted(fiscal_years.keys())[-3:]
            lines = []
            for k in sorted_keys:
                fy = fiscal_years[k]
                lines.append(
                    f"{k}: 売上高{fy.get('netSales')} 営業利益{fy.get('operatingIncome')} "
                    f"純利益{fy.get('netIncome')} EPS{fy.get('eps')} 営業利益率{fy.get('operatingMargin')}%"
                )
            forecast = (fin_data.get("companyForecast") or {}).get("forecast") or {}
            forecast_line = f"会社予想: 売上高{forecast.get('netSales')} 純利益{forecast.get('netIncome')} EPS{forecast.get('eps')}" if forecast.get("eps") else "会社予想: 未開示"
            fin_summary = "直近3期の実績:\n" + "\n".join(lines) + f"\n{forecast_line}\n"
        prompt = (
            f"以下は日本株「{name}」({ticker})の決算・業績データです。\n"
            "これをもとに、日本語で40〜60字程度の一言コメントを作成してください。\n"
            "条件:\n"
            "- 売上高・利益の直近の伸び率を踏まえること\n"
            "- 割安度判定がある場合はそれにも触れること\n"
            "- 「買い」「売り」など断定的な投資判断は書かず、事実ベースの短評にすること\n"
            "- 絵文字や記号装飾は使わず、文章のみで出力すること\n\n"
            f"{fin_summary}"
            f"{score_summary}"
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        resp = _post_with_429_retry(
            url, "Gemini",
            json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30,
        )
        if resp.status_code == 429:
            print("❌ Gemini: 429が解消しないためクォータ超過と判断 → 以降の解説生成をスキップします")
            _gemini_disabled = True
            return ""
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text.replace("\n", " ")
    except Exception as e:
        print(f"⚠️ Gemini解説生成失敗({name}): {e}")
        return ""

def build_fundamental_commentaries(tickers, ticker_name_map):
    """点灯銘柄それぞれについてEDINETデータ+スコアを取得し、Gemini解説とバリュエーション情報を生成する。
    戻り値: (commentaries, valuations)
      commentaries: ticker→コメント文字列
      valuations: ticker→{radi: {verdict, alphaPct, ...}, evy: {fairPrice, discountPct, label, ...}}
    """
    commentaries = {}
    valuations = {}
    if not RADIKABUNAVI_API_KEY:
        print("⚠️ RADIKABUNAVI_API_KEY未設定 → ファンダメンタルズ解説をスキップ")
        return commentaries, valuations
    print(f"📚 ファンダメンタルズ解説+バリュエーション取得中({len(tickers)}銘柄)...")
    for ticker in tickers:
        if _radikabunavi_disabled:
            print("⏹️ ラジ株ナビが利用不可のため、残りの処理を打ち切ります")
            break
        name = ticker_name_map.get(ticker, ticker)
        fin, score = get_fundamental_data(ticker)

        # --- バリュエーション情報を抽出 ---
        val_info = {}
        if score:
            ip = score.get("idealPrice") or {}
            val_info["radi"] = {
                "verdict": ip.get("verdict"),
                "alphaPct": ip.get("alphaPct"),
                "mid": ip.get("mid"),
                "pseudoPrice": ip.get("pseudoPrice"),
            }
            axes = score.get("axes") or {}
            val_info["scores"] = axes
        evy = evy_valuation(fin, score)
        if evy:
            val_info["evy"] = evy
        if val_info:
            valuations[ticker] = val_info

        # --- Gemini解説 ---
        if not _gemini_disabled and GEMINI_API_KEY:
            comment = generate_gemini_commentary(name, ticker, fin, score)
            if comment:
                commentaries[ticker] = comment
        elif _gemini_disabled:
            pass  # Geminiが停止済みでもバリュエーションは取得済みなので続行

        time.sleep(4.5)
    print(f"✅ 解説生成完了({len(commentaries)}/{len(tickers)}銘柄)、バリュエーション取得({len(valuations)}銘柄)")
    return commentaries, valuations
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

def chart_url(ticker):
    """銘柄チャートへのリンク(TradingViewに統一)"""
    if MARKET == "US":
        return f"https://www.tradingview.com/symbols/{ticker}/"
    code = ticker.replace(".T", "")
    return f"https://www.tradingview.com/chart/?symbol=TSE%3A{code}"

def write_to_spreadsheet(today, ace_stocks, king_stocks, poly_stocks, bep_stocks, commentaries=None, valuations=None):
    commentaries = commentaries or {}
    valuations = valuations or {}
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
        # --- 日付ごとに新しいシートを作成(見やすさ対策) ---
        sheet_name = today.replace("/", "-")  # 例: "2026/07/25" → "2026-07-25"
        try:
            ws = sh.worksheet(sheet_name)
            is_new_sheet = False
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=sheet_name, rows=500, cols=10)
            is_new_sheet = True

        SHEET_HEADERS = ["日付", "種別", "銘柄名", "市場", "ラジ株判定", "Evy式適正価格", "Evy式割安率%", "解説", "チャート"]
        if is_new_sheet or ws.row_count == 0 or ws.cell(1, 1).value != "日付":
            _sheets_call_with_retry(ws.append_row, SHEET_HEADERS)
            # 見出し行を固定し、フィルタと列幅を自動設定
            try:
                ws.freeze(rows=1)
                ws.set_basic_filter()
                ws.columns_auto_resize(0, len(SHEET_HEADERS) - 1)
            except Exception as e:
                print(f"⚠️ シート書式設定に失敗(処理は継続): {e}")

        def _chart_link(ticker):
            return f'=HYPERLINK("{chart_url(ticker)}", "チャートを見る")'

        def _valuation_cells(ticker):
            """バリュエーション列の値を返す: [ラジ株判定, Evy式適正価格, Evy式割安率%]"""
            v = valuations.get(ticker)
            if not v:
                return ["", "", ""]
            radi = v.get("radi") or {}
            evy = v.get("evy") or {}
            radi_label = radi.get("verdict", "")
            if radi.get("alphaPct") is not None:
                radi_label += f" ({radi['alphaPct']:+.1f}%)"
            evy_price = evy.get("fairPrice", "")
            evy_discount = evy.get("discountPct", "")
            return [radi_label, evy_price, evy_discount]

        rows_to_write = []
        for stock, ticker in ace_stocks:
            rows_to_write.append([today, "Ace", stock.replace("・", ""), get_market_label(ticker)] + _valuation_cells(ticker) + [commentaries.get(ticker, ""), _chart_link(ticker)])
        for stock, ticker in king_stocks:
            rows_to_write.append([today, "King", stock.replace("・", ""), get_market_label(ticker)] + _valuation_cells(ticker) + [commentaries.get(ticker, ""), _chart_link(ticker)])
        for stock, ticker in poly_stocks:
            rows_to_write.append([today, "ポリグラフ", stock.replace("・", ""), get_market_label(ticker)] + _valuation_cells(ticker) + [commentaries.get(ticker, ""), _chart_link(ticker)])
        for stock, ticker in bep_stocks:
            rows_to_write.append([today, "Ace×BEP", stock.replace("・", ""), get_market_label(ticker)] + _valuation_cells(ticker) + [commentaries.get(ticker, ""), _chart_link(ticker)])
        if rows_to_write:
            _sheets_call_with_retry(ws.append_rows, rows_to_write, value_input_option="USER_ENTERED")

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
# シグナル的中率トラッキング(「追跡」シート)
# ============================================================
TRACKING_HEADERS = [
    "銘柄名", "ティッカー", "種別", "市場", "点灯日", "点灯日終値",
    "ラジ株判定", "Evy式割安率%",
    "ステータス", "判定日", "判定時終値", "騰落率%", "的中",
]
TRACKING_SIGNAL_COLUMNS = {
    "Ace_Start": "Ace",
    "King_Start": "King",
    "Polygraph_Start": "ポリグラフ",
    "Ace_with_BEP_Start": "Ace×BEP",
}
TRACKING_HOLD_DAYS = 10
TRACKING_HIT_THRESHOLD_PCT = 2.0

def _tracking_register_new_signals(ws, combined_df, ticker_name_map, existing_keys, valuations=None):
    """当日点灯したシグナルを「追跡」シートに新規登録する"""
    valuations = valuations or {}
    new_rows = []
    for ticker, df in combined_df.groupby("Ticker"):
        if df.empty:
            continue
        last_row = df.iloc[-1]
        lit_date_str = df.index[-1].strftime("%Y/%m/%d")
        close_price = last_row.get("Close")
        if pd.isna(close_price):
            continue
        for col, label in TRACKING_SIGNAL_COLUMNS.items():
            if col not in df.columns or not bool(last_row.get(col, False)):
                continue
            key = (ticker, label, lit_date_str)
            if key in existing_keys:
                continue
            # バリュエーション情報を取得
            v = valuations.get(ticker, {})
            radi = v.get("radi") or {}
            evy = v.get("evy") or {}
            radi_label = radi.get("verdict", "")
            evy_discount = evy.get("discountPct", "")
            new_rows.append([
                ticker_name_map.get(ticker, ticker),
                ticker,
                label,
                get_market_label(ticker),
                lit_date_str,
                round(float(close_price), 2),
                radi_label,
                evy_discount,
                "追跡中", "", "", "", "",
            ])
            existing_keys.add(key)
    if new_rows:
        _sheets_call_with_retry(ws.append_rows, new_rows, value_input_option="RAW")
    return len(new_rows)

def _tracking_resolve_pending_signals(ws, combined_df, records):
    """10営業日経過した「追跡中」行の的中判定を確定する"""
    cells_to_update = []
    resolved_count = 0
    hit_count = 0
    for i, rec in enumerate(records):
        if rec.get("ステータス") != "追跡中":
            continue
        row_num = i + 2  # ヘッダー行の次から
        ticker = rec.get("ティッカー")
        lit_date_str = rec.get("点灯日")
        lit_close = rec.get("点灯日終値")
        if not ticker or not lit_date_str or lit_close in ("", None):
            continue
        try:
            lit_close = float(lit_close)
        except (TypeError, ValueError):
            continue

        ticker_df = combined_df[combined_df["Ticker"] == ticker].sort_index()
        if ticker_df.empty:
            continue
        try:
            lit_date = pd.to_datetime(lit_date_str, format="%Y/%m/%d")
            pos = ticker_df.index.get_loc(lit_date)
        except KeyError:
            continue
        if isinstance(pos, (slice, np.ndarray)):
            continue  # 重複日付など異常系はスキップ

        target_pos = pos + TRACKING_HOLD_DAYS
        if target_pos >= len(ticker_df):
            continue  # まだ規定営業日数を経過していない

        judge_row = ticker_df.iloc[target_pos]
        judge_close = judge_row["Close"]
        if pd.isna(judge_close):
            continue
        judge_date_str = ticker_df.index[target_pos].strftime("%Y/%m/%d")
        pct_change = (judge_close - lit_close) / lit_close * 100
        is_hit = pct_change >= TRACKING_HIT_THRESHOLD_PCT

        resolved_count += 1
        if is_hit:
            hit_count += 1

        values = ["完了", judge_date_str, round(float(judge_close), 2),
                  round(float(pct_change), 2), "的中" if is_hit else "不的中"]
        for col_offset, value in enumerate(values):
            cells_to_update.append(gspread.Cell(row=row_num, col=9 + col_offset, value=value))

    if cells_to_update:
        _sheets_call_with_retry(ws.update_cells, cells_to_update, value_input_option="RAW")
    return resolved_count, hit_count

def run_signal_tracking(combined_df, ticker_name_map, valuations=None):
    """当日点灯シグナルの「追跡」シートへの新規登録と、10営業日経過分の的中判定確定を行う。
    戻り値: (新規登録件数, 判定確定件数, 的中件数)。スプレッドシート設定が無い場合はNone"""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
    if not creds_json or not spreadsheet_id:
        print("⚠️ スプレッドシート設定未完了 → シグナル的中率トラッキングをスキップ")
        return None
    try:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(spreadsheet_id)

        try:
            ws = sh.worksheet("追跡")
            is_new_sheet = False
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title="追跡", rows=2000, cols=len(TRACKING_HEADERS))
            is_new_sheet = True

        if is_new_sheet or ws.row_count == 0 or ws.cell(1, 1).value != "銘柄名":
            _sheets_call_with_retry(ws.append_row, TRACKING_HEADERS)
            try:
                ws.freeze(rows=1)
                ws.set_basic_filter()
                ws.columns_auto_resize(0, len(TRACKING_HEADERS) - 1)
            except Exception as e:
                print(f"⚠️ 追跡シート書式設定に失敗(処理は継続): {e}")

        existing_records = _sheets_call_with_retry(ws.get_all_records)
        existing_keys = {(r["ティッカー"], r["種別"], r["点灯日"]) for r in existing_records}

        new_count = _tracking_register_new_signals(ws, combined_df, ticker_name_map, existing_keys, valuations)

        records = _sheets_call_with_retry(ws.get_all_records) if new_count else existing_records
        resolved_count, hit_count = _tracking_resolve_pending_signals(ws, combined_df, records)

        print(f"✅ シグナル的中率トラッキング: 新規登録{new_count}件 / 判定確定{resolved_count}件 / 的中{hit_count}件")
        return new_count, resolved_count, hit_count
    except Exception as e:
        print(f"❌ シグナル的中率トラッキング失敗: {e}")
        return None

# ============================================================
# J-Quants APIで全上場銘柄コードを取得
# ============================================================
MARKET_SEGMENT_MAP = {}

def get_market_label(ticker):
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
        TSE_MARKET_CODE_NAMES = {
            "111": "プライム", "112": "スタンダード", "113": "グロース",
            "0111": "プライム", "0112": "スタンダード", "0113": "グロース",
            "0105": "その他", "0106": "その他", "0107": "その他",
            "0109": "その他", "0110": "その他",
        }
        for col in ["MarketCodeName", "MarketCode", "Market", "MarketName", "Mkt", "MktName", "S19", "S19Name"]:
            if col in df_stocks.columns:
                raw_values = df_stocks[col].astype(str).tolist()
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
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calculate_macd(close, fast=12, slow=26, signal=9):
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
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14).mean()

    df["RSI14"] = calculate_rsi(df["Close"], period=14)
    df["MACD"], df["MACD_Signal"], df["MACD_Hist"] = calculate_macd(df["Close"])

    df["Trend"] = "➡️中立"
    df.loc[(df["MACD"] > df["MACD_Signal"]) & (df["RSI14"] > 50), "Trend"] = "📈上昇"
    df.loc[(df["MACD"] < df["MACD_Signal"]) & (df["RSI14"] < 50), "Trend"] = "📉下降"

    df = detect_bullish_ep(df)
    return df

def get_trend(df):
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
            (df["Close"] >= df["Low52"] * 1.4) &
            (df["Close"] >= df["High52"] * 0.85)
        )
        df["Ace"]  = base_7 & (df["RSR"] >= 80)
        df["King"] = base_7 & (df["RSR"] >= 65) & (df["RSR"] < 80)
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
            entry_idx = idx + 1
            if entry_idx >= len(df): continue
            atr = df.iloc[idx]["ATR14"]
            if pd.isna(atr) or atr <= 0: continue
            buy_p = df.iloc[entry_idx]["Open"]
            stop_loss_pct = -(atr_stop_mult * atr / buy_p) * 100
            take_profit_pct = (atr_profit_mult * atr / buy_p) * 100
            exited = False
            for i in range(entry_idx, min(entry_idx + max_hold_days, len(df))):
                pnl = (df.iloc[i]["Close"] - buy_p) / buy_p * 100 - txn_cost_pct
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
END   = (date.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
BENCH = "^GSPC" if MARKET == "US" else "1306.T"

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
# ファンダメンタルズ解説 + バリュエーション(EDINET財務 + ラジ株スコア + Evy式)
# ============================================================
# 優先度順(厳選度が高いシグナルを優先): ポリグラフ → Ace×BEP → Ace → King
# ラジ株ナビ無料プランは1日150リクエスト、1銘柄につき2リクエスト消費するため上限75銘柄
RADIKABUNAVI_DAILY_LIMIT = 150
CALLS_PER_TICKER = 2
MAX_FUNDAMENTAL_TICKERS = RADIKABUNAVI_DAILY_LIMIT // CALLS_PER_TICKER

_priority_ordered_tickers = []
_seen_tickers = set()
for _stocks in (poly_stocks_all, bep_stocks_all, ace_stocks_all, king_stocks_all):
    for _, _t in _stocks:
        if _t not in _seen_tickers:
            _seen_tickers.add(_t)
            _priority_ordered_tickers.append(_t)

_all_signaled_tickers = _priority_ordered_tickers
if len(_all_signaled_tickers) > MAX_FUNDAMENTAL_TICKERS:
    print(f"priority list exceeds free-tier limit, trimming to top {MAX_FUNDAMENTAL_TICKERS}")
    _all_signaled_tickers = _all_signaled_tickers[:MAX_FUNDAMENTAL_TICKERS]
fundamental_commentaries, fundamental_valuations = build_fundamental_commentaries(_all_signaled_tickers, TICKER_NAME_MAP)

# --- シグナル的中率トラッキング(バリュエーション情報付きで登録) ---
print("\n📊 シグナル的中率トラッキング処理中...")
tracking_result = run_signal_tracking(combined_df, TICKER_NAME_MAP, fundamental_valuations)

# --- LINE通知用(文字数制限があるため上位20件のみ、ヘッダーには正しい総数を表示) ---
def _valuation_tag(t):
    """バリュエーション情報から短いタグを生成(LINE表示用)"""
    v = fundamental_valuations.get(t)
    if not v:
        return ""
    parts = []
    radi = v.get("radi") or {}
    if radi.get("verdict"):
        parts.append(f"ラジ:{radi['verdict']}")
    evy = v.get("evy") or {}
    if evy.get("label"):
        parts.append(f"Evy:{evy['label']}({evy['discountPct']:+.0f}%)")
    return f" [{'/'.join(parts)}]" if parts else ""

def _line_format(t, df):
    base = f"{get_trend(df)} {TICKER_NAME_MAP.get(t, t)} [{get_market_label(t)}]"
    base += _valuation_tag(t)
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

if tracking_result:
    _new_count, _resolved_count, _hit_count = tracking_result
    if _resolved_count > 0:
        _hit_rate = _hit_count / _resolved_count * 100
        msg += f"\n\n📊 シグナル的中率({TRACKING_HOLD_DAYS}営業日後+{TRACKING_HIT_THRESHOLD_PCT:.1f}%以上): {_hit_count}/{_resolved_count}件 ({_hit_rate:.1f}%)"

send_line(msg)

# スプレッドシートに履歴を書き込む
# ============================================================
write_to_spreadsheet(today, ace_stocks_all, king_stocks_all, poly_stocks_all, bep_stocks_all, fundamental_commentaries, fundamental_valuations)
