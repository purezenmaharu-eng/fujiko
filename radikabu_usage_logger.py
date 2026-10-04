"""ラジ株ナビAPI使用量の追跡用ログ。

dexter-kabu-jp・investor-agent・fujikoはRADIKABUNAVI_API_KEYを共用しており
(日次150回上限)、どのプロジェクトが・いつ・何を呼んだかを後から追えるように、
呼び出しごとに1行のログを出す。既存のツール呼び出し動作には影響させない
(ログ処理自体が失敗しても例外を投げない)。fujiko.py・build_watchlist.pyの
両方から共用する。

出力先:
  - ローカル実行: C:\\Users\\admin\\hub\\logs\\radikabu-usage.log に追記
    (環境変数 RADIKABU_USAGE_LOG が設定されていればそのパスを優先)
  - GitHub Actions / Cloud Run: 標準出力に `[RADIKABU]` から始まる1行
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

PROJECT_NAME = "fujiko"
LOCAL_LOG_PATH = os.environ.get("RADIKABU_USAGE_LOG") or r"C:\Users\admin\hub\logs\radikabu-usage.log"
_JST = timezone(timedelta(hours=9))


def _detect_env() -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "GitHub Actions"
    # Cloud Runはコンテナに常にK_SERVICEを設定する(公式ドキュメント記載の検出方法)。
    if os.environ.get("K_SERVICE"):
        return "Cloud Run"
    return "local"


def _extract_code(arguments: dict | None) -> str:
    if not arguments:
        return ""
    for key in ("code", "ticker", "symbol"):
        if key in arguments:
            return str(arguments[key])
    if isinstance(arguments.get("conditions"), list):
        return "(screening)"
    return ""


def log_radikabu_usage(tool_name: str, arguments: dict | None = None) -> None:
    """呼び出しごとに1行、日時(JST)・プロジェクト名・エンドポイント名・銘柄コード・
    実行環境を出力する。ログ失敗で呼び出し元の処理を止めないよう、例外は握り潰す。
    """
    try:
        env = _detect_env()
        now_jst = datetime.now(_JST).isoformat()
        line = (
            f"[RADIKABU] time={now_jst} project={PROJECT_NAME} env={env} "
            f"endpoint={tool_name} code={_extract_code(arguments)}"
        )
        if env == "local":
            with open(LOCAL_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        else:
            print(line)
    except Exception:
        pass
