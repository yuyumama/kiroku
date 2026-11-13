#!/usr/bin/env python3
"""PreToolUse フックから呼ばれる。取り返しのつかないコマンドを止める。

止めるときは、標準エラーに理由を書いて 2 で終わる。それ以外は何も出さずに 0 で終わる。
"""
import json
import re
import sys

BLOCKED = (
    (re.compile(r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)"), "強制的な push はしません"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+(/|~|\.\.?)(\s|$)"), "広い範囲の削除はしません"),
    (re.compile(r"\brm\s+.*\bdata/kakeibo\.json\b"), "記録のファイルは消しません"),
)


def main() -> int:
    try:
        command = (json.load(sys.stdin).get("tool_input") or {}).get("command")
    except ValueError:
        return 0
    if not isinstance(command, str):
        return 0
    for pattern, reason in BLOCKED:
        if pattern.search(command):
            print("止めました: %s。" % reason, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
