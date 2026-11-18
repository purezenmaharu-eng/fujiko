"""Geminiの単価と円換算レート(universe_scanの費用計算・月額上限用)。単価・レートはこのファイルだけに置く。

単価(gemini-3.1-flash-lite、有料tier・Standard、テキスト入力):
  出典: https://aistudio.google.com/docs/pricing
        (https://ai.google.dev/gemini-api/docs/pricing は上記へ301転送)
  確認日: 2026-11-18
  入力 $0.25 / 100万トークン、出力 $1.50 / 100万トークン。
  思考(thinking)トークンは別単価なし(ページ項目名「Output price (including thinking tokens)」)
  なので出力単価で計算する。

円換算レート:
  【未確認の仮置き】公式の料金表にはレートの記載がない。日銀の為替ページ
  (https://www.boj.or.jp/en/statistics/market/forex/fxdaily/index.htm)でも数値を確認できなかった。
  実測(呼び出し40回・入力88,997・出力40,795トークンで約15円)と照らすと、USD 0.0835 に対し
  約15円なら約180円/USD。上限を超過側に外さないため、仮置きは実測側に寄せて180とする。
  実際のレートを確認したら値と確認日をここに書き換えること。
"""
from __future__ import annotations

INPUT_USD_PER_MTOK = 0.25
OUTPUT_USD_PER_MTOK = 1.50   # thinkingトークン込み
JPY_PER_USD = 180.0          # 未確認の仮置き(上のdocstring参照)


def cost_yen(prompt_tokens: int, candidates_tokens: int, thoughts_tokens: int) -> float:
    """トークン数から費用(円)を返す。出力側は candidates + thoughts(thinkingは出力単価で課金)。"""
    usd = (prompt_tokens * INPUT_USD_PER_MTOK
           + (candidates_tokens + thoughts_tokens) * OUTPUT_USD_PER_MTOK) / 1_000_000
    return usd * JPY_PER_USD
