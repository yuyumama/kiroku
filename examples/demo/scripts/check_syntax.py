#!/usr/bin/env python3
"""PostFileSave フックから呼ばれる。保存された Python ファイルの構文を確かめる。

問題が無ければ何も出さずに終わる。構文エラーがあれば、標準エラーに場所を書いて 1 で終わる。
"""
import json
import sys


def main() -> int:
    try:
        path = json.load(sys.stdin).get("file_path")
    except ValueError:
        return 0
    if not isinstance(path, str) or not path.endswith(".py"):
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            compile(handle.read(), path, "exec")
    except OSError:
        return 0
    except SyntaxError as error:
        print("構文エラー: %s の %s 行目: %s" % (path, error.lineno, error.msg), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
