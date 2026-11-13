"""振り返りノートを読み、引用がログに実在するかを照合し、トピックごとに数える。

ノートは、振り返りのエージェントがセッションごとに書く JSON。
  .kiro/kiroku/data/notes/<session_id>.json
  {"session_id": "...", "findings": [
     {"kind": "correction", "topic": "テスト実行コマンド", "summary": "...",
      "quote": "ユーザー発話の一節を原文のまま", "index": 18, "context": "..."}]}

エージェントの申告をそのまま信じず、引用の実在と、何セッションに出たかはここで機械的に確かめる。
一度照合できた引用は notes-verified.json に残すので、あとでログが消えても数えられる。
"""
import hashlib
import json
import os

import excerpt
import prompts
from record import mask

KINDS = {
    "repeated_explanation": "繰り返しの説明",
    "explanation_feedback": "説明への指摘",
    "correction": "やり方の指摘",
    "standing_policy": "継続的な方針",
    "unsure": "確認事項",
}
MIN_QUOTE_CHARS = 4
MAX_TOPIC_CHARS = 40
PATTERN_SESSIONS = 2  # 会話からの型とみなす、別々のセッションの数


def fold(text):
    """空白の違いを無視して比べるための形にする。"""
    return "".join(text.split())


def finding_key(session, finding):
    raw = "%s|%s" % (session, fold(str(finding.get("quote") or "")))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load(root):
    """ノートを読む。壊れたノートは error 付きで返す。"""
    directory = prompts.notes_dir(root)
    notes = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return notes
    for name in names:
        if not name.endswith(".json"):
            continue
        note = {"session_id": name[:-5], "findings": [], "error": None}
        notes.append(note)
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                data = json.load(handle)
            findings = data.get("findings") if isinstance(data, dict) else None
            if not isinstance(findings, list):
                raise ValueError("findings が配列ではありません")
            note["findings"] = [f for f in findings if isinstance(f, dict)]
        except (OSError, ValueError) as exc:
            note["error"] = str(exc)
    return notes


def _problem(finding):
    """形式の誤りを返す。問題が無ければ None。"""
    if finding.get("kind") not in KINDS:
        return "kind が不正（%s のいずれか）" % "、".join(KINDS)
    topic = finding.get("topic")
    if not isinstance(topic, str) or not topic.strip() or len(topic) > MAX_TOPIC_CHARS \
            or any(ch.isspace() or ch == "`" for ch in topic.strip()):
        return "topic が不正（空白なし、%d 文字以内）" % MAX_TOPIC_CHARS
    quote = finding.get("quote")
    if not isinstance(quote, str) or not fold(quote):
        return "quote が空"
    return None


def _locate(rows, quote, index):
    """引用を含むユーザー発話を探す。指定の位置を先に見て、無ければセッション全体を探す。"""
    needle = fold(quote)
    # 短い引用は、どの発話にも含まれてしまう。「ん？」のような短い発話そのものを引用したときだけ認める。
    whole = len(needle) < MIN_QUOTE_CHARS
    order = ([index] if isinstance(index, int) and 0 <= index < len(rows) else []) + list(range(len(rows)))
    for position in order:
        stamp, payload = rows[position]
        if payload.get("type") != "user":
            continue
        text = excerpt.content_text(payload)
        if whole:
            if needle == fold(text):
                return position, stamp
        elif needle in fold(text) or needle in fold(mask(text)):
            return position, stamp
    return None


def verify(root):
    """すべてのノートの引用を照合する。照合できたものは記録に残す。"""
    store_path = os.path.join(root, ".kiro", "kiroku", "data", "notes-verified.json")
    try:
        with open(store_path, encoding="utf-8") as handle:
            store = json.load(handle)
    except (OSError, ValueError):
        store = {}
    changed = False
    directory = prompts.sessions_dir(root)
    findings, problems = [], []
    for note in load(root):
        session = note["session_id"]
        if note["error"]:
            problems.append({"session_id": session, "problem": "ノートを読めない：%s" % note["error"]})
            continue
        rows = None
        path = excerpt.find_log(session, directory)
        if path:
            try:
                rows = excerpt.load(path)
            except (OSError, UnicodeError):
                rows = None
        for number, finding in enumerate(note["findings"], 1):
            problem = _problem(finding)
            if problem:
                problems.append({"session_id": session, "problem": "%d 件目：%s" % (number, problem)})
                continue
            key = finding_key(session, finding)
            row = {"session_id": session, "kind": finding["kind"], "topic": finding["topic"].strip(),
                   "summary": str(finding.get("summary") or ""), "quote": finding["quote"],
                   "index": finding.get("index"), "ts": None, "status": "verified",
                   "context": str(finding.get("context") or "")}
            hit = _locate(rows, finding["quote"], finding.get("index")) if rows is not None else None
            if hit:
                row["index"], row["ts"] = hit
                if store.get(key) != {"index": hit[0], "ts": hit[1]}:
                    store[key] = {"index": hit[0], "ts": hit[1]}
                    changed = True
            elif key in store:  # 以前に照合できている。ログが消えていても数える。
                row["index"], row["ts"] = store[key]["index"], store[key]["ts"]
            elif rows is None:
                row["status"] = "no_log"
            else:
                row["status"] = "quote_not_found"
            if row["status"] != "verified":
                problems.append({"session_id": session, "problem": "%d 件目：%s" % (number, {
                    "no_log": "ログが無く、引用を照合できない",
                    "quote_not_found": "引用がユーザー発話に見つからない" if len(fold(finding["quote"])) >= MIN_QUOTE_CHARS
                    else "%d 文字未満の引用は、発話の全体と一致するときだけ認める" % MIN_QUOTE_CHARS}[row["status"]])})
            findings.append(row)
    if changed:
        os.makedirs(os.path.dirname(store_path), exist_ok=True)
        with open(store_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(store, handle, ensure_ascii=False)
        os.replace(store_path + ".tmp", store_path)
    for row in findings:
        row["_time"] = excerpt.parse_time(row["ts"]) if row["ts"] else None
    return findings, problems


def topics(findings, since=None, until=None):
    """照合できた引用を、トピックごとにまとめる。"""
    grouped = {}
    for row in findings:
        if row["status"] != "verified":
            continue
        entry = grouped.setdefault(row["topic"], {
            "topic": row["topic"], "kinds": set(), "summary": row["summary"],
            "all_sessions": set(), "sessions": set(), "firm_sessions": set(), "count": 0,
            "examples": []})
        entry["kinds"].add(row["kind"])
        entry["all_sessions"].add(row["session_id"])
        moment = row["_time"]
        if moment is None or ((since is None or moment >= since) and (until is None or moment < until)):
            entry["sessions"].add(row["session_id"])
            if row["kind"] != "unsure":
                entry["firm_sessions"].add(row["session_id"])
            entry["count"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append({"session_id": row["session_id"], "index": row["index"],
                                          "quote": row["quote"], "context": row["context"]})
    rows = []
    for entry in grouped.values():
        sessions = len(entry["sessions"])
        # 「確認事項」だけのセッションは、型の根拠に数えない。趣旨がはっきりしない発話だから。
        firm = len(entry["firm_sessions"])
        entry["unsure_only_sessions"] = sessions - firm
        del entry["firm_sessions"]
        entry["candidate"] = firm >= PATTERN_SESSIONS or \
            (firm >= 1 and "standing_policy" in entry["kinds"])
        entry["kinds"] = sorted(entry["kinds"])
        entry["all_sessions"] = len(entry["all_sessions"])
        entry["sessions"] = sessions
        rows.append(entry)
    return sorted(rows, key=lambda r: (not r["candidate"], -r["sessions"], r["topic"]))
