# -*- coding: utf-8 -*-
"""
REVE近似 + マルチプル拡大 判定(フェーズ1: 計算してログに出すだけ)

TradingViewの pine/fujiko_reve_approx_v2.pine と同じロジックを、
J-Quants(無料版: 過去2年・約12週遅延)の決算サマリーで再現する。
最新四半期がJ-Quantsにまだ無い場合は、yfinanceの四半期決算で1点だけ補う。

スコア: FCF YoY 35% / 売上YoY 25% / 純利益YoY 20% / ROE変化 20%
        成長率25%=1.0点、ROE改善5pt=1.0点、各成分 -1〜2 にクリップ。
        欠損項目は除外して重みを再配分。
判定:   強い買い候補(連続上昇4回以上・スコア2.0以上・マルチプル拡大)
        買い候補    (連続上昇4回以上・スコア1.0以上・マルチプル拡大)
        監視候補    (連続上昇2回以上・スコア1.0以上・マルチプル拡大)
"""
import os
import time
import requests
import pandas as pd
import yfinance as yf

JQ_BASE_URL = "https://api.jquants.com/v2"
COST_OF_CAPITAL = 8.0      # ROEの比較対象(%)
Q_BARS = 63                # 1四半期の営業日数
NEED_STREAK = 4
WATCH_STREAK = 2
WEIGHTS = {"fcf": 0.35, "rev": 0.25, "ni": 0.20, "roe": 0.20}

# J-QuantsのV1/V2で列名が異なるため、候補を順に試す
COLS = {
    "disc_date":  ["DisclosedDate", "DiscDate"],
    "doc_type":   ["TypeOfDocument", "DocType"],
    "per_type":   ["TypeOfCurrentPeriod", "CurPerType"],
    "per_end":    ["CurrentPeriodEndDate", "CurPerEn"],
    "fy_end":     ["CurrentFiscalYearEndDate", "CurFYEn"],
    "sales":      ["NetSales", "Sales"],
    "np":         ["Profit", "NP"],
    "eps":        ["EarningsPerShare", "EPS"],
    "bps":        ["BookValuePerShare", "BPS"],
    "equity":     ["Equity", "Eq"],
    "cfo":        ["CashFlowsFromOperatingActivities", "CFO"],
    "cfi":        ["CashFlowsFromInvestingActivities", "CFI"],
}
QUARTER_NUM = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4, "FY": 4}


# ------------------------------------------------------------
# データ取得
# ------------------------------------------------------------
def fetch_jquants_statements(code, api_key):
    """J-Quants決算サマリーを取得し、決算短信(実績)だけを期ごとに1行へ整理して返す"""
    code5 = code if len(code) == 5 else code + "0"
    rows, params = [], {"code": code5}
    for _ in range(10):
        r = requests.get(f"{JQ_BASE_URL}/fins/summary", params=params,
                         headers={"x-api-key": api_key}, timeout=30)
        if r.status_code == 429:
            time.sleep(20)
            continue
        r.raise_for_status()
        js = r.json()
        rows += js.get("data") or js.get("statements") or []
        if not js.get("pagination_key"):
            break
        params["pagination_key"] = js["pagination_key"]
    if not rows:
        return pd.DataFrame()

    raw = pd.DataFrame(rows)
    df = pd.DataFrame()
    for key, cands in COLS.items():
        col = next((c for c in cands if c in raw.columns), None)
        df[key] = raw[col] if col else None

    # 決算短信(実績)のみ。業績予想の修正等は除外
    df = df[df["doc_type"].astype(str).str.contains("FinancialStatements", na=False)]
    df = df[df["per_type"].isin(QUARTER_NUM.keys())].copy()
    for c in ["sales", "np", "eps", "bps", "equity", "cfo", "cfi"]:
        df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    for c in ["disc_date", "per_end", "fy_end"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["q"] = df["per_type"].map(QUARTER_NUM)
    # 訂正等で同じ期が複数ある場合は、最後に開示されたものを採用
    df = df.sort_values("disc_date").drop_duplicates(["fy_end", "q"], keep="last")
    return df.sort_values("per_end").reset_index(drop=True)


def _same_period_last_year(df, row):
    m = df[(df["q"] == row["q"]) &
           (abs((df["fy_end"] - (row["fy_end"] - pd.DateOffset(years=1))).dt.days) <= 45)]
    return m.iloc[-1] if len(m) else None


def _yoy(cur, prev):
    if pd.isna(cur) or pd.isna(prev) or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _roe(row):
    if pd.isna(row["np"]) or pd.isna(row["equity"]) or row["equity"] <= 0:
        return None
    return row["np"] * (4 / row["q"]) / row["equity"] * 100  # 年換算(%)


def _clip(x):
    return max(-1.0, min(2.0, x))


def calc_score(rev_yoy, ni_yoy, fcf_yoy, roe_chg):
    parts = {
        "fcf": None if fcf_yoy is None else _clip(fcf_yoy / 0.25),
        "rev": None if rev_yoy is None else _clip(rev_yoy / 0.25),
        "ni":  None if ni_yoy is None else _clip(ni_yoy / 0.25),
        "roe": None if roe_chg is None else _clip(roe_chg / 5.0),
    }
    w = sum(WEIGHTS[k] for k, v in parts.items() if v is not None)
    if w == 0:
        return None
    return sum(WEIGHTS[k] * v for k, v in parts.items() if v is not None) / w


def score_points_from_jquants(df):
    """決算発表ごとのスコア点列(古い順)を返す。前年同期が無い期は計算できないので除外"""
    pts = []
    last_fcf, last_fcf_yoy = None, None
    for _, row in df.iterrows():
        prev = _same_period_last_year(df, row)
        fcf = row["cfo"] + row["cfi"] if not (pd.isna(row["cfo"]) or pd.isna(row["cfi"])) else None
        fcf_prev = None
        if prev is not None and not (pd.isna(prev["cfo"]) or pd.isna(prev["cfi"])):
            fcf_prev = prev["cfo"] + prev["cfi"]
        if fcf is not None:
            # CFは半期・通期しか開示されないことが多い。開示の無い四半期は直前の値を引き継ぐ
            # (引き継がないと、CFの有無だけでスコアが上下し、連続上昇の判定が壊れる)
            last_fcf = fcf
            last_fcf_yoy = _yoy(fcf, fcf_prev) if fcf_prev is not None else None
        if prev is None:
            continue
        roe, roe_prev = _roe(row), _roe(prev)
        p = {
            "per_end": row["per_end"], "disc_date": row["disc_date"], "source": "J-Quants",
            "rev_yoy": _yoy(row["sales"], prev["sales"]),
            "ni_yoy": _yoy(row["np"], prev["np"]),
            "fcf": last_fcf,
            "fcf_yoy": last_fcf_yoy,
            "roe": roe,
            "roe_chg": (roe - roe_prev) if roe is not None and roe_prev is not None else None,
        }
        p["score"] = calc_score(p["rev_yoy"], p["ni_yoy"], p["fcf_yoy"], p["roe_chg"])
        if p["score"] is not None:
            pts.append(p)
    return pts


def latest_point_from_yfinance(ticker, after_date, fcf_yoy_carry=None, roe_chg_carry=None):
    """J-Quants(12週遅延)より新しい四半期があれば、yfinanceの単独四半期から1点作る。
    FCF・ROE変化は四半期単独では揃わないため、直前のJ-Quantsの値を引き継ぐ。"""
    try:
        inc = yf.Ticker(ticker).quarterly_income_stmt
        if inc is None or inc.empty or inc.shape[1] < 5:
            return None
        cols = sorted(inc.columns, reverse=True)
        latest, year_ago = cols[0], cols[4]
        if after_date is not None and pd.Timestamp(latest) <= after_date:
            return None
        def g(name, c):
            return inc.loc[name, c] if name in inc.index else None
        rev_yoy = _yoy(g("Total Revenue", latest), g("Total Revenue", year_ago))
        ni_yoy = _yoy(g("Net Income", latest), g("Net Income", year_ago))
        s = calc_score(rev_yoy, ni_yoy, fcf_yoy_carry, roe_chg_carry)
        if s is None:
            return None
        return {"per_end": pd.Timestamp(latest), "disc_date": None, "source": "yfinance",
                "rev_yoy": rev_yoy, "ni_yoy": ni_yoy, "fcf": None, "fcf_yoy": fcf_yoy_carry,
                "roe": None, "roe_chg": roe_chg_carry, "score": s}
    except Exception as e:
        print(f"⚠️ REVE: yfinance四半期取得失敗({ticker}): {e}")
        return None


def count_streak(points):
    streak = 0
    for i in range(1, len(points)):
        streak = streak + 1 if points[i]["score"] > points[i - 1]["score"] else 0
    return streak


# ------------------------------------------------------------
# マルチプル拡大(PER・PBR)
# ------------------------------------------------------------
def eps_ttm_series(df):
    """開示日ごとのEPS(TTM)とBPSの階段状データ。EPSは累計なので
    TTM = 前期通期EPS + 今期累計EPS - 前年同期累計EPS"""
    out = []
    for _, row in df.iterrows():
        if pd.isna(row["eps"]) or pd.isna(row["disc_date"]):
            continue
        if row["q"] == 4:
            ttm = row["eps"]
        else:
            prev = _same_period_last_year(df, row)
            fy_prev = df[(df["q"] == 4) &
                         (abs((df["fy_end"] - (row["fy_end"] - pd.DateOffset(years=1))).dt.days) <= 45)]
            if prev is None or pd.isna(prev["eps"]) or fy_prev.empty or pd.isna(fy_prev.iloc[-1]["eps"]):
                continue
            ttm = fy_prev.iloc[-1]["eps"] + row["eps"] - prev["eps"]
        out.append({"date": row["disc_date"], "eps_ttm": ttm, "bps": row["bps"]})
    s = pd.DataFrame(out)
    if s.empty:
        return s
    s["bps"] = s["bps"].ffill()  # 四半期でBPSが空欄の会社は直前の値を使う
    return s.set_index("date").sort_index()


def multiple_expansion(close, fund):
    if close is None or len(close) < Q_BARS * 3 or fund.empty:
        return None, None, None, None
    f = fund.reindex(close.index.union(fund.index)).ffill().reindex(close.index)
    per = close.where(f["eps_ttm"] > 0) / f["eps_ttm"]
    pbr = close.where(f["bps"] > 0) / f["bps"]
    per_q = per.rolling(Q_BARS).mean()
    pbr_q = pbr.rolling(Q_BARS).mean()

    def up(x):
        a, b, c = x.iloc[-1], x.iloc[-1 - Q_BARS], x.iloc[-1 - Q_BARS * 2]
        return bool(pd.notna(a) and pd.notna(b) and pd.notna(c) and a > b > c)

    last = lambda x: None if pd.isna(x.iloc[-1]) else float(x.iloc[-1])
    return last(per), last(pbr), up(per_q), up(pbr_q)


# ------------------------------------------------------------
# 1銘柄の判定
# ------------------------------------------------------------
def evaluate_reve(ticker, api_key, close=None):
    code = ticker.replace(".T", "")
    df = fetch_jquants_statements(code, api_key)
    if df.empty:
        return None
    points = score_points_from_jquants(df)
    last = points[-1] if points else {}
    yf_pt = latest_point_from_yfinance(ticker, df["per_end"].max(),
                                       last.get("fcf_yoy"), last.get("roe_chg"))
    if yf_pt:
        points.append(yf_pt)
    if not points:
        return None

    if close is None:
        px = yf.download(ticker, period="2y", auto_adjust=True, progress=False)
        if isinstance(px.columns, pd.MultiIndex):
            px.columns = px.columns.get_level_values(0)
        close = px["Close"] if "Close" in px else None
    per, pbr, per_up, pbr_up = multiple_expansion(close, eps_ttm_series(df))

    cur = points[-1]
    jq_last = next((p for p in reversed(points) if p["source"] == "J-Quants"), cur)
    roe = cur["roe"] if cur["roe"] is not None else jq_last["roe"]
    fcf = cur["fcf"] if cur["fcf"] is not None else jq_last["fcf"]
    fcf_yoy = cur["fcf_yoy"] if cur["fcf_yoy"] is not None else jq_last["fcf_yoy"]

    streak = count_streak(points)
    score = cur["score"]
    per_up = bool(per_up) and cur["ni_yoy"] is not None and cur["ni_yoy"] > 0
    roe_ok = roe is not None and roe > COST_OF_CAPITAL
    fcf_ok = True if fcf is None else (fcf > 0 and (fcf_yoy is None or fcf_yoy > 0))
    me = (per_up or (bool(pbr_up) and roe_ok)) and fcf_ok

    if streak >= NEED_STREAK and score >= 2.0 and me:
        label = "強い買い候補"
    elif streak >= NEED_STREAK and score >= 1.0 and me:
        label = "買い候補"
    elif streak >= WATCH_STREAK and score >= 1.0 and me:
        label = "監視候補"
    else:
        label = None

    return {
        "score": round(score, 2), "streak": streak, "points": len(points),
        # 無料版は過去2年分のみ → 計算できる点が少なく、連続上昇は最大でも(点数-1)回
        "streak_provisional": len(points) - 1 < NEED_STREAK,
        "rev_yoy": cur["rev_yoy"], "ni_yoy": cur["ni_yoy"], "fcf_yoy": fcf_yoy,
        "roe": roe, "roe_chg": cur["roe_chg"],
        "per": per, "pbr": pbr, "per_up": per_up, "pbr_up": bool(pbr_up),
        "roe_ok": roe_ok, "fcf_ok": fcf_ok, "me": me, "label": label,
        "latest_period": cur["per_end"].strftime("%Y-%m-%d"), "latest_source": cur["source"],
        "history": [{"per_end": p["per_end"].strftime("%Y-%m-%d"), "score": round(p["score"], 2),
                     "source": p["source"]} for p in points],
    }


# ------------------------------------------------------------
# フェーズ1: ログ出力だけ
# ------------------------------------------------------------
def run_reve_phase1(tickers, ticker_name_map, close_map=None):
    api_key = os.environ.get("JQUANTS_API_KEY", "")
    if not api_key:
        print("⏭️ REVE: JQUANTS_API_KEY未設定のためスキップ")
        return {}
    print(f"\n📈 REVE近似(フェーズ1・ログのみ) {len(tickers)}銘柄を評価中...")
    results = {}
    for t in tickers:
        try:
            r = evaluate_reve(t, api_key, (close_map or {}).get(t))
        except Exception as e:
            print(f"⚠️ REVE: {t} 評価失敗: {e}")
            continue
        if r:
            results[t] = r
    pct = lambda x: "－" if x is None else f"{x * 100:+.0f}%"
    num = lambda x: "－" if x is None else f"{x:.1f}"
    ok = lambda b: "○" if b else "×"
    print(f"{'銘柄':<16} スコア 連続 売上   純利益 FCF    ROE/変化     PER/拡 PBR/拡 ME 判定")
    for t, r in sorted(results.items(), key=lambda kv: kv[1]["score"], reverse=True):
        name = f"{ticker_name_map.get(t, t)}"[:8]
        streak = f"{r['streak']}{'*' if r['streak_provisional'] else ''}"
        print(f"{name:<8}{t:<8} {r['score']:>5.2f} {streak:>4} {pct(r['rev_yoy']):>6} {pct(r['ni_yoy']):>6} "
              f"{pct(r['fcf_yoy']):>6} {num(r['roe'])}/{num(r['roe_chg'])}pt "
              f"{num(r['per'])}/{ok(r['per_up'])} {num(r['pbr'])}/{ok(r['pbr_up'])} {ok(r['me'])} "
              f"{r['label'] or '－'}  [{r['latest_period']} {r['latest_source']}]")
    print("※連続の*印: 無料版J-Quantsは過去2年分のみのため暫定(履歴の蓄積はフェーズ2で対応)")
    counts = pd.Series([r["label"] for r in results.values() if r["label"]]).value_counts().to_dict()
    print(f"✅ REVE評価完了 {len(results)}/{len(tickers)}銘柄  判定内訳: {counts or 'なし'}")
    return results
