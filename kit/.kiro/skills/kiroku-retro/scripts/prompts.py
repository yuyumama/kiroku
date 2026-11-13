"""対象セッションの会話を、本文を保存せずにログから読む。

sessions()  セッションの一覧。発話数、ログの状態、振り返りノートの有無。
distill()   1セッションの要約表示。ユーザー発話は原文、その後のツール実行は1行、応答は冒頭だけ。
page()      全セッションのユーザー発話を全文でページ表示する（要約表示で足りないときの予備）。
"""
import collections
import glob
import json
import os

import excerpt
from record import load_config, mask


def sessions_dir(root):
    directory = load_config(root).get("sessions_dir")
    if directory:
        directory = os.path.expanduser(directory)
        if not os.path.isabs(directory):
            directory = os.path.join(root, directory)
    return directory


def notes_dir(root):
    return os.path.join(root, ".kiro", "kiroku", "data", "notes")


def _in_period(moment, since, until):
    return not ((since and moment < since) or (until and moment >= until))


def _user_text(payload):
    text = excerpt.content_text(payload)
    if not text or not text.strip() or text.lstrip().startswith("/kiroku-retro"):
        return None
    return text


def discover(root, directory=None):
    """このリポジトリで行われたセッションを、ログの session.json から探す。

    フックが1度も動かなかったセッション（質問だけの会話など）も拾うためのもの。
    session.json の workspacePaths / rootPaths が、リポジトリのルートかその配下を指すものを選ぶ。
    """
    if directory:
        base = os.path.abspath(os.path.expanduser(directory))
    else:
        home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
        base = os.path.join(home, "sessions")
    base, target = glob.escape(base), os.path.realpath(root)
    found = []
    for pattern in (os.path.join(base, "*", "session.json"), os.path.join(base, "*", "*", "session.json")):
        for path in glob.glob(pattern):
            try:
                with open(path, encoding="utf-8-sig") as handle:
                    meta = json.load(handle)
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            places = []
            for key in ("workspacePaths", "rootPaths"):
                value = meta.get(key)
                places += [p for p in value if isinstance(p, str)] if isinstance(value, list) else []
            for place in places:
                real = os.path.realpath(os.path.expanduser(place))
                if real == target or real.startswith(target + os.sep):
                    session = os.path.basename(os.path.dirname(path))
                    if session not in found:
                        found.append(session)
                    break
    return found


def sessions(root, events, since=None, until=None):
    """期間内に活動があるセッションを、新しい順に並べる。

    記録（events.jsonl）に出たセッションに加えて、ログから見つけたセッションも含める。
    """
    directory = sessions_dir(root)
    last_event = {}
    for event in events:
        if event.get("session_id"):
            last_event[event["session_id"]] = event["ts"]
    names = list(last_event) + [s for s in discover(root, directory) if s not in last_event]
    rows = []
    for session in names:
        recorded = session in last_event
        row = {"session_id": session, "last_event": last_event.get(session), "recorded": recorded,
               "log": "ok", "prompts": 0, "prompt_chars": 0, "note": "none"}
        latest = None
        path = excerpt.find_log(session, directory)
        if not path:
            row["log"] = "missing_log"
        else:
            issues = []
            try:
                messages = excerpt.load(path, issues)
            except (OSError, UnicodeError):
                messages = None
                row["log"] = "unreadable_log"
            for stamp, payload in messages or []:
                moment = excerpt.parse_time(stamp)
                if payload.get("type") != "user" or moment is None:
                    continue
                text = _user_text(payload)
                if text and _in_period(moment, since, until):
                    row["prompts"] += 1
                    row["prompt_chars"] += len(text)
                    latest = moment if latest is None or moment > latest else latest
            if messages is not None and issues:
                row["log"] = "partial_log"
        if not recorded and not row["prompts"]:
            continue  # ログだけのセッションは、期間内に発話があるものだけを載せる
        note = os.path.join(notes_dir(root), session + ".json")
        if os.path.isfile(note):
            # ノートを書いた後に発話が増えていれば、読み直しが要る。
            written = os.path.getmtime(note)
            row["note"] = "stale" if latest and latest.timestamp() > written else "fresh"
        activity = [m for m in (excerpt.parse_time(row["last_event"]) if recorded else None, latest) if m]
        row["_activity"] = max(activity) if activity else None
        row["last_activity"] = row["_activity"].isoformat() if row["_activity"] else None
        rows.append(row)
    rows.sort(key=lambda r: r["_activity"].timestamp() if r["_activity"] else 0, reverse=True)
    for row in rows:
        del row["_activity"]
    return rows


def _clip(text, limit):
    """長い文を、頭と末尾を残して切る。"""
    if len(text) <= limit:
        return text
    head, tail = limit * 2 // 3, limit // 3
    return "%s\n…（中略 %d 文字）…\n%s" % (text[:head], len(text) - head - tail, text[-tail:])


def distill(root, session, since=None, until=None, user_chars=1500, assistant_chars=300):
    """1セッションを、ユーザー発話を軸に要約表示する。

    ユーザー発話は原文（長ければ頭と末尾）。次の発話までのツール実行は件数だけ、
    応答は最後の1件の冒頭だけを添える。全文が要る箇所は excerpt.py --index で読む。
    """
    result = {"session_id": session, "status": "ok", "issues": [], "turns": []}
    path = excerpt.find_log(session, sessions_dir(root))
    if not path:
        result["status"] = "missing_log"
        return result
    try:
        rows = excerpt.load(path, result["issues"])
    except (OSError, UnicodeError):
        result["status"] = "unreadable_log"
        return result
    if result["issues"]:
        result["status"] = "partial_log"
    turn = None
    for index, (stamp, payload) in enumerate(rows):
        kind = payload.get("type")
        if kind == "user":
            turn = None
            moment = excerpt.parse_time(stamp)
            text = _user_text(payload)
            if moment is None or text is None or not _in_period(moment, since, until):
                continue
            turn = {"index": index, "ts": stamp, "chars": len(text),
                    "text": _clip(mask(text), user_chars),
                    "tools": collections.Counter(), "tool_failures": 0, "reply": None}
            result["turns"].append(turn)
        elif turn is None:
            continue
        elif kind == "tool_call":
            turn["tools"][payload.get("toolName") or "unknown"] += 1
        elif kind == "tool_result" and payload.get("success") is False:
            turn["tool_failures"] += 1
        elif kind == "assistant":
            text = excerpt.content_text(payload)
            # 実機のログでは、各ターンの最後に "..." だけの応答が入る。文字のある応答だけを採る。
            if any(ch.isalnum() for ch in text):
                flat = " ".join(mask(text).split())
                turn["reply"] = {"index": index, "chars": len(text),
                                 "text": flat[:assistant_chars] + ("…" if len(flat) > assistant_chars else "")}
    for turn in result["turns"]:
        turn["tools"] = dict(turn["tools"])
    return result


def page(root, events, since=None, until=None, offset=0, size=20, chunk_chars=2000):
    if offset < 0 or size < 1 or chunk_chars < 1:
        raise ValueError("offset は0以上、size/chunk_chars は1以上が必要です")
    directory = sessions_dir(root)
    chunks, coverage = [], []
    session_ids = sorted({e["session_id"] for e in events if e.get("session_id")})
    for session in session_ids:
        status = {"session_id": session, "status": "ok", "issues": [], "prompts": 0}
        coverage.append(status)
        path = excerpt.find_log(session, directory)
        if not path:
            status["status"] = "missing_log"
            continue
        try:
            rows = excerpt.load(path, status["issues"])
        except (OSError, UnicodeError):
            status["status"] = "unreadable_log"
            continue
        if not rows:
            status["issues"].append("no_supported_messages")
        for index, (stamp, payload) in enumerate(rows):
            if payload.get("type") != "user":
                continue
            moment = excerpt.parse_time(stamp)
            if moment is None:
                status["issues"].append("unknown_timestamp:index_%d" % index)
                continue
            if (since and moment < since) or (until and moment >= until):
                continue
            text = excerpt.content_text(payload)
            if not text:
                status["issues"].append("no_text_content:index_%d" % index)
                continue
            if not text.strip() or text.lstrip().startswith("/kiroku-retro"):
                continue
            text = mask(text)
            status["prompts"] += 1
            parts = (len(text) + chunk_chars - 1) // chunk_chars
            for part in range(parts):
                chunks.append({"session_id": session, "index": index, "ts": stamp,
                               "part": part + 1, "parts": parts,
                               "text": text[part * chunk_chars:(part + 1) * chunk_chars]})
        if status["issues"]:
            status["status"] = "partial_log"
    end = min(offset + size, len(chunks))
    return {"coverage": coverage, "total_chunks": len(chunks), "offset": offset,
            "next_offset": end if end < len(chunks) else None,
            "chunks": chunks[offset:end]}
