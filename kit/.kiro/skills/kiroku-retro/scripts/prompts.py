"""対象セッションのユーザー発話を、本文を保存せずページ単位で読む。"""
import os

import excerpt
from record import load_config, mask


def page(root, events, since=None, until=None, offset=0, size=20, chunk_chars=2000):
    if offset < 0 or size < 1 or chunk_chars < 1:
        raise ValueError("offset は0以上、size/chunk_chars は1以上が必要です")
    directory = load_config(root).get("sessions_dir")
    if directory:
        directory = os.path.expanduser(directory)
        if not os.path.isabs(directory):
            directory = os.path.join(root, directory)
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
