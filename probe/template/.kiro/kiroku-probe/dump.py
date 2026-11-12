#!/usr/bin/env python3
"""Kiro フックに渡る入力をそのままファイルへ保存する検証用スクリプト。

使い方（フックから呼ばれる）: python3 .kiro/kiroku-probe/dump.py <トリガー名>

- 標準出力には何も書かない（終了コード 0 の標準出力はエージェントの文脈に入るため）。
- 何が起きても終了コード 0 で終わる（検証が本来の作業を止めないため）。
- 保存先: .kiro/kiroku-probe/out/（生データ。コミットしないこと）
"""
import json
import os
import sys
import time


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    raw = sys.stdin.buffer.read()
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out_dir, exist_ok=True)

    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
        parse_error = None
    except ValueError as exc:
        payload = None
        parse_error = str(exc)

    # 環境変数は値を残さない（API キーが混ざりうる）。名前と長さだけ記録する。
    env = {
        name: len(value)
        for name, value in os.environ.items()
        if "KIRO" in name.upper() or name in ("USER_PROMPT", "CLAUDE_PROJECT_DIR")
    }

    record = {
        "label": label,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "stdin_bytes": len(raw),
        "stdin_is_json": payload is not None,
        "parse_error": parse_error,
        "payload": payload,
        "raw_if_not_json": None if payload is not None else text[:4000],
        "env_name_to_length": env,
    }
    name = "%d-%d-%s.json" % (time.time_ns(), os.getpid(), label)
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # 検証用なので握りつぶす。本来の作業は止めない。
        pass
    sys.exit(0)
