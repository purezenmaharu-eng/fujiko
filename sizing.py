"""ポジションサイズ(資金管理)とポートフォリオ・バックテストの純粋な計算。
ネットワーク・シート・LINEには触れない(テストしやすいよう fujiko.py から分離)。"""
import os
import numpy as np
import pandas as pd

DEFAULT_CAPITAL = 1_000_000
RISK_PCT = 0.01          # 1回の売買で失ってよい金額 = 資金の1%
MAX_POSITION_PCT = 0.20  # 1銘柄に使う金額の上限 = 資金の20%
LOT_SIZE = 100           # 日本株の売買単位


def get_capital():
    """FUJIKO_CAPITAL(環境変数/.env/Secrets)を読む。無い・不正・0以下なら100万円"""
    raw = os.environ.get("FUJIKO_CAPITAL", "").strip().replace(",", "")
    try:
        value = float(raw)
        return value if value > 0 else DEFAULT_CAPITAL
    except ValueError:
        return DEFAULT_CAPITAL


def calc_position(entry, stop, capital, lot=LOT_SIZE):
    """株数 = 資金×1% ÷ (エントリー − 損切り)、lot単位に切り捨て、金額は資金の20%まで。
    戻り値: {"shares", "amount", "loss", "skip", "capped", "reason"}
    lot未満なら shares=0, skip=True(見送り)"""
    out = {"shares": 0, "amount": 0.0, "loss": 0.0, "skip": True, "capped": False, "reason": ""}
    try:
        entry, stop, capital = float(entry), float(stop), float(capital)
    except (TypeError, ValueError):
        out["reason"] = "価格不正"
        return out
    risk_per_share = entry - stop
    if not (entry > 0 and capital > 0) or risk_per_share <= 0 or np.isnan(risk_per_share):
        out["reason"] = "損切り幅が不正"
        return out
    raw_shares = capital * RISK_PCT / risk_per_share
    max_shares = capital * MAX_POSITION_PCT / entry
    capped = raw_shares > max_shares
    shares = int(min(raw_shares, max_shares) // lot) * lot
    if shares < lot:
        out["reason"] = "100株未満"
        out["capped"] = capped
        return out
    out.update(shares=shares, amount=shares * entry, loss=shares * risk_per_share,
               skip=False, capped=capped, reason="上限20%で制限" if capped else "")
    return out


def position_text(pos):
    """LINE/シート用の1行表記"""
    if pos["skip"]:
        return f"見送り({pos['reason']})"
    cap = "・上限20%" if pos["capped"] else ""
    return f"{pos['shares']:,}株 / 損切り損失-{pos['loss']:,.0f}円{cap}"


def simulate_portfolio(trades, closes, capital, lot=LOT_SIZE):
    """トレード一覧を1%リスク・20%上限のルールで資金を動かして再現する。
    trades: dict(ticker, entry_date, exit_date, entry_price, stop_price, pnl_pct) のリスト
            (pnl_pct は取引コスト込みの%、entry/exit_date は Timestamp)
    closes: {ticker: 終値Series(日付index)}。保有中の評価損益(時価評価)に使う
    資金は実現損益で増減(複利)。同一銘柄の重複保有・現金不足・100株未満は見送り。
    戻り値: dict(equity: 日次資産Series, final_return_pct, max_drawdown_pct, sharpe,
                 max_concurrent, n_trades, n_skipped)"""
    empty = {"equity": pd.Series(dtype=float), "final_return_pct": 0.0, "max_drawdown_pct": 0.0,
             "sharpe": 0.0, "max_concurrent": 0, "n_trades": 0, "n_skipped": 0}
    if not trades:
        return empty
    trades = sorted(trades, key=lambda t: (t["entry_date"], t["ticker"]))
    start, end = trades[0]["entry_date"], max(t["exit_date"] for t in trades)
    days = sorted({d for s in closes.values() for d in s.index if start <= d <= end})
    if not days:
        return empty

    cash = float(capital)
    open_pos = []        # dict(ticker, shares, entry_price, exit_date, pnl_pct, cost)
    pending = list(trades)
    equity, max_conc, n_taken, n_skipped = [], 0, 0, 0
    for day in days:
        # 1) 当日決済(実現損益を現金へ)
        still = []
        for p in open_pos:
            if p["exit_date"] <= day:
                cash += p["cost"] * (1 + p["pnl_pct"] / 100)
            else:
                still.append(p)
        open_pos = still
        # 2) 当日エントリー(資金は直前の実現資産ベース)
        while pending and pending[0]["entry_date"] <= day:
            t = pending.pop(0)
            if any(p["ticker"] == t["ticker"] for p in open_pos):
                n_skipped += 1
                continue
            realized = cash + sum(p["cost"] for p in open_pos)
            pos = calc_position(t["entry_price"], t["stop_price"], realized, lot)
            shares = pos["shares"]
            if shares and shares * t["entry_price"] > cash:   # 現金不足なら買える分まで
                shares = int(cash // t["entry_price"] // lot) * lot
            if shares < lot:
                n_skipped += 1
                continue
            cost = shares * t["entry_price"]
            cash -= cost
            open_pos.append({"ticker": t["ticker"], "shares": shares, "entry_price": t["entry_price"],
                             "exit_date": t["exit_date"], "pnl_pct": t["pnl_pct"], "cost": cost})
            n_taken += 1
        max_conc = max(max_conc, len(open_pos))
        # 3) 時価評価(終値ベース。終値が無い日は直近値)
        value = cash
        for p in open_pos:
            s = closes[p["ticker"]]
            if not s.index.is_unique or not s.index.is_monotonic_increasing:
                s = s[~s.index.duplicated(keep="last")].sort_index()   # 重複日付で落ちないよう念のため
                closes[p["ticker"]] = s
            c = s.loc[:day].iloc[-1] if day >= s.index[0] else p["entry_price"]
            value += p["cost"] * (float(c) / p["entry_price"])
        equity.append(value)

    eq = pd.Series(equity, index=days)
    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if len(rets) > 1 and rets.std() > 0 else 0.0
    mdd = float(((eq / eq.cummax()) - 1).min() * 100)
    return {"equity": eq, "final_return_pct": float((eq.iloc[-1] / capital - 1) * 100),
            "max_drawdown_pct": mdd, "sharpe": sharpe, "max_concurrent": max_conc,
            "n_trades": n_taken, "n_skipped": n_skipped}


# ---- 根拠(なぜ点灯したか)と出どころ ----
DATA_SOURCE_TEXT = "株価=yfinance、銘柄一覧=J-Quants、割安度=yfinance財務(Evy式/株おじさん式)、ラジ判定=ラジ株ナビ(EDINET)"


def build_signal_reason(label, row):
    """点灯シグナルの『使った条件と数値』を1行で返す。rowは指標列を持つ1日分のSeries"""
    def g(k):
        v = row.get(k)
        return None if v is None or pd.isna(v) else float(v)
    close, ma150, ma200 = g("Close"), g("MA150"), g("MA200")
    high52, low52, rsr = g("High52"), g("Low52"), g("RSR")
    trend = (f"終値{close:,.0f}>150日線{ma150:,.0f}>200日線{ma200:,.0f}(200日線上昇)・"
             f"52週高値比{close / high52 * 100:.0f}%・52週安値の{close / low52:.2f}倍"
             if None not in (close, ma150, ma200, high52, low52) and high52 > 0 and low52 > 0 else "トレンド条件値なし")
    rsr_txt = f"RSR{rsr:.0f}" if rsr is not None else "RSR不明"
    if label == "Ace":
        return f"Ace: {rsr_txt}(80以上)・{trend}"
    if label == "King":
        return f"King: {rsr_txt}(65〜79)・{trend}"
    if label == "ポリグラフ":
        vol, mom = g("VolumeVCP"), g("RSR_Mom")
        extra = (f"出来高20日平均比+{vol * 100:.0f}%(100%超)・RSRモメンタム{mom:+.1f}"
                 if vol is not None and mom is not None else "")
        return f"ポリグラフ: {rsr_txt}(85以上)・{extra}・Ace条件成立"
    if label == "Ace×BEP":
        return f"Ace×BEP: {rsr_txt}(80以上)+強気包み足(前日安値引け→陽線で前日高値超え・出来高増)・{trend}"
    return label


def build_signal_info(label, row, capital, reason_row=None):
    """シグナル1件分: 根拠・出どころ・推奨株数(エントリー=直近終値、損切り=終値-2×ATR14)。
reason_row: 点灯日の行(直近3日以内の点灯日が最終日と違う場合に根拠の数値をその日のものにする)"""
    close, atr = row.get("Close"), row.get("ATR14")
    stop = close - 2.0 * atr if pd.notna(close) and pd.notna(atr) else None
    pos = calc_position(close, stop, capital) if stop is not None else calc_position(None, None, capital)
    return {"label": label, "entry": close, "stop": stop, "pos": pos,
            "reason": build_signal_reason(label, row if reason_row is None else reason_row), "source": DATA_SOURCE_TEXT}


# ---- 比較用: 買い持ち・期間別の指標 ----
def equity_metrics(eq, base):
    """日次資産Series eq と開始時の基準額 base から (最終リターン%, 最大DD%, 年率シャープ)"""
    if eq is None or len(eq) == 0:
        return {"final_return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0}
    full = pd.concat([pd.Series([float(base)]), eq.reset_index(drop=True)], ignore_index=True)
    rets = eq.pct_change()
    rets.iloc[0] = eq.iloc[0] / base - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if len(rets) > 1 and rets.std() > 0 else 0.0
    return {"final_return_pct": float((eq.iloc[-1] / base - 1) * 100),
            "max_drawdown_pct": float(((full / full.cummax()) - 1).min() * 100),
            "sharpe": sharpe}


def buy_and_hold(close, capital, start, end):
    """start〜end の終値で買い持ちした場合の指標(start日の終値で全額買い)"""
    c = close.loc[start:end].dropna()
    # データ異常(例: 1306.Tの2026/3/30-31は他の日の約1/10で配信される)を、前後5日の中央値から
    # 半分未満/2倍超に外れた日として除外する
    med = c.rolling(5, center=True, min_periods=3).median()
    c = c[(c / med > 0.5) & (c / med < 2)]
    if len(c) < 2:
        return equity_metrics(None, capital)
    eq = capital * c / c.iloc[0]
    return equity_metrics(eq.iloc[1:], capital)


def period_metrics(eq, year_from, year_to=None):
    """資産推移を年で区切った指標。区間の直前日の資産を基準にする。取引期間が無ければ None"""
    year_to = year_to or 9999
    sel = eq[(eq.index.year >= year_from) & (eq.index.year <= year_to)]
    if len(sel) < 2:
        return None
    prev = eq[eq.index < sel.index[0]]
    base = float(prev.iloc[-1]) if len(prev) else float(sel.iloc[0])
    return equity_metrics(sel if len(prev) else sel.iloc[1:], base)
