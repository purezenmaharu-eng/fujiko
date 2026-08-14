//@version=6
// =====================================================================
// フジコ投資法 スイングトレード インジケーター (Fujiko + 黒猫法) v1
// -----------------------------------------------------------------
// 株おじさん(note: kabu_ojisan)氏の「フジコ投資法(ミネさん投資法)」
// および「黒猫投資法」を参考に、既存の fujiko.py(日本株/米国株の
// 自動スクリーニングシステム)のロジックをPine Script用に翻訳・
// 補完したもの。
//
// 【重要な注意・限界】
// ・RSR(相対力ランク)はPine Scriptの仕様上、他銘柄と横断比較する
//   本家IBD式のランキングを再現できないため、ベンチマーク(日経平均/
//   TOPIXなど)に対する相対パフォーマンスを自銘柄の過去レンジ内で
//   パーセンタイル化した「近似値」です。fujiko.py側の本物のRSRとは
//   一致しません。
// ・ポリグラフ(3Cパターン)は計算式が非公開のため未実装です。
// ・BEPは「市場構造(3本足フラクタルのジグザグ)+包み足」による簡易
//   版で、著者のAI学習版とは異なります。
// ・スクイーズモメンタムは公知のLazyBear式(BB2σ/KC1.5σ)を採用して
//   おり、株おじさん氏の具体的な実装とは異なる可能性があります。
// =====================================================================

indicator("フジコ投資法 スイングトレード (Fujiko+黒猫法) v1", shorttitle="Fujiko-Swing", overlay=true, max_labels_count=500, max_lines_count=500)

// ========================= 入力:表示モード =========================
grpMode = "■ 表示モード"
dispMode = input.string("フジコ+黒猫法", title="表示モード", options=["フジコのみ", "黒猫法のみ", "フジコ+黒猫法"], group=grpMode)
comboFilter = input.bool(true, title="黒猫法の買いシグナルをAce/King銘柄のみに絞る(株おじさん式2段構え)", group=grpMode)
showTable = input.bool(true, title="情報テーブルを表示", group=grpMode)

// ========================= 入力:RSR近似 =========================
grpBench = "■ RSR近似(ベンチマーク相対)"
benchSymbol = input.symbol("TVC:NI225", title="ベンチマーク銘柄(日本株なら日経平均/TOPIX、米国株ならSPXなどに変更)", group=grpBench)
rsrLookback = input.int(252, title="RSR近似の正規化期間(営業日)", minval=50, group=grpBench)

// ========================= 入力:フジコ SEPA判定 =========================
grpSepa = "■ フジコ:SEPA判定(トレンドテンプレート)"
maFast = input.int(50, title="短期MA", group=grpSepa)
maMid  = input.int(150, title="中期MA", group=grpSepa)
maSlow = input.int(200, title="長期MA", group=grpSepa)
lowMult = input.float(1.4, title="52週安値からの上昇率しきい値(倍)", step=0.05, group=grpSepa)
highMult = input.float(0.85, title="52週高値からの許容乖離(倍)", step=0.05, group=grpSepa)
rsrAceTh = input.float(80, title="RSR近似:エースしきい値", group=grpSepa)
rsrKingTh = input.float(65, title="RSR近似:キングしきい値", group=grpSepa)
showSepaMAs = input.bool(false, title="SEPA用MA(50/150/200)をチャートに描画", group=grpSepa)

// ========================= 入力:出来高VCP =========================
grpVcp = "■ 出来高VCP"
vcpShortLen = input.int(3, title="短期期間", group=grpVcp)
vcpLongLen = input.int(15, title="長期期間", group=grpVcp)
vcpBaseLen = input.int(252, title="出来高平均の基準期間", group=grpVcp)

// ========================= 入力:BEP / 市場構造 =========================
grpBep = "■ 市場構造 / BEP"
showBep = input.bool(true, title="BEPマーカーを表示", group=grpBep)
bepNearBars = input.int(3, title="スイング安値からの許容バー数", group=grpBep)

// ========================= 入力:黒猫法 =========================
grpKuro = "■ 黒猫法"
kFastLen = input.int(8, title="短期WMA", group=grpKuro)
kSlowLen = input.int(15, title="中期SMA", group=grpKuro)
kTrendLen = input.int(200, title="長期SMA(全体トレンド確認)", group=grpKuro)
sqBbLen = input.int(20, title="スクイーズ:BB期間", group=grpKuro)
sqBbMult = input.float(2.0, title="スクイーズ:BB乗数", step=0.1, group=grpKuro)
sqKcLen = input.int(20, title="スクイーズ:KC期間", group=grpKuro)
sqKcMult = input.float(1.5, title="スクイーズ:KC乗数", step=0.1, group=grpKuro)
requireWeeklyOk = input.bool(true, title="週足での確認も必須にする(その3の教訓)", group=grpKuro)

// ========================= 入力:ATRバンド =========================
grpBand = "■ ATRバンド(利確/損切りの目安)"
showBand = input.bool(true, title="ATRバンドを表示", group=grpBand)
atrLen = input.int(14, title="ATR期間", group=grpBand)
atrStopMult = input.float(2.0, title="損切り:ATR倍率", step=0.5, group=grpBand)
atrTargetMult = input.float(4.0, title="利確:ATR倍率", step=0.5, group=grpBand)
bandBasisLen = input.int(15, title="バンド中心線のMA期間", group=grpBand)

// ========================= RSR近似の計算 =========================
// IBD方式(直近四半期を2倍加重)を参考に、自銘柄とベンチマークの
// 相対パフォーマンスを算出し、自身の過去レンジ内でパーセンタイル化する
r3  = close / close[63]  - 1
r6  = close / close[126] - 1
r9  = close / close[189] - 1
r12 = close / close[252] - 1
stockScore = 2 * r3 + r6 + r9 + r12

bClose = request.security(benchSymbol, timeframe.period, close, lookahead=barmerge.lookahead_off)
br3  = bClose / bClose[63]  - 1
br6  = bClose / bClose[126] - 1
br9  = bClose / bClose[189] - 1
br12 = bClose / bClose[252] - 1
benchScore = 2 * br3 + br6 + br9 + br12

relScore = stockScore - benchScore
rsrApprox = ta.percentrank(relScore, rsrLookback)

// ========================= SEPA判定(base7 + RSR近似) =========================
ma50  = ta.sma(close, maFast)
ma150 = ta.sma(close, maMid)
ma200 = ta.sma(close, maSlow)

cond1 = close > ma150 and close > ma200
cond2 = ma150 > ma200
cond3 = ma200 > ma200[20]
cond4 = ma50 > ma50[5]
cond5 = close > ma50
low52  = ta.lowest(low, 252)
high52 = ta.highest(high, 252)
cond6 = close > low52 * lowMult
cond7 = close >= high52 * highMult

base7Count = (cond1 ? 1 : 0) + (cond2 ? 1 : 0) + (cond3 ? 1 : 0) + (cond4 ? 1 : 0) + (cond5 ? 1 : 0) + (cond6 ? 1 : 0) + (cond7 ? 1 : 0)
base7 = base7Count == 7

isAce  = base7 and rsrApprox >= rsrAceTh
isKing = base7 and rsrApprox >= rsrKingTh and rsrApprox < rsrAceTh

// ========================= RSRモメンタム =========================
rsrEma8  = ta.ema(rsrApprox, 8)
rsrSma21 = ta.sma(rsrApprox, 21)
rsrMomentum = rsrEma8 - rsrSma21
rsrMomentumRising = rsrMomentum > rsrMomentum[1]

momState = rsrMomentum > 0 and rsrMomentumRising ? 3 :
           rsrMomentum > 0 and not rsrMomentumRising ? 2 :
           rsrMomentum <= 0 and not rsrMomentumRising ? 0 : 1
momColor = momState == 3 ? color.new(color.fuchsia, 0) :
           momState == 2 ? color.new(color.purple, 0) :
           momState == 0 ? color.new(color.navy, 0) : color.new(color.aqua, 0)
momLabel = momState == 3 ? "買い継続" : momState == 2 ? "手仕舞い検討" : momState == 0 ? "撤退" : "中立"

// ========================= 出来高VCP(短期/長期クロス) =========================
avgVol = ta.sma(volume, vcpBaseLen)
volPct = avgVol > 0 ? volume / avgVol * 100 : 100
signedVolPct = close >= close[1] ? volPct : -volPct
vcpShort = ta.sma(signedVolPct, vcpShortLen)
vcpLong  = ta.sma(signedVolPct, vcpLongLen)
vcpBuy  = ta.crossover(vcpShort, vcpLong) and vcpShort > 0
vcpSell = ta.crossunder(vcpShort, vcpLong) and vcpShort < 0

// ========================= 市場構造 / BEP =========================
swingHigh = ta.pivothigh(high, 1, 1)
swingLow  = ta.pivotlow(low, 1, 1)
bullishEngulf = close[1] < open[1] and close > open and close >= open[1] and open <= close[1]
barsSincePivotLow = ta.barssince(not na(swingLow))
isNearSwingLow = not na(barsSincePivotLow) and barsSincePivotLow <= bepNearBars
bepSignal = bullishEngulf and isNearSwingLow

// ========================= 黒猫法 =========================
wma8   = ta.wma(close, kFastLen)
sma15  = ta.sma(close, kSlowLen)
sma200k = ta.sma(close, kTrendLen)
wma8Rising = wma8 > wma8[1]
sma200Falling = sma200k < sma200k[1]

[wWma8, wClose] = request.security(syminfo.tickerid, "W", [ta.wma(close, kFastLen), close], lookahead=barmerge.lookahead_off)
weeklyOk = wClose > wWma8

kuronekoBuyRaw = ta.crossover(wma8, sma15) and close > wma8 and close > sma15 and wma8Rising and not sma200Falling
kuronekoBuy = requireWeeklyOk ? (kuronekoBuyRaw and weeklyOk) : kuronekoBuyRaw
kuronekoBuyFinal = comboFilter ? (kuronekoBuy and (isAce or isKing)) : kuronekoBuy

wma8SlowingDown = wma8 > wma8[1] and (wma8 - wma8[1]) < (wma8[1] - wma8[2])
kuronekoSell = ta.crossunder(wma8, sma15) or wma8SlowingDown

// スクイーズモメンタム(LazyBear式)
basisBB = ta.sma(close, sqBbLen)
devBB = sqBbMult * ta.stdev(close, sqBbLen)
upperBB = basisBB + devBB
lowerBB = basisBB - devBB

maKC = ta.sma(close, sqKcLen)
rangeKC = ta.sma(ta.tr, sqKcLen)
upperKC = maKC + rangeKC * sqKcMult
lowerKC = maKC - rangeKC * sqKcMult

sqzOn  = lowerBB > lowerKC and upperBB < upperKC
sqzOff = lowerBB < lowerKC and upperBB > upperKC

highestHigh = ta.highest(high, sqKcLen)
lowestLow = ta.lowest(low, sqKcLen)
avgHL = (highestHigh + lowestLow) / 2
smaCloseKC = ta.sma(close, sqKcLen)
avgAll = (avgHL + smaCloseKC) / 2
sqzMomentum = ta.linreg(close - avgAll, sqKcLen, 0)

// ========================= ATRバンド(利確/損切りの目安) =========================
atrVal = ta.atr(atrLen)
bandBasis = ta.sma(close, bandBasisLen)
bandUpper = bandBasis + atrVal * atrTargetMult
bandLower = bandBasis - atrVal * atrStopMult

// ========================= 描画:フジコ =========================
showFujiko = dispMode != "黒猫法のみ"
showKuro = dispMode != "フジコのみ"

plotshape(showFujiko and isAce, title="エース", text="A", style=shape.labelup, location=location.belowbar, color=color.new(color.red, 0), textcolor=color.white, size=size.tiny)
plotshape(showFujiko and isKing, title="キング", text="K", style=shape.labelup, location=location.belowbar, color=color.new(color.orange, 0), textcolor=color.white, size=size.tiny)
plotshape(showFujiko and showBep and bepSignal, title="BEP", text="BEP", style=shape.labelup, location=location.belowbar, color=color.new(color.blue, 0), textcolor=color.white, size=size.tiny)

bgcolor(showFujiko and isAce ? color.new(color.red, 92) : showFujiko and isKing ? color.new(color.orange, 94) : na, title="フジコ判定背景")

plot(showFujiko and showSepaMAs ? ma50 : na, title="SEPA:50MA", color=color.new(color.yellow, 30))
plot(showFujiko and showSepaMAs ? ma150 : na, title="SEPA:150MA", color=color.new(color.teal, 30))
plot(showFujiko and showSepaMAs ? ma200 : na, title="SEPA:200MA", color=color.new(color.maroon, 30))

// ========================= 描画:黒猫法 =========================
plot(showKuro ? wma8 : na, title="黒猫:8WMA", color=color.new(color.orange, 0), linewidth=2)
plot(showKuro ? sma15 : na, title="黒猫:15SMA", color=color.new(color.blue, 0), linewidth=2)
plot(showKuro ? sma200k : na, title="黒猫:200SMA", color=color.new(color.gray, 0), linewidth=1)

plotshape(showKuro and kuronekoBuyFinal, title="黒猫:買い", style=shape.triangleup, location=location.belowbar, color=color.new(color.lime, 0), size=size.small)
plotshape(showKuro and kuronekoSell, title="黒猫:売り", style=shape.triangledown, location=location.abovebar, color=color.new(color.red, 0), size=size.small)

// ========================= 描画:ATRバンド =========================
plot(showBand ? bandUpper : na, title="利確目安(ATR)", color=color.new(color.green, 60))
plot(showBand ? bandLower : na, title="損切り目安(ATR)", color=color.new(color.red, 60))

// ========================= 情報テーブル =========================
var table infoTable = table.new(position.top_right, 2, 9, bgcolor=color.new(color.black, 70), border_width=1, border_color=color.new(color.gray, 30))

if showTable and barstate.islast
    table.cell(infoTable, 0, 0, "項目", text_color=color.white, bgcolor=color.new(color.gray, 40))
    table.cell(infoTable, 1, 0, "値", text_color=color.white, bgcolor=color.new(color.gray, 40))

    table.cell(infoTable, 0, 1, "SEPA(7条件)", text_color=color.white)
    table.cell(infoTable, 1, 1, str.tostring(base7Count) + "/7", text_color=color.white)

    table.cell(infoTable, 0, 2, "RSR近似", text_color=color.white)
    table.cell(infoTable, 1, 2, str.tostring(int(math.round(rsrApprox))), text_color=color.white)

    table.cell(infoTable, 0, 3, "フジコ判定", text_color=color.white)
    table.cell(infoTable, 1, 3, isAce ? "Aエース" : isKing ? "Kキング" : "-", text_color=isAce ? color.red : isKing ? color.orange : color.gray)

    table.cell(infoTable, 0, 4, "RSRモメンタム", text_color=color.white)
    table.cell(infoTable, 1, 4, momLabel, text_color=momColor)

    table.cell(infoTable, 0, 5, "出来高VCP(短/長)", text_color=color.white)
    table.cell(infoTable, 1, 5, str.tostring(int(math.round(vcpShort))) + " / " + str.tostring(int(math.round(vcpLong))), text_color=color.white)

    table.cell(infoTable, 0, 6, "スクイーズ", text_color=color.white)
    table.cell(infoTable, 1, 6, sqzOn ? "収束中" : sqzOff ? "解放" : "-", text_color=sqzOn ? color.yellow : sqzOff ? color.lime : color.gray)

    table.cell(infoTable, 0, 7, "黒猫法", text_color=color.white)
    table.cell(infoTable, 1, 7, kuronekoBuyFinal ? "買い" : kuronekoSell ? "売り" : "-", text_color=kuronekoBuyFinal ? color.lime : kuronekoSell ? color.red : color.gray)

    table.cell(infoTable, 0, 8, "BEP", text_color=color.white)
    table.cell(infoTable, 1, 8, bepSignal ? "検出" : "-", text_color=bepSignal ? color.blue : color.gray)

// ========================= アラート =========================
alertcondition(isAce and not isAce[1], title="フジコ:新規エース", message="{{ticker}} が新たにエース(A)条件を満たしました")
alertcondition(isKing and not isKing[1], title="フジコ:新規キング", message="{{ticker}} が新たにキング(K)条件を満たしました")
alertcondition(bepSignal, title="フジコ:BEP検出", message="{{ticker}} でBEP(強気の包み足)を検出しました")
alertcondition(kuronekoBuyFinal, title="黒猫法:買いシグナル", message="{{ticker}} で黒猫法の買いシグナルが出ました")
alertcondition(kuronekoSell, title="黒猫法:売りシグナル", message="{{ticker}} で黒猫法の売りシグナルが出ました")
alertcondition(vcpBuy, title="出来高VCP:買いクロス", message="{{ticker}} で出来高VCPの買いクロスが発生しました")
alertcondition(vcpSell, title="出来高VCP:売りクロス", message="{{ticker}} で出来高VCPの売りクロスが発生しました")
