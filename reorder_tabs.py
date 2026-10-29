"""日次シートの日付タブ(2026-10-29 形式)を、新しい日付が左になるように並べ替える。
日付以外のタブ(追跡・サマリーなど)は今の相対順のまま左端にまとめ、その右に日付タブを並べる。既定は dry-run、--apply で実際に並べ替える。
"""
import argparse
import os
import re

DATE_TAB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_date_tab(title):
    return bool(DATE_TAB_RE.match(title))


def new_date_tab_index(titles):
    """新しい日付タブを入れる位置。左端にまとめた日付以外のタブの直後(=日付タブの一番左)"""
    return sum(1 for t in titles if not is_date_tab(t))


def planned_order(titles):
    """日付以外のタブを今の相対順のまま左端にまとめ、その右に日付タブを新しい順で並べる"""
    others = [t for t in titles if not is_date_tab(t)]
    dates = sorted((t for t in titles if is_date_tab(t)), reverse=True)
    return others + dates


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
    print("動くタブ数:", sum(1 for a, b in zip(titles, order) if a != b))
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
