"""ウォークフォワード検証: 各四半期の初めに「その時点までのデータだけ」で監視銘柄を選び、
その四半期のサイン(Ace/King/ポリグラフ/Ace×BEP)の成績を測る。

backtest_watchlist.py(今の監視銘柄を過去にさかのぼって当てる)には先読みバイアスがある。
こちらは各時点で build_watchlist.py と同じ関数・同じ基準で選び直すので、そのバイアスを外した成績になる。

流れ(四半期の初日 t ごと):
  1. 選定: t より前のデータだけで 流動性(20日平均売買代金3億円以上) → 週足トレンド → Fスコア
     (build_watchlist.check_liquidity / check_long_term_trend / calc_f_score をそのまま使う)
  2. 運用: t から次の四半期の初日までに出た Start サイン(RSRは選んだ銘柄の中だけで順位付け=本番と同じ)を
     翌営業日の始値で買い、fujiko.backtest と同じ出口(2×ATR損切り/4×ATR利確/60営業日)で決済
  3. 全四半期の取引を1つにまとめ、1%リスク・20%上限・100株単位で資金を動かす(sizing.simulate_portfolio)
  4. 年別・四半期別の成績を、1306買い持ちと同じ形式で出す

Fスコア(EDINET DB /v1/companies/{edinet_code}/financials。submit_date(提出日)が t より前の本決算の直近2期。
点時点の値なので先読みなし)。本番(build_watchlist.calc_f_score)と同じ9項目で判定する:
  ① ROA>0  ② 営業CF>0  ③ ROA改善  ④ 営業CF>純利益  ⑥ 流動比率上昇(流動資産/流動負債)  ⑨ 総資産回転率の上昇
  ⑤ DEレシオ低下: 有利子負債(ibd_current+ibd_noncurrent)/純資産。2期とも有利子負債が取れない銘柄(IFRS等)は負債合計/純資産
  ⑦ 希薄化なし: (発行済株式数(期末)-自己株式数)×split_adjustment_factor。自己株式数が2期とも取れなければ発行済株式数のみで比較。
     split_adjustment_factor は株式分割の単位をそろえるだけの係数(分割で誤って「希薄化」になるのを防ぐ)
  ⑧ 粗利率上昇: 粗利(無ければ 売上-売上原価)/売上。取れない期があれば、その項目だけ判定から除外
  決算が2期取れない銘柄は本番同様「データ不足=不合格」。
  赤字(直近の提出済み本決算の当期純利益<=0)の銘柄は、Fスコアの点数に関係なく不合格(本番 score_pending_candidates と同じ)。
  使う決算は選定日 t より前に提出済みの最新の本決算だけ(Fスコアと同じ fin_data_asof)。四半期の間は t の監視銘柄を固定するので、
  期中に出た決算は次の選定日まで使わない(先読みなし)。
  金融・不動産5業種(build_watchlist.EXCLUDED_S33)は除外する。--cache-only は .cache_wf/s33.json(通常実行で保存)の業種コードで除外する。
  Fスコアなしの版(流動性+トレンドのみ)も同時に出すので、Fスコアの効果はその差で見られる。

EDINET DBの呼び出し上限(無料枠: 1日100回・月2,800回)への対応:
  - 証券コード→EDINETコードの変換は /v1/companies?per_page=5000 の1回で全社分を取る(.cache_wf/edinetdb/_companies.json)
  - 財務は1銘柄1回(years省略=全期間)。取得結果は .cache_wf/edinetdb/<コード>.json に保存し、2回目以降は続きから
  - --edinetdb-daily-limit(既定40)回で止める。x-ratelimit-remaining が20以下でも止める
  - 取得が終わっていないときはバックテストを走らせず終了する(未取得を「データ不足」にして成績が歪むのを防ぐ。--partial で強制続行)

残るバイアス(解消できない):
  - 銘柄一覧は「今」のJ-Quants上場銘柄。過去に上場廃止になった銘柄は入っていない(生き残りバイアス。成績は良めに出る)
  - 市場区分(プライム/スタンダード/グロース)の絞り込みも今の区分
  - 株価はyfinance(分割・配当調整済み)

使い方(データ取得に時間がかかる。初回は株価約3,900銘柄+決算の取得):
  python walkforward.py                  # 本番相当(2022年から)
  python walkforward.py --limit 150      # 動作確認用: 銘柄を先頭150件に絞る
  python walkforward.py --first 2023-01-01 --out docs/walkforward.html
  python walkforward.py --tickers-file codes.txt --edinetdb-daily-limit 40 --partial   # 指定銘柄だけ(--limit より優先)
  python walkforward.py --cache-only --out docs/walkforward_test.html   # Fスコア閾値(関門なし/3〜7点以上)の比較。キャッシュだけで実行
ラジ株ナビ・LINE・スプレッドシートは使わない。J-Quants(銘柄一覧だけ)・EDINET DB(決算)・yfinance(株価)。
"""
import argparse
import contextlib
import html
import io
import json
import os
import pickle
import re
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache_wf")
PRICE_START = "2018-12-01"       # 3年前比較(週足156本)と200日線・52週の助走のため、最初の選定日より3年以上前から
BENCH_TICKER = "1306.T"
SIGNALS = [("Ace_Start", "Ace"), ("King_Start", "King"),
           ("Polygraph_Start", "ポリグラフ"), ("Ace_with_BEP_Start", "Ace×BEP")]
MIN_BARS = 250                   # 本番(fujiko.py)と同じ: 250営業日未満の銘柄は対象外
STALE_DAYS = 10                  # 選定日の直前10日以内に株価が無い銘柄(上場廃止・売買停止)は除く
FS_STALE_DAYS = 550              # 直近の本決算がこれより古い銘柄はFスコアを付けない(データ不足扱い)
F_POINTS_DEFAULT = "3,4,5,6,7"   # 比較するFスコアの閾値(点)。「関門なし」は常に加える
NO_GATE = "関門なし"
EXIT_RULES = {"atr_stop_mult": 2.0, "atr_profit_mult": 4.0, "max_hold_days": 60}  # --stop-mult/--profit-mult/--max-hold
EXIT_LABEL = "損切り2.0×ATR / 利確4.0×ATR / 最大保有60営業日"
N225_TICKER = "^N225"
OOS_SELECT = (2022, 2024)        # 「選ぶ期間」: 2022-01〜2024-12
OOS_TEST = (2025, 9999)          # 「試す期間」: 2025-01〜(データの最後=2026-10)
REGIME_MA_DAYS = 200             # 改善案A: 1306の終値が200日線より下ならAce・Kingを見送る
OOS_F_POINTS = 5                 # 改善案B: Kingに付けるFスコアの関門(5点以上)
OFFLINE = False                  # --cache-only のとき True: 外部通信を一切しない


def f_label(points):
    return f"Fスコア{points}点以上"

# ---- EDINET DB /v1/companies/{edinet_code}/financials の年次行(キャッシュには下の項目だけ残す) ----
EDB_BASE = "https://edinetdb.jp/v1"
EDB_DIR = os.path.join(CACHE_DIR, "edinetdb")
EDB_FIELDS = [
    "fiscal_year", "submit_date", "treasury_shares_as_of_date",
    "revenue", "operating_income", "net_income", "cost_of_sales", "gross_profit",
    "total_assets", "net_assets", "total_liabilities", "current_assets", "current_liabilities",
    "ibd_current", "ibd_noncurrent", "cf_operating",
    "shares_issued_fiscal_year_end", "shares_issued", "treasury_shares_count", "split_adjustment_factor",
]
EDB_DAILY_LIMIT = 40             # 1回の実行で呼ぶ回数の既定(無料枠は1日100回)
EDB_STOP_REMAINING = 20          # ヘッダー x-ratelimit-remaining がこれ以下なら取得を止める
FS_COLS = {  # 表の列 -> EDINET DBのキー
    "sales": "revenue", "op": "operating_income", "np": "net_income", "cos": "cost_of_sales", "gp": "gross_profit",
    "ta": "total_assets", "na": "net_assets", "tl": "total_liabilities",
    "ca": "current_assets", "cl": "current_liabilities",
    "ibd_c": "ibd_current", "ibd_n": "ibd_noncurrent", "cfo": "cf_operating",
    "sh_fye": "shares_issued_fiscal_year_end", "sh_iss": "shares_issued",
    "tr": "treasury_shares_count", "factor": "split_adjustment_factor",
}


# ============================================================
# 純粋な計算(ネットワークなし。tests/test_walkforward.py で検証)
# ============================================================
def rebalance_dates(trading_days, first, end):
    """四半期の初日(1/4/7/10月)以降で最初の営業日のリスト。first以降、end未満"""
    first, end = pd.Timestamp(first), pd.Timestamp(end)
    out, q = [], pd.Timestamp(year=first.year, month=first.month, day=1)
    while q < end:
        later = trading_days[trading_days >= q]
        if len(later) and later[0] < end and (not out or later[0] > out[-1]):
            out.append(later[0])
        q = q + pd.DateOffset(months=3)
    return out


def parse_edinetdb_rows(rows):
    """EDINET DBの年次財務の生の行 → 本決算の表(列は FS_COLS のキー + disc_date・fy_end)。空なら空DataFrame。
    disc_date = submit_date(有報の提出日。これ以前は使わない=先読み防止)。
    fy_end = treasury_shares_as_of_date(決算期末日)。無ければ提出日の90日前で代用"""
    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    df = pd.DataFrame()
    df["disc_date"] = pd.to_datetime(raw.get("submit_date"), errors="coerce").dt.normalize()
    fy_end = pd.to_datetime(raw.get("treasury_shares_as_of_date"), errors="coerce")
    df["fy_end"] = fy_end.fillna(df["disc_date"] - pd.Timedelta(days=90))
    for col, key in FS_COLS.items():
        df[col] = pd.to_numeric(raw[key], errors="coerce") if key in raw.columns else np.nan
    df["factor"] = df["factor"].fillna(1.0)
    return df.dropna(subset=["disc_date", "fy_end"]).reset_index(drop=True)


def fin_data_asof(fy_df, asof):
    """asof より前に提出された本決算の直近2期を、build_watchlist.calc_f_score が読む形にする。
    取れなければ None(=データ不足)。同じ期が複数あれば、asof 時点で最後に提出されたものを採用"""
    if fy_df is None or fy_df.empty:
        return None
    asof = pd.Timestamp(asof)
    d = fy_df[fy_df["disc_date"] < asof]
    d = d.sort_values("disc_date").drop_duplicates("fy_end", keep="last").sort_values("fy_end")
    d = d.tail(2)
    if len(d) < 2 or (asof - d["fy_end"].iloc[-1]).days > FS_STALE_DAYS:
        return None

    def num(v):
        return None if v is None or pd.isna(v) else float(v)

    rows = [{c: num(r[c]) for c in FS_COLS} | {"factor": num(r["factor"]) or 1.0} for _, r in d.iterrows()]
    # 銘柄ごとに2期そろえて同じ方法で比べる(期によって方法が変わると比較にならないため)
    use_ibd = all(r["ibd_c"] is not None or r["ibd_n"] is not None for r in rows)
    use_tr = all(r["tr"] is not None for r in rows)

    years = {}
    for (_, src), r in zip(d.iterrows(), rows):
        ta, na, sales = r["ta"], r["na"], r["sales"]
        debt = (r["ibd_c"] or 0.0) + (r["ibd_n"] or 0.0) if use_ibd else r["tl"]
        sh = r["sh_fye"] if r["sh_fye"] is not None else r["sh_iss"]
        gp = r["gp"] if r["gp"] is not None else (sales - r["cos"] if sales is not None and r["cos"] is not None else None)
        years[src["fy_end"].strftime("%Y-%m-%d")] = {
            "roa": r["np"] / ta if r["np"] is not None and ta else None,
            "cashFlowFromOperations": r["cfo"],
            "netIncome": r["np"],
            "debtToEquityRatio": debt / na if debt is not None and na and na > 0 else None,
            "currentRatio": r["ca"] / r["cl"] if r["ca"] is not None and r["cl"] else None,
            # 分割の単位をそろえた発行済株式数(自己株式を除く。自己株式が2期とも取れなければ除かずに比較)
            "sharesOutstanding": (sh - (r["tr"] if use_tr else 0.0)) * r["factor"] if sh is not None else None,
            "grossProfitMargin": gp / sales if gp is not None and sales else None,
            "assetTurnover": sales / ta if sales is not None and ta else None,
        }
    return {"fiscalYears": years}


def f_pass(f, points, bw):
    """Fスコアの結果 f({"score","maxScore",...} or None)が points 点以上の関門を通るか。
    build_watchlist.classify_f_score と同じ整数比較(丸め済みratioは使わない): score × 9 >= points × maxScore。
    項目欠損で分母(maxScore)が9未満の銘柄は、達成率 score/maxScore で同じ基準を当てる。f が None(データ不足)は不合格"""
    if not f:
        return False
    max_score = f.get("maxScore")
    if not max_score or max_score <= 0:
        return False
    return f["score"] * bw.F_SCORE_DENOMINATOR >= points * max_score


def parse_points(text):
    """--f-thresholds の文字列("3,4,5,6,7")→ 昇順の点数リスト(1〜9)"""
    try:
        pts = sorted({int(x) for x in str(text).split(",") if x.strip()})
    except ValueError:
        pts = []
    if not pts or any(p < 1 or p > 9 for p in pts):
        sys.exit("--f-thresholds は1〜9の整数をカンマ区切りで指定してください(例 3,4,5,6,7)")
    return pts


S33_PATH = os.path.join(CACHE_DIR, "s33.json")
S33_NAMES = {"7050": "銀行業", "7100": "証券、商品先物取引業", "7150": "保険業", "7200": "その他金融業", "8050": "不動産業"}


def load_s33_map():
    """{ticker: 東証33業種コード(4桁文字列)}。通常実行で保存したもの。無ければ None"""
    try:
        with open(S33_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_s33_map(bw):
    """通常実行(--cache-only でない)のとき、J-Quantsの銘柄一覧から {ticker: S33} を保存する(1回の呼び出し)。
    --cache-only はこれを読んで金融・不動産の除外を行う。失敗しても本処理は続ける"""
    try:
        df = bw.jquantsapi.ClientV2(api_key=os.environ.get("JQUANTS_API_KEY", "")).get_list()
        m = {str(c)[:-1] + ".T": str(s).strip() for c, s in zip(df["Code"].astype(str), df["S33"])}
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(S33_PATH, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
        return m
    except Exception as e:
        print(f"⚠️ 業種コード(S33)の保存に失敗: {e}")
        return None


def split_excluded_sectors(tickers, s33_map, excluded):
    """build_watchlist.EXCLUDED_S33(excluded)に当たる銘柄を除く。戻り値: (残す銘柄, {除外した銘柄: S33コード})。
    s33_map に無い銘柄は業種不明として残す"""
    s33_map = s33_map or {}
    drop = {t: s33_map[t] for t in tickers if str(s33_map.get(t, "")).strip() in excluded}
    return [t for t in tickers if t not in drop], drop


def report_dropped_sectors(dropped, s33_map):
    """除外した銘柄数と業種を表示"""
    by = {}
    for t, code in dropped.items():
        by.setdefault(code, []).append(t)
    detail = " / ".join(f"{S33_NAMES.get(c, c)}({c}) {len(ts)}銘柄: {', '.join(x[:-2] for x in sorted(ts))}" for c, ts in sorted(by.items()))
    print(f"🚫 金融・不動産5業種の除外: {len(dropped)}銘柄(業種コード既知 {len(s33_map or {})}銘柄)" + (f" → {detail}" if detail else ""))


NAME_EXCLUDE_WORDS = ("銀行", "証券", "保険", "金融", "不動産")   # --tickers-file 用: 銘柄名に含むものは対象外


def parse_tickers_file(text):
    """--tickers-file の中身 → (ティッカーのリスト(重複除去・入力順), {ティッカー: 銘柄名}, 読めなかった行)。
    1行1コード。コードの後ろに空白・カンマ・タブ区切りで銘柄名を書いてもよい(名前での除外に使う)。
    7203 / 7203.T / 72030(5桁) / 130A を受け付ける。空行と # 始まりの行は無視"""
    tickers, names, bad = [], {}, []
    for raw in str(text).splitlines():
        line = raw.strip().lstrip(chr(0xFEFF))
        if not line or line.startswith("#"):
            continue
        head, *rest = re.split(r"[,\t ]+", line, maxsplit=1)
        code = head.upper()
        if code.endswith(".T"):
            code = code[:-2]
        if len(code) == 5 and code.endswith("0"):
            code = code[:4]
        if not re.fullmatch(r"[0-9][0-9A-Z]{3}", code):
            bad.append(line)
            continue
        t = code + ".T"
        if t not in names and t not in tickers:
            tickers.append(t)
        if rest and rest[0].strip():
            names[t] = rest[0].strip()
    return tickers, names, bad


def split_excluded_names(tickers, names):
    """銘柄名に NAME_EXCLUDE_WORDS を含む銘柄を除く。戻り値: (残す銘柄, {除外した銘柄: 銘柄名})。名前が分からない銘柄は残す"""
    drop = {t: names[t] for t in tickers if names.get(t) and any(w in names[t] for w in NAME_EXCLUDE_WORDS)}
    return [t for t in tickers if t not in drop], drop


def block_network():
    """--cache-only 用: このプロセスから外部へ通信できなくする(API・yfinanceを誤って呼ばないための保険)"""
    import socket

    def _deny(*_a, **_k):
        raise RuntimeError("--cache-only: 外部通信は禁止されています")
    socket.socket.connect = _deny
    socket.socket.connect_ex = _deny
    socket.create_connection = _deny
    socket.getaddrinfo = _deny


def regime_ok_series(bench_close, window=REGIME_MA_DAYS):
    """改善案A用: 日付ごとの「1306の終値が window日線より上」(bool)。その日までの終値だけで決まる。
    window日分の履歴が無い日は False(見送り)"""
    ma = bench_close.rolling(window).mean()
    return (bench_close > ma).fillna(False)


def judge_candidate(base, m):
    """採用候補の条件(人間が判断する材料)。試す期間で 騰落率・シャープが baseline 以上 かつ 最大DDが悪化しない
    (最大DDは負の値なので、baseline 以上=浅い or 同じ)。どちらかが None なら False"""
    if not base or not m:
        return False
    return (m["final_return_pct"] >= base["final_return_pct"] and m["sharpe"] >= base["sharpe"]
            and m["max_drawdown_pct"] >= base["max_drawdown_pct"])


def ensure_n225(end):
    """日経平均(^N225)の終値をyfinanceから取り .cache_wf/n225_<end>.pkl に保存して返す(比較表示だけに使う。
    --cache-only で外部通信を遮断する前に呼ぶこと。キャッシュがあれば通信しない)"""
    path = os.path.join(CACHE_DIR, f"n225_{end}.pkl")
    cached = _load_pickle(path, None)
    if cached is not None and len(cached):
        return cached
    import yfinance as yf
    raw = yf.download(N225_TICKER, start=PRICE_START, end=end, auto_adjust=True, progress=False)
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = close.dropna().astype(float)
    close = close[~close.index.duplicated(keep="last")].sort_index()
    if len(close) < 250:
        sys.exit("日経平均(^N225)の株価を取得できませんでした")
    _save_pickle(path, close)
    return close


def trade_stats(trades, days):
    """取引ごとの成績。trades は fujiko.backtest が出す取引リスト(資金管理で見送る前の全シグナル分)。
    pnl_pct は往復取引コスト0.2%を引いた後の値。保有日数は営業日(エントリー日を1日目と数え、決済日を含む。days=1306の営業日)"""
    if not trades:
        return None
    pnl = np.array([t["pnl_pct"] for t in trades], dtype=float)
    ent = days.searchsorted(pd.DatetimeIndex([t["entry_date"] for t in trades]))
    ext = days.searchsorted(pd.DatetimeIndex([t["exit_date"] for t in trades]))
    return {"n": int(len(pnl)), "win_rate": float((pnl > 0).mean() * 100), "avg_pnl": float(pnl.mean()),
            "avg_hold": float((ext - ent + 1).mean())}


def is_loss(f):
    """直近の本決算の当期純利益が0以下(赤字)か。build_watchlist.score_pending_candidates の
    「net_income is not None and net_income <= 0 → 不合格(赤字)」と同じ規則(純利益が不明(None)なら赤字扱いにしない)。
    f は calc_f_score の戻り値で、net_income は fin_data_asof(asof) = asof より前に提出済みの決算の最新期の値"""
    ni = (f or {}).get("net_income")
    return ni is not None and ni <= 0


def apply_f_gate(survivors, asof, fscore_fn, bw, min_points=None, loss_out=None):
    """流動性+トレンド通過銘柄に Fスコアの関門をかける。戻り値: (通過リスト, 判定不能の件数)。
    min_points 指定時は f_pass(整数比較)、省略時は従来どおり ratio >= F_SCORE_RATIO_THRESHOLD(6/9)。
    赤字(is_loss)の銘柄は、Fスコアに関係なく不合格(build_watchlist本番と同じ)。loss_out(リスト)を渡すと、
    赤字で落とした銘柄のうち「赤字でなければ関門を通っていた銘柄」を追加する"""
    final, unknown = [], 0
    for t in survivors:
        f = fscore_fn(t, asof)
        if not f:
            unknown += 1
            continue
        passed = (f_pass(f, min_points, bw) if min_points is not None
                  else f["ratio"] >= bw.F_SCORE_RATIO_THRESHOLD)
        if not passed:
            continue
        if is_loss(f):
            if loss_out is not None:
                loss_out.append(t)
            continue
        final.append(t)
    return final, unknown


def select_watchlist(asof, prices, bw, fscore_fn=None, min_points=None):
    """asof より前のデータだけで build_watchlist と同じ基準の監視銘柄を選ぶ。
    prices: {ticker: OHLCV DataFrame}。fscore_fn(ticker, asof) -> {"score","maxScore","ratio"} or None(データ不足)。
    fscore_fn=None なら流動性+トレンドまで。min_points を渡すと Fスコアが min_points 点以上(整数比較。f_pass)で通す。
    省略時は従来どおり ratio >= F_SCORE_RATIO_THRESHOLD(6/9)。
    戻り値: (流動性+トレンド通過リスト, 最終リスト, 段階別の件数dict)"""
    asof = pd.Timestamp(asof)
    stage = {"対象": 0, "流動性通過": 0, "トレンド通過": 0, "Fスコア判定不能": 0, "Fスコア合格": 0}
    survivors = []
    for t, df in prices.items():
        h = df[df.index < asof]
        if len(h) < MIN_BARS or (asof - h.index[-1]).days > STALE_DAYS:
            continue
        stage["対象"] += 1
        ok, _ = bw.check_liquidity(h)
        if not ok:
            continue
        stage["流動性通過"] += 1
        ok, _ = bw.check_long_term_trend(h)
        if not ok:
            continue
        stage["トレンド通過"] += 1
        survivors.append(t)
    final = []
    if fscore_fn is not None:
        final, stage["Fスコア判定不能"] = apply_f_gate(survivors, asof, fscore_fn, bw, min_points)
        stage["Fスコア合格"] = len(final)
    return survivors, final, stage


def align_equity(eq, days, capital):
    """資産推移を共通の営業日 days に揃える(取引前は資金のまま、決済後は最後の値を維持)"""
    if eq is None or len(eq) == 0:
        return pd.Series(float(capital), index=days)
    return eq.reindex(eq.index.union(days)).ffill().fillna(float(capital)).reindex(days)


def span_return(eq, t0, t1, capital):
    """[t0, t1) の騰落率(%)。直前日の資産を基準にする(最初の期間は元手)"""
    before = eq[eq.index < t0]
    within = eq[(eq.index >= t0) & (eq.index < t1)]
    if within.empty:
        return None
    base = float(before.iloc[-1]) if len(before) else float(capital)
    return (float(within.iloc[-1]) / base - 1) * 100


# ============================================================
# データ取得(キャッシュあり)
# ============================================================
def _load_pickle(path, default):
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return default


def _save_pickle(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(obj, f)


def download_prices(tickers, end, refresh=False, chunk=100, offline=False):
    """yfinanceで株価をまとめて取得し、{ticker: DataFrame(Open/High/Low/Close/Volume)}を返す。
    取れなかった銘柄は含めない。キャッシュは終了日ごと(--refreshで作り直し)"""
    path = os.path.join(CACHE_DIR, f"prices_{end}.pkl")
    cache = {} if refresh else _load_pickle(path, {})
    todo = [t for t in tickers if t not in cache]
    if offline and todo:
        sys.exit(f"⏹️ 株価キャッシュに未取得の銘柄が{len(todo)}件あるため終了します(--cache-only は取りに行きません): " + ", ".join(todo[:20]))
    if todo:
        import yfinance as yf
    print(f"📥 株価: キャッシュ{len(tickers) - len(todo)}件 / 新規取得{len(todo)}件")
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        raw = None
        for attempt in range(2):
            try:
                raw = yf.download(part, start=PRICE_START, end=end, auto_adjust=True,
                                  group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:
                print(f"⚠️ yfinance失敗({e}) → 再試行")
                time.sleep(5)
        for t in part:
            df = None
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                sub = sub[~sub.index.duplicated(keep="last")].sort_index()
                if len(sub):
                    df = sub.astype("float32")
            except Exception:
                pass
            cache[t] = df  # 取れなかった銘柄はNone(再取得しない)
        _save_pickle(path, cache)
        print(f"  {min(i + chunk, len(todo))}/{len(todo)}")
    wanted = set(tickers)
    return {t: d for t, d in cache.items() if d is not None and t in wanted}


def _edb_path(name):
    return os.path.join(EDB_DIR, f"{name}.json")


def _edb_load(name):
    try:
        with open(_edb_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _edb_save(name, obj):
    os.makedirs(EDB_DIR, exist_ok=True)
    with open(_edb_path(name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _edb_get(path, params, api_key, state):
    """EDINET DBを1回呼ぶ。呼び出し回数と、ヘッダーの日の残り回数を state に記録する"""
    if OFFLINE:
        raise RuntimeError("--cache-only: EDINET DBは呼びません")
    import requests
    res = requests.get(f"{EDB_BASE}{path}", params=params, timeout=60,
                       headers={"X-API-Key": api_key, "Accept": "application/json"})
    state["calls"] += 1
    rem = res.headers.get("x-ratelimit-remaining")
    if rem is not None and str(rem).isdigit():
        state["remaining"] = int(rem)
    res.raise_for_status()
    return res.json()


def _edb_can_call(state, daily_limit):
    if state["calls"] >= daily_limit:
        return False, f"今回の呼び出し上限({daily_limit}回)に達した"
    if state["remaining"] is not None and state["remaining"] <= EDB_STOP_REMAINING:
        return False, f"日の残りが{state['remaining']}回(<= {EDB_STOP_REMAINING})"
    return True, ""


def _sec4(code):
    code = str(code).strip()
    return code[:4] if len(code) == 5 and code.endswith("0") else code


def load_edinet_codes(api_key, state):
    """証券コード(4桁)→EDINETコードの全社分を1回の呼び出しで取得してキャッシュ。取れなければ None"""
    m = _edb_load("_companies")
    if m is not None:
        return m
    ok, why = _edb_can_call(state, state["limit"])
    if not ok:
        print(f"⛔ 変換表を取得できない: {why}")
        return None
    data = _edb_get("/companies", {"per_page": 5000}, api_key, state)
    m = {_sec4(c["sec_code"]): c["edinet_code"] for c in data.get("data") or [] if c.get("sec_code") and c.get("edinet_code")}
    _edb_save("_companies", m)
    print(f"🗂️ 証券コード→EDINETコードの変換表を1回の呼び出しで取得: {len(m)}社")
    return m


def fetch_fy_tables(tickers, api_key, daily_limit):
    """tickers(例 7203.T)の本決算表を EDINET DB から取得。取得済み(.cache_wf/edinetdb)は飛ばして続きから。
    戻り値: ({ticker: DataFrame}, 未取得の銘柄リスト)"""
    state = {"calls": 0, "remaining": None, "limit": daily_limit}
    tables, todo = {}, []
    for t in tickers:
        entry = _edb_load(t.replace(".T", ""))
        if entry is None:
            todo.append(t)
        else:
            tables[t] = parse_edinetdb_rows(entry.get("rows"))
    total = len(tickers)
    codes = load_edinet_codes(api_key, state) if todo else {}
    if codes is None:
        return tables, todo
    pending = []
    for t in todo:
        code4 = t.replace(".T", "")
        if code4 in codes:
            pending.append(t)
        else:  # EDINET DBに無い銘柄(API不要)。判定できない銘柄として記録する
            if not OFFLINE:
                _edb_save(code4, {"edinet_code": None, "rows": []})
            tables[t] = pd.DataFrame()

    def progress():
        done = total - len(pending)
        rest = len(pending)
        print(f"  取得済み {done}/{total}銘柄 / あと{rest}回で完了 / 今回の呼び出し{state['calls']}回"
              + (f" / 日の残り{state['remaining']}回" if state["remaining"] is not None else ""))

    progress()
    while pending:
        ok, why = _edb_can_call(state, daily_limit)
        if not ok:
            print(f"⏸️ 取得を止めます: {why}。同じコマンドを再実行すると続きから取ります")
            break
        t = pending[0]
        code4 = t.replace(".T", "")
        try:
            data = _edb_get(f"/companies/{codes[code4]}/financials", None, api_key, state)
        except Exception as e:
            print(f"⚠️ 決算取得失敗 {t}: {e}(今回は止めます)")
            break
        rows = [{k: r.get(k) for k in EDB_FIELDS} for r in data.get("data") or []]
        _edb_save(code4, {"edinet_code": codes[code4], "rows": rows})
        tables[t] = parse_edinetdb_rows(rows)
        pending.pop(0)
        if (total - len(pending)) % 10 == 0 or not pending:
            progress()
    if pending:
        progress()
    return tables, pending


def report_treasury_coverage(tables):
    """自己株式数(treasury_shares_count)が本決算の行に入っている割合(2021年以降の開示分)"""
    frames = [d for d in tables.values() if len(d)]
    if not frames:
        return
    allrows = pd.concat(frames)
    allrows = allrows[allrows["disc_date"] >= "2021-01-01"]
    if allrows.empty:
        return
    n_tr = int(allrows["tr"].notna().sum())
    n_sh = int(allrows["sh_fye"].notna().sum())
    print(f"🔎 自己株式数 treasury_shares_count あり: {n_tr}/{len(allrows)}行({n_tr / len(allrows) * 100:.0f}%) / "
          f"期末発行済株式数 あり: {n_sh}/{len(allrows)}行({n_sh / len(allrows) * 100:.0f}%)")
    miss = sorted({t for t, d in tables.items() if len(d) and d[d["disc_date"] >= "2021-01-01"]["tr"].isna().any()})
    print(f"   自己株式数が欠ける行を持つ銘柄: {len(miss)}銘柄(その銘柄は発行済株式数のみで希薄化を判定)")


# ============================================================
# 実行
# ============================================================
def run(args):
    global OFFLINE, EXIT_LABEL
    EXIT_RULES.update(atr_stop_mult=args.stop_mult, atr_profit_mult=args.profit_mult, max_hold_days=args.max_hold)
    EXIT_LABEL = f"損切り{args.stop_mult:g}×ATR / 利確{args.profit_mult:g}×ATR / 最大保有{args.max_hold}営業日"
    print(f"出口ルール: {EXIT_LABEL}")
    offline = bool(args.cache_only)
    if offline and args.refresh:
        sys.exit("--cache-only と --refresh は同時に使えません")
    oos = bool(getattr(args, "oos", False))
    if oos and args.no_fscore:
        sys.exit("--oos は改善案B(Fスコアの関門)を使うため --no-fscore と同時に使えません")
    n225_close = None
    if oos:   # 日経平均はyfinanceだけ(キャッシュが無い時だけ通信。EDINET DB・ラジ株ナビは呼ばない)。--cache-only の遮断より前に取る
        n225_end = args.end or (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        n225_close = ensure_n225(n225_end)
    # EDINET DBのキーはここで読み(値は表示しない)、本体(build_watchlist/fujiko)には渡さない。
    # ラジ株ナビ等のキーは空にしてから本体を読み込む(呼ばない)。--cache-only では外部通信そのものを遮断する
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
    edb_key = "" if offline else os.environ.get("EDINETDB_API_KEY", "")
    os.environ.update({"RADIKABUNAVI_API_KEY": "", "GEMINI_API_KEY": "", "EDINETDB_API_KEY": "",
                       "GAS_URL": "", "GAS_TOKEN": "", "DRY_RUN": "1", "SKIP_FUNDAMENTALS": "1"})
    if offline:
        OFFLINE = True
        os.environ["JQUANTS_API_KEY"] = ""
        args.edinetdb_daily_limit = 0
        block_network()
        print("🔒 --cache-only: 外部通信を遮断しました。.cache_wf のキャッシュだけで実行します(未取得があれば止めます)")
    sys.path.insert(0, HERE)
    import build_watchlist as bw
    import fujiko
    import sizing

    capital = sizing.get_capital()
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if getattr(args, "end", None):   # --end 指定時は、その日付の株価キャッシュだけを使う(無ければ別の日付に切り替えず止める)
        end = args.end
        end_path = os.path.join(CACHE_DIR, f"prices_{end}.pkl")
        if not os.path.exists(end_path):
            sys.exit(f"⏹️ 株価キャッシュ {os.path.relpath(end_path, HERE)} がありません(--end {end})。別の日付には切り替えずに終了します")
    if not edb_key and not args.no_fscore and not offline:
        sys.exit("EDINETDB_API_KEY が未設定です")
    points_list = [] if args.no_fscore else parse_points(args.f_thresholds)
    if oos:
        points_list = [OOS_F_POINTS]   # 改善案Bで使う5点だけ計算する(通常のFスコア閾値比較は出さない)

    file_tickers, file_names = None, {}
    if args.tickers_file:
        file_tickers, file_names = load_tickers_file(args.tickers_file)
        print(f"📄 --tickers-file: {len(file_tickers)}銘柄を読み込み" + (f"(--limit {args.limit} は無視します)" if args.limit else ""))

    def apply_tickers_file_filters(tickers, names, file_tickers):
        """--tickers-file のときだけ、銘柄名に銀行・証券・保険・金融・不動産を含むものを除く(除外数を表示)"""
        if file_tickers is None:
            return tickers
        keep, drop = split_excluded_names(tickers, names)
        print(f"🚫 銘柄名での除外({'・'.join(NAME_EXCLUDE_WORDS)}を含む): {len(drop)}銘柄"
              + (" → " + ", ".join(f"{t[:-2]} {n}" for t, n in sorted(drop.items())) if drop else "")
              + ("" if any(names.get(t) not in (None, t) for t in tickers) else
                 "(銘柄名が分かりません: ファイルの各行に「コード 銘柄名」と書くか、通常実行で銘柄一覧を使ってください)"))
        return keep

    if offline:
        # J-Quantsの銘柄一覧は呼ばず、株価キャッシュに入っている銘柄を対象にする(作成時に市場区分で絞り込み済み)
        cached = _load_pickle(os.path.join(CACHE_DIR, f"prices_{end}.pkl"), None)
        if not cached:
            sys.exit(f"株価キャッシュ prices_{end}.pkl がありません(--cache-only は新規取得しません)")
        if file_tickers is not None:   # 指定銘柄(キャッシュに無い銘柄は download_prices が止めて報告する)
            tickers = list(file_tickers)
            name_map = {t: file_names.get(t, t) for t in tickers}
        else:
            tickers = [t for t, d in cached.items() if d is not None and t != BENCH_TICKER]
            name_map = {t: t for t in tickers}
        s33 = load_s33_map()
        if s33 is None:
            print(f"⚠️ {os.path.relpath(S33_PATH, HERE)} がありません: 金融・不動産5業種を除外できていません"
                  "(通常実行(--cache-onlyなし)を1回走らせると、業種コードが保存されます)")
        tickers, dropped = split_excluded_sectors(tickers, s33, bw.EXCLUDED_S33)
        report_dropped_sectors(dropped, s33)
        tickers = apply_tickers_file_filters(tickers, name_map, file_tickers)
        if args.limit and file_tickers is None:
            tickers = tickers[:args.limit]
        print(f"📋 対象ユニバース({'--tickers-file の銘柄' if file_tickers is not None else '株価キャッシュにある銘柄'}): {len(tickers)}銘柄")
    else:
        tickers, name_map, segs = bw.get_all_tickers()
        if file_tickers is not None:   # 指定銘柄のうち、今のJ-Quants一覧にあるものだけ(5業種は get_all_tickers が除外済み)
            listed = set(tickers)
            notfound = [t for t in file_tickers if t not in listed]
            if notfound:
                print(f"⚠️ 銘柄一覧に無い指定銘柄(上場廃止・5業種・コード誤り): {len(notfound)}件 " + ", ".join(x[:-2] for x in notfound))
            tickers = [t for t in file_tickers if t in listed]
            name_map = {**file_names, **name_map}
        else:
            tickers = [t for t in tickers if segs.get(t) in {"プライム", "スタンダード", "グロース"}]
        s33 = save_s33_map(bw)   # --cache-only で使う業種コードを保存(get_all_tickers が既に5業種を除いている)
        tickers, dropped = split_excluded_sectors(tickers, s33, bw.EXCLUDED_S33)
        report_dropped_sectors(dropped, s33)
        tickers = apply_tickers_file_filters(tickers, name_map, file_tickers)
        if args.limit and file_tickers is None:
            tickers = tickers[:args.limit]
        print(f"📋 対象ユニバース({'--tickers-file の銘柄' if file_tickers is not None else '今のJ-Quants'}): {len(tickers)}銘柄")

    prices = download_prices(tickers + [BENCH_TICKER], end, refresh=args.refresh, offline=offline)
    bench = prices.pop(BENCH_TICKER, None)
    if bench is None:
        sys.exit("1306.Tの株価を取得できませんでした")
    bench_close = bench["Close"].astype(float)
    dates = rebalance_dates(bench.index, args.first, pd.Timestamp(end))
    if len(dates) < 2:
        sys.exit("選定日が2つ未満です(--first を確認)")
    spans = list(zip(dates, dates[1:] + [pd.Timestamp(end)]))
    print(f"🗓️ 選定日 {len(dates)}回: {dates[0]:%Y-%m-%d} 〜 {dates[-1]:%Y-%m-%d}(以降は最後の期間)")

    # 1) 流動性+トレンドの通過銘柄(全期間)→ 決算が必要な銘柄だけ取得
    stage_by_date, survivors_by_date = {}, {}
    for t0, _ in spans:
        surv, _, st = select_watchlist(t0, prices, bw, fscore_fn=None)
        survivors_by_date[t0], stage_by_date[t0] = surv, st
    fy_tables = {}
    if not args.no_fscore:
        need = sorted({t for s in survivors_by_date.values() for t in s})
        print(f"📥 決算(EDINET DB): Fスコア判定が必要な{len(need)}銘柄(1日の上限 {args.edinetdb_daily_limit}回)")
        fy_tables, missing = fetch_fy_tables(need, edb_key, args.edinetdb_daily_limit)
        report_treasury_coverage(fy_tables)
        if missing:
            if not args.partial:
                print(f"⏹️ 決算の未取得が{len(missing)}銘柄あるため、ここで終了します(成績が歪むのを防ぐため。取りに行きません)。")
                print("   未取得: " + ", ".join(missing[:30]) + (" ..." if len(missing) > 30 else ""))
                return
            print(f"⚠️ --partial: 未取得{len(missing)}銘柄は「データ不足=不合格」として続行します")
            for t in missing:
                fy_tables[t] = pd.DataFrame()

    if args.diag and fy_tables:
        mins = pd.Series({t: d["disc_date"].min() for t, d in fy_tables.items() if len(d)})
        print(f"\n🔎 [診断1] 本決算(FY)の最古の開示日: 取得できた{len(mins)}/{len(fy_tables)}銘柄")
        if len(mins):
            print(f"  銘柄ごとの最古の開示日の中央値: {mins.sort_values().iloc[len(mins) // 2]:%Y-%m-%d} / 全体の最小: {mins.min():%Y-%m-%d} / 最大: {mins.max():%Y-%m-%d}")

    fcache = {}

    def fscore(t, asof):  # 同じ(銘柄, 選定日)は1回だけ計算し、全ての閾値で使い回す
        key = (t, pd.Timestamp(asof))
        if key not in fcache:
            fcache[key] = bw.calc_f_score(fin_data_asof(fy_tables.get(t), asof))
        return fcache[key]

    loss_by_variant = {}   # 閾値ごと・選定日ごとの「関門は通ったが赤字で落とした銘柄」
    variants = {NO_GATE: {}}
    for p in points_list:
        variants[f_label(p)] = {}
    for t0, _ in spans:
        variants[NO_GATE][t0] = survivors_by_date[t0]
        for p in points_list:
            lost = []
            fin, unknown = apply_f_gate(survivors_by_date[t0], t0, fscore, bw, p, loss_out=lost)
            loss_by_variant.setdefault(f_label(p), {})[t0] = lost
            variants[f_label(p)][t0] = fin
            stage_by_date[t0]["Fスコア判定不能"] = unknown   # 判定不能の件数は閾値によらず同じ
    if loss_by_variant:
        print("\n📉 赤字(直近の提出済み本決算の当期純利益<=0)で落とした銘柄(関門は通過していたが赤字): 平均件数/四半期・のべ件数")
        for vname, by_date in loss_by_variant.items():
            tot = sum(len(v) for v in by_date.values())
            print(f"  {vname}: 平均{tot / max(len(by_date), 1):.1f}件/四半期 / のべ{tot}件")
    if args.diag and points_list:
        print("\n🔎 [診断2] 四半期ごとのFスコア判定不能(判定が必要=流動性+トレンド通過)")
        for t0, _ in spans:
            n = len(survivors_by_date[t0])
            u = stage_by_date[t0]["Fスコア判定不能"]
            print(f"  {t0:%Y-%m-%d}: 判定が必要{n}銘柄 / 判定不能{u}銘柄" + (f"({u / n * 100:.0f}%)" if n else ""))

    # 2) 銘柄ごとの指標は1回だけ計算(RSRと判定は期間ごとに、選んだ銘柄の中で出し直す)
    union = sorted({t for v in variants.values() for lst in v.values() for t in lst})
    print(f"📊 指標計算: {len(union)}銘柄")
    indicators = {}
    for t in union:
        df = prices[t].astype(float)
        ind = fujiko.calculate_base_indicators(df)
        ind["Ticker"] = t
        indicators[t] = ind
    closes_all = {t: d["Close"] for t, d in indicators.items()}

    bench_days = bench_close.index[bench_close.index >= dates[0]]
    bench_eq = sizing.bench_equity(bench_close, capital, dates[0], bench_close.index[-1])
    results = {}
    sig_cols = [col for col, _ in SIGNALS]
    comb_cache = {}   # 監視銘柄リスト → RSR・シグナル計算後の表(期間に依存しない)

    def simulate(by_date, exit_rules, regime=None, cols=None):
        """1つの条件(監視銘柄の決め方・出口ルール・地合いフィルタ)で全期間の取引と資金管理後の成績を出す。
        regime: 日付→bool(Trueの日のシグナルだけ使う)。cols: 対象シグナル列(省略=全部)"""
        use = [(c, l) for c, l in SIGNALS if cols is None or c in cols]
        trades = {col: [] for col, _ in use}
        sizes = {}
        for t0, t1 in spans:
            wl = [t for t in by_date[t0] if t in indicators]
            sizes[t0] = len(wl)
            if len(wl) < 2:
                continue
            key = tuple(sorted(wl))
            if key not in comb_cache:
                comb = pd.concat([indicators[t] for t in wl])
                comb = fujiko.calc_cross_sectional_rsr(comb, bench_close)
                comb = fujiko.calc_signals(comb)
                comb_cache[key] = comb[["Ticker", "Open", "Close", "ATR14"] + sig_cols].copy()
            comb = comb_cache[key]
            in_span = (comb.index >= t0) & (comb.index < t1)
            if regime is not None:
                in_span = in_span & regime.reindex(comb.index).fillna(False).to_numpy()
            for col, _label in use:
                slim = comb[["Ticker", "Open", "Close", "ATR14"]].copy()
                slim[col] = comb[col] & in_span
                with contextlib.redirect_stdout(io.StringIO()):
                    fujiko.backtest(slim, col, name_map, trades_out=trades[col], **exit_rules)
        sims = {}
        for col, label in use:
            r = sizing.simulate_portfolio(trades[col], dict(closes_all), capital)
            r["equity"] = align_equity(r["equity"], bench_days, capital)
            sims[col] = r
        stats = {col: trade_stats(trades[col], bench_close.index) for col, _ in use}
        return {"sims": sims, "sizes": sizes, "trades": trades, "stats": stats}

    if oos:
        n225_eq = sizing.bench_equity(n225_close, capital, dates[0], bench_close.index[-1])
        n225_eq = align_equity(n225_eq, bench_eq.index, capital) if len(n225_eq) else n225_eq
        regime = regime_ok_series(bench_close)
        gate_name = f_label(OOS_F_POINTS)
        ak = ["Ace_Start", "King_Start"]
        base_exit = dict(EXIT_RULES)
        scen = [  # (名前, 説明, 監視銘柄, 出口ルール, 地合い, 対象シグナル)。1つずつだけ変える(組み合わせない)
            ("baseline", "今のルール", NO_GATE, base_exit, None, ak),
            ("A", f"地合いフィルタ: 1306の終値が{REGIME_MA_DAYS}日線より下の日はシグナルを見送る", NO_GATE, base_exit, regime, ak),
            ("B", f"Kingに{gate_name}の関門(Aceは対象外)", gate_name, base_exit, None, ["King_Start"]),
            ("C1", "損切り2×ATR・利確6×ATR", NO_GATE, {**base_exit, "atr_stop_mult": 2.0, "atr_profit_mult": 6.0}, None, ak),
            ("C2", "損切り3×ATR・利確6×ATR", NO_GATE, {**base_exit, "atr_stop_mult": 3.0, "atr_profit_mult": 6.0}, None, ak),
        ]
        oos_results = {}
        for name, desc, vname, ex, rg, cols in scen:
            oos_results[name] = {"desc": desc, "cols": cols, "res": simulate(variants[vname], ex, rg, cols)}
            print(f"  [OOS {name}] {desc}: " + " ".join(
                f"{l}:発生{len(oos_results[name]['res']['trades'][c])}件" for c, l in SIGNALS if c in cols))
        out = args.out or os.path.join("docs", "walkforward_oos.html")
        out = out if os.path.isabs(out) else os.path.join(HERE, out)
        if os.path.basename(out) == "walkforward.html":
            sys.exit("--oos の出力先を既存の walkforward.html にはできません(上書き防止)")
        table = oos_table(oos_results, bench_eq, n225_eq, capital)
        html_text = render_oos(table, capital, dates, bench_eq.index[-1], pd.Timestamp.today(), len(prices))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html_text)
        print_oos(table)
        print(f"✅ HTML出力: {out}")
        return

    for vname, by_date in variants.items():
        results[vname] = simulate(by_date, EXIT_RULES)
        sizes = results[vname]["sizes"]
        trades = results[vname]["trades"]
        print(f"  [{vname}] 監視銘柄 平均{np.mean([sizes.get(t0, 0) for t0, _ in spans]):.1f}件/四半期 / "
              + " ".join(f"{label}:発生{len(trades[col])}件" for col, label in SIGNALS))

    # 3) 出力
    cmp = compare_rows(results, spans, bench_eq, capital)
    out = args.out or os.path.join("docs", "walkforward.html")
    out = out if os.path.isabs(out) else os.path.join(HERE, out)
    html_text = render(capital=capital, dates=dates, spans=spans, stage_by_date=stage_by_date,
                       results=results, bench_eq=bench_eq, bench_days=bench_days, today=pd.Timestamp.today(),
                       n_universe=len(prices), cmp=cmp)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_text)
    print_summary(results, bench_eq, capital)
    print_compare(cmp)
    print(f"✅ HTML出力: {out}")


def year_table(eq, capital):
    """{年: metrics or None, "通期": metrics}。sizing.period_metrics と同じ基準(年の直前日の資産)"""
    import sizing
    out = {}
    for y in range(eq.index[0].year, eq.index[-1].year + 1):
        out[f"{y}年"] = sizing.period_metrics(eq, y, y)
    out["通期"] = sizing.period_metrics(eq, 0, 9999)
    return out


def print_summary(results, bench_eq, capital):
    print()
    print(f"出口ルール: {EXIT_LABEL}")
    b = year_table(bench_eq, capital)
    for vname, res in results.items():
        print(f"\n=== {vname} ===")
        for col, label in SIGNALS:
            r = res["sims"][col]
            y = year_table(r["equity"], capital)
            line = " / ".join(f"{k}:{'－' if m is None else format(m['final_return_pct'], '+.1f') + '%'}" for k, m in y.items())
            print(f"  [{label}] 取引{r['n_trades']}件(見送り{r['n_skipped']}) {line}")
    print("  [1306買い持ち] " + " / ".join(f"{k}:{'－' if m is None else format(m['final_return_pct'], '+.1f') + '%'}" for k, m in b.items()))


BENCH_LABEL = "1306買い持ち"


def _f(v, fmt, suffix=""):
    return "－" if v is None else format(v, fmt) + suffix


def compare_rows(results, spans, bench_eq, capital):
    """Fスコア閾値ごとの比較表のデータ。{シグナル名: [行...], "1306買い持ち": 通期metrics}。
    勝率・平均損益・平均保有日数は取引ごと(資金管理で見送る前の全シグナル)、
    通期騰落・最大DD・シャープは資金管理後(1%リスク・20%上限)の日次資産から"""
    cmp = {}
    for col, label in SIGNALS:   # Ace・King・ポリグラフ・Ace×BEP
        rows = []
        for vname, res in results.items():
            st, sim = res["stats"][col], res["sims"][col]
            m = year_table(sim["equity"], capital)["通期"]
            rows.append({
                "variant": vname,
                "watch": float(np.mean([res["sizes"].get(t0, 0) for t0, _ in spans])),
                "n_raw": st["n"] if st else 0, "n_exec": sim["n_trades"],
                "win": st["win_rate"] if st else None, "avg_pnl": st["avg_pnl"] if st else None,
                "avg_hold": st["avg_hold"] if st else None,
                "ret": m["final_return_pct"] if m else None, "mdd": m["max_drawdown_pct"] if m else None,
                "sharpe": m["sharpe"] if m else None})
        cmp[label] = rows
    cmp[BENCH_LABEL] = year_table(bench_eq, capital)["通期"]
    return cmp


def _dw(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def print_compare(cmp):
    cols = ["Fスコア関門", "監視(平均)", "発生", "約定", "勝率", "平均損益", "保有日", "通期騰落", "最大DD", "シャープ"]
    b = cmp[BENCH_LABEL]
    for label, rows in cmp.items():
        if label == BENCH_LABEL:
            continue
        table = [cols]
        for r in rows:
            table.append([r["variant"], f"{r['watch']:.1f}", str(r["n_raw"]), str(r["n_exec"]),
                          _f(r["win"], ".1f", "%"), _f(r["avg_pnl"], "+.2f", "%"), _f(r["avg_hold"], ".1f"),
                          _f(r["ret"], "+.1f", "%"), _f(r["mdd"], ".1f", "%"), _f(r["sharpe"], ".2f")])
        table.append([BENCH_LABEL, "－", "－", "－", "－", "－", "－",
                      _f(b and b["final_return_pct"], "+.1f", "%"), _f(b and b["max_drawdown_pct"], ".1f", "%"),
                      _f(b and b["sharpe"], ".2f")])
        widths = [max(_dw(row[i]) for row in table) for i in range(len(cols))]
        print(f"\n--- {label}(通期) ---")
        for row in table:
            print("  " + "  ".join(" " * (w - _dw(c)) + c for c, w in zip(row, widths)))


def _cmp_html(cmp):
    head = ("<tr><th>Fスコア関門</th><th>監視銘柄<br><small>四半期平均</small></th><th>シグナル<br>発生</th><th>約定</th>"
            "<th>勝率</th><th>平均損益</th><th>平均保有<br><small>営業日</small></th><th>通期騰落</th><th>最大DD</th><th>シャープ</th></tr>")
    b = cmp[BENCH_LABEL]
    parts = []
    for label, rows in cmp.items():
        if label == BENCH_LABEL:
            continue
        body = "".join(
            f"<tr><td>{html.escape(r['variant'])}</td><td>{r['watch']:.1f}</td><td>{r['n_raw']}</td><td>{r['n_exec']}</td>"
            f"<td>{_f(r['win'], '.1f', '%')}</td><td>{_f(r['avg_pnl'], '+.2f', '%')}</td><td>{_f(r['avg_hold'], '.1f')}</td>"
            f"<td>{_f(r['ret'], '+.1f', '%')}</td><td>{_f(r['mdd'], '.1f', '%')}</td><td>{_f(r['sharpe'], '.2f')}</td></tr>"
            for r in rows)
        body += (f"<tr><td>{BENCH_LABEL}</td><td>－</td><td>－</td><td>－</td><td>－</td><td>－</td><td>－</td>"
                 f"<td>{_f(b and b['final_return_pct'], '+.1f', '%')}</td><td>{_f(b and b['max_drawdown_pct'], '.1f', '%')}</td>"
                 f"<td>{_f(b and b['sharpe'], '.2f')}</td></tr>")
        parts.append(f"<h2>Fスコア閾値の比較: {html.escape(label)}(通期)</h2><div class='wrap'><table><thead>{head}</thead>"
                     f"<tbody>{body}</tbody></table></div>")
    parts.append("<p class='muted'>発生=バックテストが出した全シグナル(勝率・平均損益・平均保有日数はこの全件の取引ごとの値。往復コスト0.2%控除後)。"
                 "約定=資金管理で実際に買えた件数(同一銘柄の重複保有・100株未満・現金不足は見送り)。"
                 "通期騰落・最大DD・シャープは約定分の日次資産(現金含む・時価評価)から。シャープ=日次リターンの平均÷標準偏差×√252(年率換算・無リスク金利0)。</p>")
    return "".join(parts)


def _fmt(m):
    return "<td colspan='3'>－</td>" if m is None else (
        f"<td>{m['final_return_pct']:+.1f}%</td><td>{m['max_drawdown_pct']:.1f}%</td><td>{m['sharpe']:.2f}</td>")


def render(capital, dates, spans, stage_by_date, results, bench_eq, bench_days, today, n_universe, cmp):
    import report_html
    bench_y = year_table(bench_eq, capital)
    parts = []
    for vname, res in results.items():
        head = ("<th>シグナル</th>" + "".join(f"<th colspan='3'>{html.escape(k)}</th>" for k in bench_y)
                + "</tr><tr><th></th>" + "<th>騰落</th><th>最大DD</th><th>シャープ</th>" * len(bench_y))
        rows = []
        for col, label in SIGNALS:
            y = year_table(res["sims"][col]["equity"], capital)
            rows.append(f"<tr><td>{html.escape(label)}<br><small>取引{res['sims'][col]['n_trades']}件</small></td>"
                        + "".join(_fmt(y.get(k)) for k in bench_y) + "</tr>")
        rows.append("<tr><td>1306買い持ち</td>" + "".join(_fmt(bench_y[k]) for k in bench_y) + "</tr>")
        parts.append(f"<h2>年別成績: {html.escape(vname)}</h2><div class='wrap'><table><thead><tr>{head}</tr></thead>"
                     f"<tbody>{''.join(rows)}</tbody></table></div>")
    gate = f_label(6) if f_label(6) in results else list(results)[-1]
    curves = ""
    for col, label in SIGNALS[:2]:
        series = {f"{label} {NO_GATE}": results[NO_GATE]["sims"][col]["equity"],
                  f"{label} {gate}": results[gate]["sims"][col]["equity"], "1306買い持ち": bench_eq}
        curves += f"<h2>資産推移: {label}</h2>" + report_html._svg_equity(series, capital)

    names = list(results.keys())
    short = ["なし" if n == NO_GATE else n.replace("Fスコア", "").replace("点以上", "") for n in names]
    q_rows = []
    for t0, t1 in spans:
        st = stage_by_date[t0]
        cells = "".join(f"<td>{st.get(k, 0)}</td>" for k in ["対象", "流動性通過", "トレンド通過", "Fスコア判定不能"])
        rets = []
        for res in results.values():
            r = span_return(res["sims"]["Ace_Start"]["equity"], t0, t1, capital)
            rets.append("－" if r is None else f"{r:+.1f}%")
        b = span_return(bench_eq, t0, t1, capital)
        sz = "/".join(str(res["sizes"].get(t0, 0)) for res in results.values())
        q_rows.append(f"<tr><td>{t0:%Y-%m-%d}</td>{cells}<td>{sz}</td><td>{' / '.join(rets)}</td>"
                      f"<td>{'－' if b is None else format(b, '+.1f') + '%'}</td></tr>")
    sl = "/".join(short)
    quarter = (f"<h2>四半期ごとの選定とAceの成績</h2><div class='wrap'><table><thead><tr><th>選定日</th><th>対象</th><th>流動性</th>"
               f"<th>トレンド</th><th>F判定不能</th><th>監視銘柄<br><small>({sl})</small></th>"
               f"<th>Ace騰落<br><small>({sl})</small></th><th>1306</th></tr></thead><tbody>"
               + "".join(q_rows) + "</tbody></table></div>")
    notes = f"""<h2>この検証の前提と限界</h2><ul>
<li>対象は株価キャッシュにある{n_universe}銘柄。サンプルが小さく、閾値ごとの取引数は数十件規模のため、差は偶然の影響を受けやすい。</li>
<li>各選定日の前日までのデータだけで、流動性・週足トレンド・Fスコアの順に選定(build_watchlist.pyと同じ関数・基準)。赤字の銘柄は点数に関係なく除外。金融・不動産5業種は対象外。Fスコアの関門は build_watchlist.classify_f_score と同じ整数比較(score×9 ≧ 閾値×分母。分母が9未満なら達成率)。</li>
<li>「関門なし」はFスコアが判定不能(決算データ不足)の銘柄も含む。「N点以上」は判定不能の銘柄を除く。</li>
<li>FスコアはEDINET DBの財務で本番と同じ9項目(取れない項目はその項目だけ除外)。有利子負債が取れない銘柄のDEレシオは負債合計/純資産。提出日が選定日より前の本決算だけを使用。</li>
<li>取引コスト: 往復0.2%を1取引ごとに損益から差し引く(売買手数料とスリッページを分けず一括)。約定はシグナル翌営業日の始値、損切り/利確/60営業日は終値で判定して終値で決済(価格の滑りは別途見ていない)。</li>
<li>残るバイアス: 銘柄一覧と市場区分は「今」の上場銘柄。上場廃止銘柄が入らないため成績は良めに出る(生き残りバイアス)。</li>
<li>RSRは選んだ監視銘柄の中での順位(本番と同じ)。サインは選定日以降の期間内のものだけ。保有中の銘柄は、次の選定で外れても本来の出口まで持つ。</li>
<li>各年は直前日の資産を基準にした年内の値。取引がない期間は資金のまま(1306は常に全額投資)。</li></ul>"""
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>フジコ ウォークフォワード検証</title>
<style>
:root{{--bg:#fff;--fg:#1f2937;--muted:#6b7280;--line:#e5e7eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111827;--fg:#e5e7eb;--muted:#9ca3af;--line:#374151}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Hiragino Sans","Yu Gothic",sans-serif;margin:0;padding:16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:1.4rem}}h2{{font-size:1.1rem;margin-top:2rem}}
table{{border-collapse:collapse;width:100%;font-size:.85rem}}th,td{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:right;vertical-align:top}}
th:first-child,td:first-child{{text-align:left}}.muted,small{{color:var(--muted)}}
.legend{{font-size:.85rem;margin-top:6px}}.wrap{{overflow-x:auto}}
</style></head><body>
<h1>フジコ ウォークフォワード検証(Fスコア閾値の比較)</h1>
<p class="muted">出口ルール: {EXIT_LABEL}</p>
<p class="muted">更新: {today:%Y/%m/%d} / 資金 {capital:,.0f}円 / 選定は四半期ごと({dates[0]:%Y-%m-%d}〜) / 1回の損失=資金の1%・1銘柄20%まで・100株単位</p>
{_cmp_html(cmp)}
{curves}
{''.join(parts)}
{quarter}
{notes}
</body></html>
"""


OOS_PERIODS = [("全体(2022-01〜)", 0, 9999), ("選ぶ期間(2022-01〜2024-12)", *OOS_SELECT), ("試す期間(2025-01〜)", *OOS_TEST)]
OOS_TEST_KEY = OOS_PERIODS[2][0]


def oos_table(oos_results, bench_eq, n225_eq, capital):
    """{シグナル名: [行...]}。行={name, desc, m: {期間名: metrics|None}, cand: True/False/None}。
    cand: 試す期間で baseline と比べた採用候補の判定(baseline・買い持ち・対象外の行は None)"""
    import sizing
    def metrics(eq):
        if eq is None or len(eq) < 2:
            return {k: None for k, *_ in OOS_PERIODS}
        return {k: sizing.period_metrics(eq, y0, y1) for k, y0, y1 in OOS_PERIODS}
    bench_m, n225_m = metrics(bench_eq), metrics(n225_eq)
    table = {}
    for col, label in SIGNALS[:2]:   # Ace・King
        rows = [{"name": "1306買い持ち", "desc": "", "m": bench_m, "cand": None},
                {"name": "日経平均(^N225)買い持ち", "desc": "", "m": n225_m, "cand": None}]
        base = None
        for name, info in oos_results.items():
            if col not in info["cols"]:
                continue
            m = metrics(info["res"]["sims"][col]["equity"])
            if name == "baseline":
                base, cand = m, None
            else:
                cand = judge_candidate(base[OOS_TEST_KEY], m[OOS_TEST_KEY])
            rows.append({"name": name, "desc": info["desc"], "m": m, "cand": cand,
                         "n": info["res"]["sims"][col]["n_trades"]})
        table[label] = rows
    return table


def print_oos(table):
    for label, rows in table.items():
        print(f"\n--- {label}: 騰落率 / 最大DD / シャープ ---")
        for r in rows:
            cells = []
            for k, *_ in OOS_PERIODS:
                m = r["m"][k]
                cells.append(f"{k.split('(')[0]}:" + ("－" if m is None else
                             f"{m['final_return_pct']:+.1f}% {m['max_drawdown_pct']:.1f}% {m['sharpe']:.2f}"))
            mark = "" if r["cand"] is None else (" ★候補" if r["cand"] else " ×")
            print(f"  {r['name']:<14} " + " | ".join(cells) + mark)


def render_oos(table, capital, dates, last_day, today, n_universe):
    head1 = "<th rowspan='2'>案</th>" + "".join(f"<th colspan='3'>{html.escape(k)}</th>" for k, *_ in OOS_PERIODS) + "<th rowspan='2'>試す期間の判定</th>"
    head2 = "<tr>" + "<th>騰落</th><th>最大DD</th><th>シャープ</th>" * len(OOS_PERIODS) + "</tr>"
    parts = []
    for label, rows in table.items():
        body = []
        for r in rows:
            cand = "－" if r["cand"] is None else ("候補条件を満たす" if r["cand"] else "満たさない")
            desc = f"<br><small>{html.escape(r['desc'])}</small>" if r["desc"] else ""
            n = f"<br><small>約定{r['n']}件</small>" if r.get("n") is not None else ""
            body.append(f"<tr><td>{html.escape(r['name'])}{desc}{n}</td>"
                        + "".join(_fmt(r["m"][k]) for k, *_ in OOS_PERIODS) + f"<td>{cand}</td></tr>")
        parts.append(f"<h2>{html.escape(label)}</h2><div class='wrap'><table><thead><tr>{head1}</tr>{head2}</thead>"
                     f"<tbody>{''.join(body)}</tbody></table></div>")
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>フジコ ウォークフォワード: 期間分割と改善案の試験</title>
<style>
:root{{--bg:#fff;--fg:#1f2937;--muted:#6b7280;--line:#e5e7eb;--warn:#fef3c7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111827;--fg:#e5e7eb;--muted:#9ca3af;--line:#374151;--warn:#422006}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Hiragino Sans","Yu Gothic",sans-serif;margin:0;padding:16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:1.4rem}}h2{{font-size:1.1rem;margin-top:2rem}}
table{{border-collapse:collapse;width:100%;font-size:.85rem}}th,td{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:right;vertical-align:top}}
th:first-child,td:first-child{{text-align:left}}.muted,small{{color:var(--muted)}}
.warn{{background:var(--warn);padding:8px 12px;border-radius:6px}}.wrap{{overflow-x:auto}}
</style></head><body>
<h1>フジコ ウォークフォワード: 日経平均との比較と期間分割(改善案の試験)</h1>
<div class="warn"><ul>
<li>銘柄一覧は今の上場銘柄のため生き残りバイアスがあり、成績は良めに出る。</li>
<li>採用の判断は人間が行う(試す期間で、騰落率とシャープが baseline 以上かつ最大DDが悪化しない案だけを候補にする)。</li>
</ul></div>
<p class="muted">更新: {today:%Y/%m/%d} / 資金 {capital:,.0f}円 / 選定は四半期ごと({dates[0]:%Y-%m-%d}〜{last_day:%Y-%m-%d}) / 出口ルール(baseline): {EXIT_LABEL}</p>
<p class="muted">選ぶ期間=2022-01〜2024-12、試す期間=2025-01〜データの最後。各期間は直前日の資産を基準にした値(試す期間は2024年末の資産が基準)。
案は1つずつ変えて baseline と比べ、組み合わせは試していない。日経平均は比較表示のみで、シグナル判定には使わない。</p>
{''.join(parts)}
<h2>案の内容</h2><ul>
<li>A 地合いフィルタ: シグナル日に1306の終値が{REGIME_MA_DAYS}日線より下なら、Ace・Kingのシグナルを見送る(その日までのデータだけを使用)。</li>
<li>B: Kingに{f_label(OOS_F_POINTS)}の関門(既存の関門機能)。Aceには付けない。</li>
<li>C1: 損切り2×ATR・利確6×ATR。 C2: 損切り3×ATR・利確6×ATR(最大保有は baseline と同じ)。</li>
<li>対象は株価キャッシュにある{n_universe}銘柄。取引コスト往復0.2%控除後。1%リスク・20%上限・100株単位の資金管理後の日次資産から算出。シャープ=日次リターンの平均÷標準偏差×√252(無リスク金利0)。</li>
<li>期間を分けても、同じ銘柄一覧・同じ期間のデータを見て案を決めているため、試す期間は完全な未見データではない。件数が少なく差は偶然の影響を受けやすい。</li></ul>
</body></html>
"""


def load_tickers_file(path):
    """--tickers-file を読む。戻り値: (ティッカー, {ティッカー: 銘柄名})。読めない行は警告して無視、1銘柄も無ければ終了"""
    full = path if os.path.isabs(path) else (path if os.path.exists(path) else os.path.join(HERE, path))
    try:
        with open(full, encoding="utf-8-sig") as f:
            tickers, names, bad = parse_tickers_file(f.read())
    except OSError as e:
        sys.exit(f"--tickers-file を読めません: {e}")
    if bad:
        print(f"⚠️ --tickers-file: コードとして読めない行を{len(bad)}件無視: " + " | ".join(bad[:10]))
    if not tickers:
        sys.exit("--tickers-file に有効なコードが1つもありません")
    return tickers, names


def parse_end_date(text):
    """--end の値(YYYY-MM-DD)を検査してそのまま返す。形式が違う・存在しない日付ならエラー"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(text)):
        raise argparse.ArgumentTypeError(f"--end は YYYY-MM-DD 形式で指定してください: {text}")
    try:
        pd.Timestamp(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--end の日付が正しくありません: {text}")
    return text


def positive_float(text):
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"数値で指定してください: {text}")
    if not v > 0:
        raise argparse.ArgumentTypeError(f"0より大きい値を指定してください: {text}")
    return v


def positive_int(text):
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"整数で指定してください: {text}")
    if v <= 0:
        raise argparse.ArgumentTypeError(f"1以上の整数を指定してください: {text}")
    return v


def build_parser():
    ap = argparse.ArgumentParser(description="ウォークフォワード検証(四半期ごとに監視銘柄を選び直す)")
    ap.add_argument("--first", default="2022-01-01", help="最初の選定日(この日以降の最初の営業日)")
    ap.add_argument("--limit", type=int, default=0, help="動作確認用: 銘柄を先頭N件に絞る(--tickers-file があるときは無視)")
    ap.add_argument("--tickers-file", default="",
                    help="1行1コードのテキスト(例 7203 / 130A。コードの後ろに銘柄名を書いてもよい)。これを対象銘柄にする(--limit より優先)。"
                         "銘柄名に銀行・証券・保険・金融・不動産を含む銘柄は除く")
    ap.add_argument("--no-fscore", action="store_true", help="Fスコアを使わない(決算を取得しない)")
    ap.add_argument("--diag", action="store_true", help="確認用: 決算の最古開示日と、四半期ごとのFスコア判定不能数を出す")
    ap.add_argument("--edinetdb-daily-limit", type=int, default=EDB_DAILY_LIMIT,
                    help="EDINET DBを1回の実行で呼ぶ回数の上限(無料枠は1日100回。既定40)。日の残りが20回以下でも止める")
    ap.add_argument("--partial", action="store_true", help="決算が未取得の銘柄があっても、判定不能として続行する")
    ap.add_argument("--refresh", action="store_true", help="株価キャッシュを作り直す")
    ap.add_argument("--f-thresholds", default=F_POINTS_DEFAULT,
                    help="比較するFスコアの閾値(点・カンマ区切り。既定 3,4,5,6,7)。「関門なし」は常に含める。判定は classify_f_score と同じ整数比較")
    ap.add_argument("--cache-only", action="store_true",
                    help="外部通信を一切せず .cache_wf のキャッシュだけで実行(株価・決算に未取得があればそこで止めて報告)。--edinetdb-daily-limit 0 相当")
    ap.add_argument("--end", type=parse_end_date, default=None,
                    help="使う株価キャッシュの日付(YYYY-MM-DD)。.cache_wf/prices_YYYY-MM-DD.pkl を使う。無ければ止める。省略時は今日の翌日")
    ap.add_argument("--stop-mult", type=positive_float, default=2.0, help="損切りのATR倍率(既定 2.0)")
    ap.add_argument("--profit-mult", type=positive_float, default=4.0, help="利確のATR倍率(既定 4.0)")
    ap.add_argument("--max-hold", type=positive_int, default=60, help="最大保有営業日(既定 60)")
    ap.add_argument("--oos", action="store_true",
                    help="日経平均との比較と期間分割(選ぶ期間2022-2024/試す期間2025-)で、改善案A・B・C1・C2をbaselineと比べる。"
                         "出力は既定で docs/walkforward_oos.html(walkforward.html には書かない)。日経平均はyfinanceだけ使う")
    ap.add_argument("--out", default=None, help="出力HTML(既定: docs/walkforward.html、--oos では docs/walkforward_oos.html)")
    return ap


def main():
    run(build_parser().parse_args())


if __name__ == "__main__":
    # リダイレクト時(cp932)に絵文字で落ちないよう、出力できない文字は置き換える
    import sys as _sys
    _sys.stdout.reconfigure(errors="replace")
    _sys.stderr.reconfigure(errors="replace")
    main()
