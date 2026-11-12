#!/usr/bin/env python3
"""dump.py が保存した入力を、貼り付けても安全な形に要約する。

使い方: python3 .kiro/kiroku-probe/summarize.py [--sessions]

出力するのは、キー名、値の型、文字列の長さだけ。文字列の中身は出さない。
例外として、次のキーの値はそのまま出す（設計に必要で、コードを含まないため）。
  hook_event_name, tool_name
数値、真偽値、null はそのまま出す（終了コードなどを確認するため）。

--sessions を付けると、~/.kiro（または KIRO_HOME）配下のファイル名と大きさ、
SQLite のテーブル名と列名も出す。行の中身は読まない。
"""
import glob
import json
import os
import sqlite3
import sys

VERBATIM_KEYS = {"hook_event_name", "tool_name"}


def shape(value, key=None):
    """値を「型と長さ」に置き換える。"""
    if isinstance(value, dict):
        return {k: shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        return [shape(value[0], key), "<list len=%d>" % len(value)]
    if isinstance(value, str):
        if key in VERBATIM_KEYS:
            return value
        hint = ""
        stripped = value.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                json.loads(value)
                hint = " json"
            except ValueError:
                pass
        return "<str len=%d lines=%d%s>" % (len(value), value.count("\n") + 1, hint)
    return value  # int, float, bool, None


def summarize_dumps():
    here = os.path.dirname(os.path.abspath(__file__))
    paths = sorted(glob.glob(os.path.join(here, "out", "*.json")))
    print("## フック入力の要約（%d 件）" % len(paths))
    if not paths:
        print("out/ に記録がありません。フックが一度も動いていません。")
        return
    seen = {}
    order = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        payload = record.get("payload")
        tool = payload.get("tool_name") if isinstance(payload, dict) else None
        key = (record.get("label"), tool)
        order.append("%s%s" % (record.get("label"), "(%s)" % tool if tool else ""))
        summary = {
            "stdin_bytes": record.get("stdin_bytes"),
            "stdin_is_json": record.get("stdin_is_json"),
            "payload": shape(payload),
            "env_name_to_length": record.get("env_name_to_length"),
        }
        if not record.get("stdin_is_json"):
            summary["raw_length"] = len(record.get("raw_if_not_json") or "")
        seen.setdefault(key, []).append(summary)

    print("\n### 発火した順番")
    print(" -> ".join(order))

    for (label, tool), items in seen.items():
        title = label + (" / tool_name=%s" % tool if tool else "")
        print("\n### %s（%d 回）" % (title, len(items)))
        # 形が違うものだけ出す（同じ形の繰り返しは省く）。
        printed = []
        for item in items:
            text = json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True)
            if text not in printed:
                printed.append(text)
        for text in printed[:4]:
            print("```json\n%s\n```" % text)
        if len(printed) > 4:
            print("（ほかに形の違うものが %d 件）" % (len(printed) - 4))


def summarize_sessions():
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    print("\n## セッション保存先の構造（%s）" % ("KIRO_HOME" if os.environ.get("KIRO_HOME") else "~/.kiro"))
    if not os.path.isdir(home):
        print("ディレクトリがありません。")
        return
    databases = []
    counts = {}
    for root, dirs, files in os.walk(home):
        depth = root[len(home):].count(os.sep)
        rel = os.path.relpath(root, home)
        if depth >= 4:
            dirs[:] = []
        # UUID やハッシュのような長い名前のディレクトリは件数だけ数える。
        long_dirs = [d for d in dirs if len(d) >= 20]
        if long_dirs:
            print("%s/ : 長い名前のディレクトリ %d 個（1 個だけ中を見ます）" % (rel, len(long_dirs)))
            dirs[:] = [d for d in dirs if len(d) < 20] + long_dirs[:1]
        for name in sorted(files):
            path = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower() or "(拡張子なし)"
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            counts.setdefault((rel, ext), []).append(size)
            try:
                with open(path, "rb") as handle:
                    if handle.read(16).startswith(b"SQLite format 3"):
                        databases.append(path)
            except OSError:
                pass
    for (rel, ext), sizes in sorted(counts.items()):
        shown = rel if len(rel) < 60 else rel[:28] + "..." + rel[-28:]
        print("%s/ *%s : %d 個, 合計 %d バイト" % (shown, ext, len(sizes), sum(sizes)))

    for path in databases[:5]:
        print("\n### SQLite: %s" % os.path.relpath(path, home))
        try:
            con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
            tables = con.execute(
                "select name from sqlite_master where type='table' order by name"
            ).fetchall()
            for (table,) in tables:
                cols = con.execute('pragma table_info("%s")' % table).fetchall()
                count = con.execute('select count(*) from "%s"' % table).fetchone()[0]
                print("- %s（%d 行）: %s" % (
                    table, count, ", ".join("%s %s" % (c[1], c[2]) for c in cols)))
            con.close()
        except sqlite3.Error as exc:
            print("読めませんでした: %s" % exc)


if __name__ == "__main__":
    summarize_dumps()
    if "--sessions" in sys.argv[1:]:
        summarize_sessions()
