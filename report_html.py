"""docs/backtest.html の生成(資金管理バックテスト結果と、直近シグナルの推奨株数・根拠)。
外部ライブラリ不要。資産推移は自前のSVG折れ線で描く。"""
import html
import os

COLORS = ["#2563eb", "#16a34a", "#dc2626", "#9333ea"]


def _svg_equity(series_by_label, capital, width=760, height=300):
    """series_by_label: {ラベル: 日次資産pd.Series}。全系列を同じ軸で重ねる"""
    series_by_label = {k: v for k, v in series_by_label.items() if len(v) > 1}
    if not series_by_label:
        return "<p class='muted'>取引がなく、資産推移を描けません。</p>"
    pad_l, pad_r, pad_t, pad_b = 70, 10, 10, 28
    all_dates = sorted({d for s in series_by_label.values() for d in s.index})
    d0, d1 = all_dates[0], all_dates[-1]
    span = max((d1 - d0).days, 1)
    vmin = min(min(s.min() for s in series_by_label.values()), capital)
    vmax = max(max(s.max() for s in series_by_label.values()), capital)
    if vmax == vmin:
        vmax = vmin + 1

    def x(d):
        return pad_l + (d - d0).days / span * (width - pad_l - pad_r)

    def y(v):
        return pad_t + (vmax - v) / (vmax - vmin) * (height - pad_t - pad_b)

    parts = [f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='資産推移' style='width:100%;height:auto'>"]
    for i in range(5):
        v = vmin + (vmax - vmin) * i / 4
        parts.append(f"<line x1='{pad_l}' x2='{width - pad_r}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='currentColor' opacity='.12'/>")
        parts.append(f"<text x='{pad_l - 6}' y='{y(v) + 4:.1f}' text-anchor='end' font-size='11' fill='currentColor'>{v:,.0f}</text>")
    parts.append(f"<line x1='{pad_l}' x2='{width - pad_r}' y1='{y(capital):.1f}' y2='{y(capital):.1f}' stroke='currentColor' opacity='.45' stroke-dasharray='4 3'/>")
    parts.append(f"<text x='{pad_l}' y='{height - 8}' font-size='11' fill='currentColor'>{d0:%Y-%m-%d}</text>")
    parts.append(f"<text x='{width - pad_r}' y='{height - 8}' text-anchor='end' font-size='11' fill='currentColor'>{d1:%Y-%m-%d}</text>")
    for (label, s), color in zip(series_by_label.items(), COLORS):
        pts = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in s.items())
        parts.append(f"<polyline fill='none' stroke='{color}' stroke-width='1.8' points='{pts}'/>")
    parts.append("</svg>")
    legend = "".join(
        f"<span style='margin-right:14px'><span style='display:inline-block;width:10px;height:10px;background:{c};margin-right:4px'></span>{html.escape(l)}</span>"
        for (l, _), c in zip(series_by_label.items(), COLORS))
    return "".join(parts) + f"<div class='legend'>{legend}</div>"


def render(today, capital, signal_labels, portfolio_results, rankings, signal_infos, name_map,
           bench_result=None, bench_label='1306.T', yearly_results=None):
    rows = []
    for col, label in signal_labels.items():
        r = portfolio_results[col]
        rk = rankings.get(col)
        rows.append(
            f"<tr><td>{html.escape(label)}</td><td>{r['n_trades']}</td><td>{r['n_skipped']}</td>"
            f"<td>{r['final_return_pct']:+.2f}%</td><td>{r['max_drawdown_pct']:.2f}%</td>"
            f"<td>{r['sharpe']:.2f}</td><td>{r['max_concurrent']}</td></tr>")
    if bench_result:
        a, b = bench_result["period"]
        rows.append(
            f"<tr><td>{html.escape(bench_label)}買い持ち(TOPIX連動ETF)<br><small>{a:%Y-%m-%d}〜{b:%Y-%m-%d}</small></td>"
            f"<td>－</td><td>－</td><td>{bench_result['final_return_pct']:+.2f}%</td>"
            f"<td>{bench_result['max_drawdown_pct']:.2f}%</td><td>{bench_result['sharpe']:.2f}</td><td>1</td></tr>")
    yearly_html = ""
    if yearly_results:
        yr = []
        for col, buckets in yearly_results.items():
            for name, m in buckets.items():
                cells = ("<td colspan='2'>取引なし</td>" if m is None else
                         f"<td>{m['final_return_pct']:+.2f}%</td><td>{m['sharpe']:.2f}</td>")
                yr.append(f"<tr><td>{html.escape(signal_labels[col])}</td><td>{html.escape(name)}</td>{cells}</tr>")
        yearly_html = ("<h2>年ごとの成績(Ace・King)</h2><div class='wrap'><table><thead><tr><th>シグナル</th><th>期間</th>"
                       "<th>最終リターン</th><th>シャープレシオ(年率)</th></tr></thead><tbody>" + "".join(yr) +
                       "</tbody></table></div><p class='muted'>各年の初日直前の資産を基準にした年内の値。</p>")
    curve = _svg_equity({signal_labels[c]: portfolio_results[c]["equity"] for c in signal_labels}, capital)

    sig_rows = []
    for t, infos in sorted(signal_infos.items()):
        for i in infos:
            pos = i["pos"]
            shares = f"見送り({pos['reason']})" if pos["skip"] else f"{pos['shares']:,}株"
            loss = "－" if pos["skip"] else f"-{pos['loss']:,.0f}円"
            sig_rows.append(
                f"<tr><td>{html.escape(str(name_map.get(t, t)))}<br><small>{html.escape(t)}</small></td>"
                f"<td>{html.escape(i['label'])}</td><td>{shares}</td><td>{loss}</td>"
                f"<td class='reason'>{html.escape(i['reason'])}</td></tr>")
    sig_table = ("<table><thead><tr><th>銘柄</th><th>シグナル</th><th>推奨株数</th><th>損切り損失額</th><th>根拠</th></tr></thead><tbody>"
                 + "".join(sig_rows) + "</tbody></table>") if sig_rows else "<p class='muted'>直近3日以内の点灯なし。</p>"
    source = html.escape(next((i["source"] for v in signal_infos.values() for i in v), ""))

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>フジコ 資金管理バックテスト</title>
<style>
:root{{--bg:#fff;--fg:#1f2937;--muted:#6b7280;--line:#e5e7eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111827;--fg:#e5e7eb;--muted:#9ca3af;--line:#374151}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Hiragino Sans","Yu Gothic",sans-serif;margin:0;padding:16px;max-width:900px;margin-inline:auto}}
h1{{font-size:1.4rem}}h2{{font-size:1.1rem;margin-top:2rem}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right;vertical-align:top}}
th:first-child,td:first-child,td.reason,th:last-child{{text-align:left}}.muted,small{{color:var(--muted)}}
.legend{{font-size:.85rem;margin-top:6px}}.wrap{{overflow-x:auto}}
</style></head><body>
<h1>フジコ 資金管理バックテスト</h1>
<p class="muted">更新: {html.escape(today)} / 資金 {capital:,.0f}円 / 1回の損失=資金の1% / 1銘柄は資金の20%まで / 100株単位 / 損切り=エントリー−2×ATR14</p>
<h2>資産推移</h2>
{curve}
<h2>成績(同じルールで資金を動かした場合)</h2>
<div class="wrap"><table><thead><tr><th>シグナル</th><th>取引数</th><th>見送り</th><th>最終リターン</th><th>最大ドローダウン</th><th>シャープレシオ(年率)</th><th>同時保有最大</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table></div>
{yearly_html}
<p class="muted">資金は実現損益で複利に増減。同一銘柄の重複保有・現金不足・100株未満は見送り。シャープは日次資産の変化率×√252。過去の結果であり将来を保証しません。</p>
<h2>直近3日以内に点灯したシグナルと推奨株数</h2>
<div class="wrap">{sig_table}</div>
<p class="muted">出どころ: {source}</p>
</body></html>
"""


def write_report(path, **kwargs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(**kwargs))
    return path
