import os
import json
import time
import numpy as np
import pandas as pd
import yfinance as yf
import requests
import jquantsapi
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

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
# LINE通知設定 (GAS経由)
# ============================================================
GAS_URL = "https://script.google.com/macros/s/AKfycbymWDgoF3XPJGjSFmoK6_Gyan2cN0CFE9q2P5IkAgyLbMRdbBXFCnPZzne6vgCnJSQZDQ/exec"
# ============================================================
# Gemini AIコメント生成
# ============================================================
def generate_gemini_comment(signal_stocks, signal_type):
    try:
        import google.generativeai as genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key or not signal_stocks:
            return ""
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        stocks_text = "\n".join(signal_stocks[:5])  # 上位5銘柄のみ
        prompt = f"""
以下は日本株のフジコ投資法で{signal_type}シグナルが出た銘柄です。
{stocks_text}

これらの銘柄について、投資家向けに簡潔なコメントを3行以内で生成してください。
・市場全体の状況との関連
・注目すべき共通点やセクター
・投資する際の注意点
日本語で簡潔に答えてください。
"""
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"⚠️ Geminiコメント生成失敗: {e}")
        return ""
def send_line(message):
    try:
        res = requests.post(GAS_URL, json={"message": message}, timeout=10)
        if res.status_code == 200:
            print("✅ LINE送信完了")
        else:
            print(f"❌ LINE送信失敗: {res.status_code}")
    except Exception as e:
        print(f"❌ LINE送信エラー: {e}")

# ============================================================
# スプレッドシートへの履歴書き込み
# ============================================================
def write_to_spreadsheet(today, ace_stocks, poly_stocks, bep_stocks):
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
            ws.append_row(["日付", "種別", "銘柄名"])
        for stock in ace_stocks:
            ws.append_row([today, "Ace", stock.replace("・", "")])
        for stock in poly_stocks:
            ws.append_row([today, "ポリグラフ", stock.replace("・", "")])
        for stock in bep_stocks:
            ws.append_row([today, "Ace×BEP", stock.replace("・", "")])
        print("✅ スプレッドシート書き込み完了")
    except Exception as e:
        print(f"❌ スプレッドシート書き込み失敗: {e}")

# ============================================================
# J-Quants APIで全上場銘柄コードを取得
# ============================================================
def get_all_tickers(ticker_name_map):
    try:
        api_key = os.environ.get("JQUANTS_API_KEY", "")
        if not api_key:
            print("⚠️ JQUANTS_API_KEY未設定 → 131銘柄リストを使用")
            return list(ticker_name_map.keys()), ticker_name_map
        cli = jquantsapi.ClientV2(api_key=api_key)
        df_list = cli.get_list()
        df_stocks = df_list[df_list['S33'] != '9999'].copy()
        tickers = [str(code)[:-1] + ".T" for code in df_stocks['Code'].astype(str)]
        names = df_stocks['CoName'].tolist()
        name_map = dict(zip(tickers, names))
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
    df = detect_bullish_ep(df)
    return df

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
            (df["Close"] >= df["Low52"] * 1.3) &
            (df["Close"] >= df["High52"] * 0.75)
        )
        df["Ace"]  = base_7 & (df["RSR"] >= 70)
        df["King"] = base_7 & (df["RSR"] >= 60) & (df["RSR"] < 70)
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
             take_profit=20.0, stop_loss=-5.0, max_hold_days=60):
    all_returns, ticker_stats = [], {}
    for ticker, df in combined_df.groupby("Ticker"):
        df = df.reset_index(drop=True)
        sig_idx = np.where(df[signal_col] == True)[0]
        ticker_returns = []
        for idx in sig_idx:
            if idx + 1 >= len(df): continue
            buy_p = df.iloc[idx]["Close"]
            exited = False
            for i in range(idx + 1, min(idx + 1 + max_hold_days, len(df))):
                pnl = (df.iloc[i]["Close"] - buy_p) / buy_p * 100
                if pnl <= stop_loss or pnl >= take_profit:
                    all_returns.append(pnl); ticker_returns.append(pnl); exited = True; break
            if not exited:
                pnl = (df.iloc[min(idx + max_hold_days, len(df)-1)]["Close"] - buy_p) / buy_p * 100
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
END   = "2026-06-28"
BENCH = "1306.T"

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
            print(f"  ・{TICKER_NAME_MAP.get(ticker, ticker)} ({ticker})")
            found = True
    if not found:
        print("  (該当なし)")

# ============================================================
# LINE通知送信
# ============================================================
print("\n📱 LINE通知送信中...")
today = date.today().strftime("%Y/%m/%d")
msg = f"📊 {today} フジコシグナル\n"
msg += "=" * 25 + "\n"

ace_stocks = [f"・{TICKER_NAME_MAP.get(t, t)}" for t, df in combined_df.groupby("Ticker") if df["Ace_Start"].tail(3).any()][:20]
msg += f"\n🅰️ Ace点灯中({len(ace_stocks)}銘柄):\n"
msg += "\n".join(ace_stocks) if ace_stocks else "  (該当なし)"

poly_stocks = [f"・{TICKER_NAME_MAP.get(t, t)}" for t, df in combined_df.groupby("Ticker") if df["Polygraph_Start"].tail(3).any()][:20]
msg += f"\n\n🎯 ポリグラフ点灯中({len(poly_stocks)}銘柄):\n"
msg += "\n".join(poly_stocks) if poly_stocks else "  (該当なし)"

bep_stocks = [f"・{TICKER_NAME_MAP.get(t, t)}" for t, df in combined_df.groupby("Ticker") if df["Ace_with_BEP_Start"].tail(3).any()][:10]
msg += f"\n\n🅰️🐢 Ace×BEP同時({len(bep_stocks)}銘柄):\n"
msg += "\n".join(bep_stocks) if bep_stocks else "  (該当なし)"
# Geminiコメントを追加
ai_comment = generate_gemini_comment(ace_stocks, "Ace")
if ai_comment:
    msg += f"\n\n🤖 AIコメント:\n{ai_comment}"
send_line(msg)

# ============================================================
# HTML結果ページ生成 (GitHub Pages用)
# ============================================================
print("\n📄 HTMLページ生成中...")

def signal_table_html(stocks, title, emoji):
    def make_row(s):
        name = s.replace("・", "")
        # 銘柄コードを探して TradingView リンクを生成
        ticker = next((t for t, n in TICKER_NAME_MAP.items() if n == name), None)
        if ticker:
            code = ticker.replace(".T", "")
            url = f"https://www.tradingview.com/chart/?symbol=TSE%3A{code}"
            return f'<li><a href="{url}" target="_blank">{s}</a></li>'
        return f"<li>{s}</li>"
    rows = "".join(make_row(s) for s in stocks) if stocks else "<li class='none'>該当なし</li>"
    return f"""
    <div class="card">
      <h2>{emoji} {title} ({len(stocks)}銘柄)</h2>
      <ul>{rows}</ul>
    </div>
    """

ace_list  = [TICKER_NAME_MAP.get(t, t) for t, df in combined_df.groupby("Ticker") if df["Ace_Start"].tail(3).any()]
king_list = [TICKER_NAME_MAP.get(t, t) for t, df in combined_df.groupby("Ticker") if df["King_Start"].tail(3).any()]
poly_list = [TICKER_NAME_MAP.get(t, t) for t, df in combined_df.groupby("Ticker") if df["Polygraph_Start"].tail(3).any()]
bep_list  = [TICKER_NAME_MAP.get(t, t) for t, df in combined_df.groupby("Ticker") if df["Ace_with_BEP_Start"].tail(3).any()]

top10_html = ""
if not rankings["Ace_Start"].empty:
    top10 = rankings["Ace_Start"].sort_values("_sort", ascending=False).head(10)
    rows = "".join(
        f"<tr><td>{r['会社名']}</td><td>{r['シグナル回数']}</td><td>{r['勝率']}</td><td>{r['平均リターン']}</td></tr>"
        for _, r in top10.iterrows()
    )
    top10_html = f"""
    <div class="card">
      <h2>🏆 優秀銘柄ランキング TOP10</h2>
      <table>
        <tr><th>会社名</th><th>シグナル回数</th><th>勝率</th><th>平均リターン</th></tr>
        {rows}
      </table>
    </div>
    """

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>フジコシグナル {today}</title>
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
  a {{ color: #7eb8f7; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
  th, td {{ text-align: left; padding: 6px 4px; border-bottom: 1px solid #2a2d3a; }}
  th {{ color: #888; font-weight: normal; }}
</style>
</head>
<body>
  <h1>📊 フジコシグナル</h1>
  <div class="updated">最終更新: {today}</div>

  {signal_table_html(ace_list, "Ace点灯中", "🅰️")}
  {signal_table_html(king_list, "King点灯中", "👑")}
  {signal_table_html(poly_list, "ポリグラフ点灯中", "🎯")}
  {signal_table_html(bep_list, "Ace×BEP同時", "🅰️🐢")}
  {top10_html}

</body>
</html>"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("✅ index.html 生成完了")

# ============================================================
# スプレッドシートに履歴を書き込む
# ============================================================
write_to_spreadsheet(today, ace_stocks, poly_stocks, bep_stocks)
