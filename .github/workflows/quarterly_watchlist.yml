name: Fujiko Quarterly Watchlist (株おじさん式 監視銘柄リスト構築)
on:
  schedule:
    # 毎日 UTC5:00 = JST14:00 に実行。
    # ラジ株ナビの日次クォータ(150件/日)制約により、Fスコア判定は複数日に分けて進む設計
    # (build_watchlist.py側で「監視銘柄_作業用」タブの未処理分から日次上限まで処理し、
    # 全件完了した時点で初めて「監視銘柄」タブを更新する)。
    # 四半期開始月(1/4/7/10月)だけに限定せず毎日実行する理由:
    #   ・全候補の処理が完了している日はbuild_watchlist.py側が数秒で終了する軽い実行になるため、
    #     頻度を上げても無駄なコストにならない
    #   ・build_watchlist.py内で四半期ラベル(例:2026-Q3)の変化を自動検知し、四半期が変わったら
    #     自動的に流動性+長期トレンドの再スクリーニングから始まる設計のため、月を限定する必要がない
    #   ・クォータ制約で複数日に分割処理が必要になった場合(今回のように)でも、手動実行を都度
    #     お願いしなくて済む
    - cron: '0 5 * * *'
  workflow_dispatch:
permissions:
  contents: write
jobs:
  build-watchlist:
    runs-on: ubuntu-latest
    # 全銘柄の週足取得+Fスコア判定を伴うため、日次実行より時間がかかる想定
    timeout-minutes: 240
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install yfinance pandas numpy requests jquants-api-client gspread google-auth
      - name: Get JST date for cache key
        id: jstdate
        run: |
          echo "date=$(TZ=Asia/Tokyo date +%Y-%m-%d)" >> "$GITHUB_OUTPUT"
      - name: Restore radikabunavi cache (同日の再実行でAPI呼び出しを再利用)
        uses: actions/cache@v4
        with:
          path: .cache
          key: radikabunavi-cache-${{ steps.jstdate.outputs.date }}-${{ github.run_id }}
          restore-keys: |
            radikabunavi-cache-${{ steps.jstdate.outputs.date }}-
      - name: "Build watchlist (株おじさん式: 流動性 → 長期トレンド → Fスコア)"
        env:
          JQUANTS_API_KEY: ${{ secrets.JQUANTS_API_KEY }}
          RADIKABUNAVI_API_KEY: ${{ secrets.RADIKABUNAVI_API_KEY }}
          GOOGLE_SHEETS_CREDENTIALS: ${{ secrets.GOOGLE_SHEETS_CREDENTIALS }}
          SPREADSHEET_ID: ${{ secrets.SPREADSHEET_ID }}
        run: |
          python build_watchlist.py
