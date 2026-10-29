"""日次シートの日付タブ(2026-10-29 形式)を、新しい日付が左になるように並べ替える。
日付以外のタブ(追跡・サマリーなど)は今の位置のまま動かさない。既定は dry-run、--apply で実際に並べ替える。
"""
import argparse
import os
import re

DATE_TAB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_date_tab(title):
    return bool(DATE_TAB_RE.match(title))


def new_date_tab_index(titles):
    """新しい日付タブを入れる位置。最初の日付タブの位置(=左端に並ぶ日付以外のタブの右隣)。日付タブが無ければ末尾"""
    for i, t in enumerate(titles):
        if is_date_tab(t):
            return i
    return len(titles)


def planned_order(titles):
    """日付タブの占める位置はそのままに、そこへ新しい日付順で入れ直した並びを返す"""
    dates = sorted((t for t in titles if is_date_tab(t)), reverse=True)
    it = iter(dates)
    return [next(it) if is_date_tab(t) else t for t in titles]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="実際に並べ替える(省略時は並び替え後の順序を表示するだけ)")
    args = ap.parse_args(argv)

    import gspread
    from fujiko import _load_sheets_credentials
    creds = _load_sheets_credentials(os.environ["GOOGLE_SHEETS_CREDENTIALS"],
                                     scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sh = gspread.authorize(creds).open_by_key(os.environ["SPREADSHEET_ID"])
    worksheets = sh.worksheets()
    titles = [w.title for w in worksheets]
    order = planned_order(titles)
    print("現在:", titles)
    print("並べ替え後:", order)
    if order == titles:
        print("すでに新しい日付が左です。変更なし")
        return
    if not args.apply:
        print("dry-run: --apply を付けると実際に並べ替えます")
        return
    by_title = {w.title: w for w in worksheets}
    sh.reorder_worksheets([by_title[t] for t in order])
    print("✅ 並べ替え完了")


if __name__ == "__main__":
    main()
