#!/usr/bin/env python3
"""Kiro CLI（V3）のセッションログから、該当箇所の前後を抜き出す。

使い方:
  python3 .kiro/skills/kiroku-retro/scripts/excerpt.py --session <session_id> --command "<コマンドの一部>"
  python3 .kiro/skills/kiroku-retro/scripts/excerpt.py --session <session_id> --around 2026-11-12T03:04:05Z

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
from record import mask, find_root, load_config  # noqa: E402

SHOWN_TYPES = ("user", "assistant", "tool_call", "tool_result")


def find_log(session_id, sessions_dir=None):
    # セッションIDをパス・globとして解釈しない。
    if not session_id or any(c in session_id for c in '/\\*?[]') or session_id in ('.', '..'):
        return None
    if sessions_dir:
        base = glob.escape(os.path.abspath(os.path.expanduser(sessions_dir)))
        candidates = [os.path.join(base, session_id, "messages.jsonl"),
                      os.path.join(base, "*", session_id, "messages.jsonl")]
        if os.path.basename(base) == session_id:
            candidates.insert(0, os.path.join(base, "messages.jsonl"))
        for pattern in candidates:
            for path in sorted(glob.glob(pattern)):
                if os.path.isfile(path):
                    return path
        return None
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    for path in sorted(glob.glob(os.path.join(glob.escape(home), "sessions", "*", session_id, "messages.jsonl"))):
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


def load(path, issues=None):
    rows = []
    with open(path, encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except ValueError:
                if issues is not None:
                    issues.append("invalid_json:line_%d" % number)
                continue
            if not isinstance(row, dict):
                if issues is not None:
                    issues.append("invalid_row:line_%d" % number)
                continue
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                if issues is not None:
                    issues.append("invalid_payload:line_%d" % number)
                continue
            if payload.get("type") in SHOWN_TYPES:
                rows.append((row.get("timestamp"), payload))
    return rows


def content_text(payload):
    """文字列または text ブロックだけを読む。画像・内部メタデータは会話に含めない。"""
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content
                         if isinstance(block, dict) and block.get("type") == "text"
                         and isinstance(block.get("text"), str))
    return ""


def describe(payload, limit):
    kind = payload.get("type")
    if kind == "tool_call":
        args = payload.get("args") or {}
        text = "%s %s" % (payload.get("toolName"), json.dumps(args, ensure_ascii=False))
    elif kind == "tool_result":
        text = "success=%s %s" % (payload.get("success"), payload.get("content"))
    else:
        text = content_text(payload)
    text = mask(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))
    if len(text) > limit:
        text = text[:limit // 2] + "\n…（中略）…\n" + text[-limit // 2:]
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description="セッションログの該当箇所を抜き出す")
    parser.add_argument("--session", required=True, help="events.jsonl の session_id")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--command", help="シェルコマンドの一部。一致する呼び出しを探す")
    selector.add_argument("--around", help="時刻（events.jsonl の ts）。最も近い行を探す")
    selector.add_argument("--index", type=int, help="発話一覧に表示された項目番号（0始まり）")
    parser.add_argument("--offset", type=int, default=0, help="各項目の本文を読む開始文字位置")
    parser.add_argument("--context", type=int, default=6, help="前後に出す項目の数（既定 6）")
    parser.add_argument("--limit", type=int, default=1200, help="1項目の最大文字数（既定 1200）")
    args = parser.parse_args(argv)
    if args.context < 0 or args.offset < 0 or args.limit < 1:
        parser.error("context/offset は0以上、limit は1以上にしてください")

    root = find_root(os.getcwd())
    directory = load_config(root).get("sessions_dir")
    if directory:
        directory = os.path.expanduser(directory)
        if not os.path.isabs(directory):
            directory = os.path.join(root, directory)
    path = find_log(args.session, directory)
    if not path:
        print("セッションログが見つかりません: %s" % args.session)
        return 1
    try:
        rows = load(path)
    except (OSError, UnicodeError):
        print("セッションログが読めません: %s" % args.session)
        return 1
    hits = []
    if args.index is not None:
        if 0 <= args.index < len(rows):
            hits.append(args.index)
    elif args.command:
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
        print("## index=%d [%s] %s%s\n" % (index, stamp, payload.get("type"), mark))
        text = describe(payload, sys.maxsize)
        print(text[args.offset:args.offset + args.limit] + "\n")
        if len(text) > args.offset + args.limit:
            print("続き: --index %d --context 0 --offset %d\n" % (index, args.offset + args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
