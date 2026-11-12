#!/usr/bin/env python3
"""Kiro CLI（V3）のセッションログから、該当箇所の前後を抜き出す。

使い方:
  python3 .kiro/kiroku/excerpt.py --session <session_id> --command "<コマンドの一部>"
  python3 .kiro/kiroku/excerpt.py --session <session_id> --around 2026-11-12T03:04:05Z

ログの場所は ~/.kiro/sessions/<作業場所ごとの識別子>/<session_id>/messages.jsonl
（KIRO_HOME があればその下）。各行は {"id", "timestamp", "payload": {"type", ...}} の形。

出力は秘匿情報らしい値を伏せ、1項目を短く切る。それでもコードが含まれうるので、
出力をコミットや PR に載せないこと。
"""
import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from record import mask  # noqa: E402

SHOWN_TYPES = ("user", "assistant", "tool_call", "tool_result")


def find_log(session_id):
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    for path in glob.glob(os.path.join(home, "sessions", "*", session_id, "*.jsonl")):
        return path
    return None


def parse_time(value):
    """ISO 形式、または秒・ミリ秒の数値を時刻にする。読めなければ None。"""
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            return datetime.datetime.fromtimestamp(
                number / 1000 if number > 1e11 else number, datetime.timezone.utc)
        moment = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def load(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get("payload") or {}
            if payload.get("type") in SHOWN_TYPES:
                rows.append((row.get("timestamp"), payload))
    return rows


def describe(payload, limit):
    kind = payload.get("type")
    if kind == "tool_call":
        args = payload.get("args") or {}
        text = "%s %s" % (payload.get("toolName"), json.dumps(args, ensure_ascii=False))
    elif kind == "tool_result":
        text = "success=%s %s" % (payload.get("success"), payload.get("content"))
    else:
        text = payload.get("content")
    text = mask(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))
    if len(text) > limit:
        text = text[:limit // 2] + "\n…（中略）…\n" + text[-limit // 2:]
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description="セッションログの該当箇所を抜き出す")
    parser.add_argument("--session", required=True, help="events.jsonl の session_id")
    parser.add_argument("--command", help="シェルコマンドの一部。一致する呼び出しを探す")
    parser.add_argument("--around", help="時刻（events.jsonl の ts）。最も近い行を探す")
    parser.add_argument("--context", type=int, default=6, help="前後に出す項目の数（既定 6）")
    parser.add_argument("--limit", type=int, default=1200, help="1項目の最大文字数（既定 1200）")
    args = parser.parse_args(argv)

    path = find_log(args.session)
    if not path:
        print("セッションログが見つかりません: %s" % args.session)
        return 1
    rows = load(path)
    hits = []
    if args.command:
        for index, (_, payload) in enumerate(rows):
            command = (payload.get("args") or {}).get("command") if payload.get("type") == "tool_call" else None
            if isinstance(command, str) and args.command in command:
                hits.append(index)
    elif args.around:
        target = parse_time(args.around)
        timed = [(abs((parse_time(ts) - target).total_seconds()), i)
                 for i, (ts, _) in enumerate(rows) if parse_time(ts) and target]
        if timed:
            hits.append(min(timed)[1])
    if not hits:
        print("該当する箇所が見つかりません（項目数 %d）" % len(rows))
        return 1
    hit = hits[-1]  # 複数あれば最後のもの
    start, end = max(0, hit - args.context), min(len(rows), hit + args.context + 1)
    print("# セッションログ抜粋（%s、項目 %d〜%d / %d）\n" % (args.session, start + 1, end, len(rows)))
    for index in range(start, end):
        stamp, payload = rows[index]
        mark = " ← 該当" if index == hit else ""
        print("## [%s] %s%s\n" % (stamp, payload.get("type"), mark))
        print(describe(payload, args.limit) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
