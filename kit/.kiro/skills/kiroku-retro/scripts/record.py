#!/usr/bin/env python3
"""kiroku の記録フック。Kiro CLI（V3）のフックから呼ばれ、.kiro/kiroku/data/events.jsonl に追記する。

対応するフック:
  PreToolUse    テスト実行の前のレポートの状態（state に控える。イベントは書かない）
  PostToolUse   シェル実行の失敗、テスト実行の結果、書き込みツールが触ったファイル
  PostFileSave  エージェントが保存したファイル
  UserPromptSubmit  発話のタイミングのみ（本文は保存しない）
  Stop          ターンの終わり。終わりの記録が来なかったテスト実行を閉じる

守ること:
  - 標準出力には何も書かない（終了コード 0 の標準出力はエージェントの文脈に入る）。
  - 何が起きても終了コード 0 で終わる（記録の失敗で本来の作業を止めない）。
  - テスト失敗は記録時に分類しない。結果を生のまま残し、分類は aggregate.py が後から計算する。
"""
import datetime
from contextlib import contextmanager
import fnmatch
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import commands
    IMPORT_ERROR = None
except Exception as exc:  # 配布ファイルが欠けていても、フックは終了コード 0 で終わらせる
    commands, IMPORT_ERROR = None, exc

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

SCHEMA_VERSION = 2
DEFAULT_CONFIG = {
    "tests": [],
    "sessions_dir": None,
    "write_tools": ["fs_write", "fs_append", "str_replace"],
    "ignore_paths": [".kiro/kiroku/data/**", ".git/**"],
    "output_tail_chars": 2000,
    "max_dirty_files": 500,
    "retro_read_chars": 50000,
    "retro_output_chars": 20000,
    # 終わりのフックが来なかった実行前記録を捨てるまでの秒数。最初の値で、根拠は薄い。
    "test_start_max_seconds": 3600,
}
EXIT_CODE_RE = re.compile(r"Exit Code:\s*(-?\d+)\s*$")
TIMEOUT_RE = re.compile(r"(?i)\btimed out after\b")
# 実行が完了しなかった呼び出しの応答の先頭（Kiro CLI 3.2.1〜3.3.0 で実測。verification.md）
DENIED_HEAD = "Blocked by a permission rule"
CANCELLED_HEADS = ("The tool call was cancelled before it was approved", "The user rejected this tool call")


def not_run_kind(output):
    """終了コード -1（実行が完了しなかった呼び出し）を、出力の文言で分ける。

    文言で確かめられないものは、拒否とは決めつけず recording_gap にする。
    """
    output = (output or "").strip()
    # 権限の規則による拒否の応答は、止めた規則（コマンドの文字列）を引用する。引用された文字列を
    # 取り消しやタイムアウトの文言と取り違えないよう、応答の先頭の形を先に見る。
    if output.startswith(DENIED_HEAD):
        return "tool_denied"
    # 承認画面での Escape／Ctrl+C と、Deny once。どちらも人がその場で実行を断ったもの。
    if output.startswith(CANCELLED_HEADS):
        return "tool_cancelled"
    # 先頭が切れた古い記録（出力の末尾だけを残したもの）のために、含むかどうかでも見る。
    if DENIED_HEAD in output:
        return "tool_denied"
    if "cancelled before it was approved" in output:
        return "tool_cancelled"
    if TIMEOUT_RE.search(output):
        return "tool_timeout"
    return "recording_gap"
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "[REDACTED]"),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED]"),
    (re.compile(r"\b(sk|ksk|xox[abprs])-[A-Za-z0-9_-]{16,}"), "[REDACTED]"),
    (re.compile(r"(://[^/\s:@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\b(authorization|bearer)([:=\s]+)(bearer\s+)?[A-Za-z0-9._~+/=-]{8,}"),
     r"\1\2[REDACTED]"),
    (re.compile(
        r"(?i)([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|passwd|password|credential)[A-Za-z0-9_-]*"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]{4,}"), r"\1[REDACTED]"),
]


def mask(text):
    """秘匿情報らしい値を伏せる。完全ではないので、生データは git に載せないこと。"""
    if not isinstance(text, str):
        return text
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def git(root, *args, stdin=None):
    """git を実行して標準出力を返す。失敗したら None。"""
    try:
        done = subprocess.run(
            ["git", "-C", root] + list(args), input=stdin, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def find_root(cwd):
    top = git(cwd, "rev-parse", "--show-toplevel")
    return top.strip() if top and top.strip() else cwd


def script_root(given):
    """集約・台帳のスクリプトが読み書きするリポジトリ。(ルート, 拒否の理由) を返す。

    スクリプトが書くのは、すべてルートの下の .kiro/kiroku/data。シェルの許可規則は引数を制限できない
    （kiro.dev/docs/permissions/ の Pattern matching）ので、Kiro のセッションの中（KIRO_SESSION_ID がある）
    では、--root を現在のリポジトリ以外に向けさせない。人がいない実行でも、書き込み先がこのリポジトリに
    収まるようにするため。Kiro の外（人の端末）では、これまでどおり --root を受け付ける。
    """
    current = find_root(os.getcwd())
    if given is None:
        return current, None
    if os.environ.get("KIRO_SESSION_ID") and os.path.realpath(given) != os.path.realpath(current):
        return None, ("Kiro のセッションの中では、--root に現在のリポジトリ以外を指定できません"
                      "（指定 %s、現在 %s）。--root を省いて実行してください。" % (given, current))
    return given, None


# 導入先の設定に「足す」項目。既定の値は常に効き、設定に書いた値はそこに加わる。
# 古い版の既定値が設定ファイルに残っていても、新しい既定値が隠れないようにするため。
ADDITIVE_KEYS = ("write_tools", "ignore_paths")


def load_config(root):
    """既定値に、導入先の config.json を重ねる。config.json に無い項目は既定値で動く。"""
    config = dict(DEFAULT_CONFIG)
    try:
        with open(os.path.join(root, ".kiro", "kiroku", "config.json"), encoding="utf-8") as handle:
            custom = json.load(handle)
    except (OSError, ValueError):
        return config
    if not isinstance(custom, dict):
        return config
    for key, value in custom.items():
        if key in ADDITIVE_KEYS and isinstance(value, list):
            config[key] = list(DEFAULT_CONFIG[key]) + [v for v in value if v not in DEFAULT_CONFIG[key]]
        elif value is not None or key not in DEFAULT_CONFIG:
            config[key] = value
    return config


# 0 以上の整数で指定する項目。0 は「残さない」。範囲外や数でない値は、既定値で動く。
COUNT_KEYS = ("output_tail_chars",)


def count_setting(config, key):
    """0 以上の整数の設定値。不正な値（負数、数でない値、真偽値）は既定値に戻す。"""
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_CONFIG[key]
    return value


def tail_text(text, limit):
    """末尾の limit 文字。0 なら空文字（[-0:] は全体になるので、使わない）。"""
    return text[-limit:] if limit > 0 else ""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def rel_path(root, path, cwd):
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.relpath(os.path.realpath(path), os.path.realpath(root)).replace(os.sep, "/")


def match_glob(path, pattern):
    """`**` をディレクトリまたぎとして扱う簡易 glob。"""
    if pattern.endswith("/**"):
        return path == pattern[:-3] or path.startswith(pattern[:-2])
    return fnmatch.fnmatch(path, pattern)


def ignored(path, config):
    return path.startswith("../") or any(match_glob(path, p) for p in config["ignore_paths"])


def blob_hashes(root, paths):
    """作業ツリーの内容の git blob ハッシュ。無いファイルは None。"""
    existing = [p for p in paths if os.path.isfile(os.path.join(root, p))]
    hashes = {p: None for p in paths}
    if existing:
        out = git(root, "hash-object", "--stdin-paths", stdin="\n".join(existing) + "\n")
        if out is not None:
            for path, value in zip(existing, out.split()):
                hashes[path] = value
    return hashes


VCS_MARKERS = (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
               ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"))


def vcs_in_progress(root):
    """merge や rebase の途中（コンフリクトの解消中など）なら、その種別。そうでなければ None。"""
    git_dir = git(root, "rev-parse", "--absolute-git-dir")
    if not git_dir:
        return None
    for name, label in VCS_MARKERS:
        if os.path.exists(os.path.join(git_dir.strip(), name)):
            return label
    return None


def worktree_state(root, config):
    """その時点のコミットと、作業ツリーで変更されているファイルの内容ハッシュ。"""
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    if status is None:
        return (head.strip() if head else None), None, False
    paths = []
    parts = status.split("\0")
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":  # 改名とコピーは、元のパスが次の要素に入る
            if index < len(parts) and parts[index]:
                paths.append(parts[index])
            index += 1
        paths.append(path)
    paths = sorted({p for p in paths if not ignored(p, config)})
    limit = config["max_dirty_files"]
    truncated = len(paths) > limit
    return (head.strip() if head else None), blob_hashes(root, paths[:limit]), truncated


def parse_junit(root, cwd, files):
    """JUnit XML から、テストID単位の成否を取り出す。"""
    results = {"passed": [], "failed": [], "error": [], "skipped": []}
    test_files = {}
    for path in files:
        tree = ET.parse(path)
        for suite in tree.iter():
            if suite.tag not in ("testsuite", "testsuites"):
                continue
            for case in suite.findall("testcase"):
                classname = case.get("classname") or ""
                name = case.get("name") or ""
                test_id = "%s::%s" % (classname, name) if classname else name
                tags = {child.tag for child in case}
                if "failure" in tags:
                    status = "failed"
                elif "error" in tags:
                    status = "error"
                elif "skipped" in tags:
                    status = "skipped"
                else:
                    status = "passed"
                results[status].append(test_id)
                source = case.get("file") or suite.get("file")
                if source and status in ("failed", "error"):
                    test_files[test_id] = rel_path(root, source, cwd)
    for key in results:
        results[key] = sorted(set(results[key]))
    return results, test_files


def load_state(retro_dir):
    try:
        with open(os.path.join(retro_dir, "state.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(retro_dir, state):
    os.makedirs(retro_dir, exist_ok=True)
    path = os.path.join(retro_dir, "state.json")
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(path + ".tmp", path)


def report_stamp(root, entry):
    """レポートの実行前後を比べる。内容が同じ再実行も、更新時刻・置換で区別する。

    ctime は使わない。権限の変更（chmod）などでも変わり、古い結果を新しい実行のものと取り違える。
    """
    stamps = []
    for path in sorted(glob.glob(os.path.join(root, entry.get("report") or ""), recursive=True)):
        if os.path.isfile(path):
            stat = os.stat(path)
            stamps.append([os.path.relpath(path, root), stat.st_mtime_ns, stat.st_size, stat.st_ino])
    return stamps


def same_stamp(stamp):
    """ctime を含んでいた版（要素が5個）の控えを、今の形にそろえる。"""
    return [item[:2] + item[3:] if isinstance(item, list) and len(item) == 5 else item
            for item in stamp if isinstance(item, list)] if isinstance(stamp, list) else []


WILDCARDS = "*?["


def may_share_report(first, second):
    """2つのレポートの指定が、同じファイルを指しうるか。確かめられないときは、指しうるとする。"""
    first, second = (os.path.normpath(p or "").replace(os.sep, "/") for p in (first, second))
    if first == second:
        return True
    plain = [not any(c in p for c in WILDCARDS) for p in (first, second)]
    if all(plain):
        return False
    for path, pattern, is_plain in ((first, second, plain[0]), (second, first, plain[1])):
        if is_plain:
            # `**` は階層をまたぐ。fnmatch の `*` は `/` にも当たるので、広めの判定になる。
            return fnmatch.fnmatch(path, pattern.replace("**/", "*"))
    heads = [re.split(r"[*?\[]", p, 1)[0].rsplit("/", 1)[0] for p in (first, second)]
    return heads[0].startswith(heads[1]) or heads[1].startswith(heads[0])


def start_key(payload, cwd):
    raw = json.dumps([payload.get("session_id") or os.environ.get("KIRO_SESSION_ID"),
                      payload.get("tool_name"), cwd, payload.get("tool_input") or {}], sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def live_starts(state, config):
    """終わりのフックが来ないまま残った実行前記録を、期限で捨てる。

    Kiro の異常終了などで PostToolUse が来ないと、記録が残り続け、
    同じレポートを使う以後の実行がすべて「重複」と判定されてしまう。
    時刻の無い記録（期限を入れる前の版が書いたもの）も、古さを確かめられないので捨てる。
    """
    limit = config.get("test_start_max_seconds")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
        limit = DEFAULT_CONFIG["test_start_max_seconds"]
    starts = state.get("test_starts")
    moment = time.time()
    state["test_starts"] = {
        key: value for key, value in (starts.items() if isinstance(starts, dict) else ())
        if isinstance(value, dict) and isinstance(value.get("at"), (int, float))
        and not isinstance(value.get("at"), bool) and abs(moment - value["at"]) <= limit}
    return state["test_starts"]


def close_starts(retro_dir, session_id):
    """このセッションで、終わりの記録が来ないまま残った実行前記録を閉じる。

    別のフックに止められた実行や、実行中に中断された実行では PostToolUse が呼ばれない。
    残したままにすると、同じレポートを使う次のテスト実行が「重複」と判定されるので、
    ターンの終わり（Stop）と次の発話（UserPromptSubmit）で閉じ、記録の不足として残す。
    セッションの分からない記録（この処理を入れる前の版が書いたもの）は、期限に任せる。
    """
    if not session_id:
        return []
    state = load_state(retro_dir)
    starts = state.get("test_starts")
    if not isinstance(starts, dict):
        return []
    closed = [key for key, value in starts.items()
              if isinstance(value, dict) and value.get("session") == session_id]
    if not closed:
        return []
    events = []
    for key in closed:
        start = starts.pop(key)
        if start.get("background"):
            starts[key] = start   # バックグラウンドの処理は、ターンが終わっても続いている
            continue
        event = {"type": "recording_gap", "tool": start.get("tool"), "reason": "test_end_missing"}
        count = start.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count > 1:
            event["count"] = count   # 同じ実行前記録が重なっていた回数
        at = start.get("at")
        if isinstance(at, (int, float)) and not isinstance(at, bool):
            # 閉じた時刻ではなく、実行が始まった時刻。集約はこれで期間と振り返りの前後を決める。
            event["started_at"] = datetime.datetime.fromtimestamp(
                at, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        events.append(event)
    save_state(retro_dir, state)
    return events


def begin_test(payload, root, cwd, retro_dir, config, session_id=None):
    entry = commands.test_entry((payload.get("tool_input") or {}).get("command"), config)
    if entry is None:
        return
    state = load_state(retro_dir)
    starts = live_starts(state, config)
    key = start_key(payload, cwd)
    before = report_stamp(root, entry)
    overlap = False
    for other, active in list(starts.items()):
        if not may_share_report(active.get("report"), entry.get("report")):
            continue
        if active.get("background"):
            # バックグラウンドで起動した実行。ほかの実行が書いたと分かっている状態（seen）から
            # レポートが変わっていれば、裏の処理が書き終えたとみなして忘れる。
            # 変わっていなければ、これから書く可能性がある。
            seen = active.get("seen", active.get("before"))
            if active.get("report") == entry.get("report") and same_stamp(seen) != before:
                del starts[other]
                continue
        else:
            active["overlap"] = True
        overlap = True
    prior = starts.get(key, {})
    if prior.get("background"):
        prior = {}
    starts[key] = {"before": before, "report": entry.get("report"),
                   "count": prior.get("count", 0) + 1, "overlap": overlap, "at": time.time(),
                   "session": session_id, "tool": payload.get("tool_name")}
    save_state(retro_dir, state)


def finish_test(payload, cwd, retro_dir):
    state = load_state(retro_dir)
    key = start_key(payload, cwd)
    start = state.get("test_starts", {}).get(key)
    if start is not None and start.get("background"):
        return None   # 起動だけが記録された実行。終わりの記録とは対応しない
    if start is not None:
        start = dict(start)
        if (payload.get("tool_input") or {}).get("run_in_background") is True:
            # 起動の直後に呼ばれた。処理は続いていて、同じレポートをこれから書く可能性がある。
            # 記録を消さず、「書き手が残っている」ことを期限まで覚えておく。
            state["test_starts"][key] = dict(start, background=True, count=1)
        elif start["count"] > 1:
            state["test_starts"][key]["count"] -= 1
        else:
            del state["test_starts"][key]
        save_state(retro_dir, state)
    return start


def note_report_seen(root, retro_dir, entry):
    """通常の実行が終わった時点のレポートの状態を、バックグラウンドの記録に控える。

    裏の処理が動いている間に通常の実行がレポートを書き換えても、それを「裏の処理が
    書き終えた」と取り違えないため。次の実行の前にこの状態から変わっていれば、
    通常の実行のあとで誰か（裏の処理）が書いたと分かる。
    """
    state = load_state(retro_dir)
    changed = False
    for active in (state.get("test_starts") or {}).values():
        if isinstance(active, dict) and active.get("background") \
                and active.get("report") == entry.get("report"):
            active["seen"] = report_stamp(root, entry)
            changed = True
    if changed:
        save_state(retro_dir, state)


def read_test_report(root, cwd, retro_dir, entry, start=None):
    """設定されたレポートを読む。前回と同じファイルなら古いものとして読まない。"""
    pattern = entry.get("report")
    if not pattern:
        return None, {}, "no_report_configured"
    if entry.get("format", "junit") != "junit":
        return None, {}, "unsupported_format"
    files = sorted(glob.glob(os.path.join(root, pattern), recursive=True))
    if not files:
        return None, {}, "report_not_found"
    stamp = report_stamp(root, entry)
    state = load_state(retro_dir)
    if same_stamp(state.get("reports", {}).get(pattern)) == stamp:
        return None, {}, "report_stale"
    if start is None:
        return None, {}, "missing_test_start"
    if start.get("overlap") or start.get("report") != pattern:
        return None, {}, "ambiguous_test_run"
    before = same_stamp(start.get("before"))
    if before == stamp:
        return None, {}, "report_stale"
    if any(item in before for item in stamp):
        return None, {}, "report_partly_stale"
    try:
        results, test_files = parse_junit(root, cwd, files)
    except (ET.ParseError, OSError):
        return None, {}, "report_unreadable"
    state.setdefault("reports", {})[pattern] = stamp
    save_state(retro_dir, state)
    return results, test_files, None


def shell_events(payload, root, cwd, retro_dir, config):
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or commands.own_script(command):
        return []  # kiroku 自身のスクリプト（振り返り中の集約など）は記録しない
    start = finish_test(payload, cwd, retro_dir)
    response = payload.get("tool_response")
    response = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
    found = EXIT_CODE_RE.search(response)
    exit_code = int(found.group(1)) if found else None
    output = response[:found.start()] if found else response
    if output.startswith("Output:\n"):
        output = output[len("Output:\n"):]
    tail = tail_text(mask(output.rstrip()), count_setting(config, "output_tail_chars"))

    if exit_code is None:
        return [{"type": "recording_gap", "tool": payload.get("tool_name"),
                 "reason": "unknown_shell_response", "command": mask(command)}]

    entry, how = commands.test_match(command, config)
    if entry is not None and exit_code != -1:
        head, dirty, truncated = worktree_state(root, config)
        background = tool_input.get("run_in_background") is True
        if background:
            # バックグラウンドの実行では、処理が終わる前にこのフックが呼ばれる。終了コードは
            # 起動の成否で、レポートもまだ書かれていない。結果は読まず、未完了として残す。
            results, test_files, note = None, {}, "background_not_finished"
        else:
            results, test_files, note = read_test_report(root, cwd, retro_dir, entry, start)
            note_report_seen(root, retro_dir, entry)
        event = {
            "type": "test_run", "command": mask(command), "exit_code": exit_code,
            "head": head, "dirty": dirty, "results": results, "test_files": test_files,
        }
        if how == "text":
            event["matched"] = "text"  # 起動先ではなく文面で一致。実行か言及かは、ここでは決めない
        if background:
            event["background"] = True
        if truncated:
            event["dirty_truncated"] = True
        vcs = vcs_in_progress(root)
        if vcs:
            event["vcs"] = vcs
        if note:
            event["note"] = note
        if exit_code != 0:
            event["output_tail"] = tail
        if how == "text" and results is None and exit_code != 0:
            # テストの実行と確かめられなかった失敗は、ツール失敗としても残す。
            return [event, {"type": "tool_failure", "tool": payload.get("tool_name"),
                            "command": mask(command), "exit_code": exit_code,
                            "output_tail": tail, "head": head}]
        return [event]
    if exit_code == -1:
        # 完了しなかった呼び出し。人による取り消し、タイムアウト、権限の規則による拒否を分ける。
        kind = not_run_kind(output)
        # judged：応答の全体（先頭を含む）で種類を決めた印。出力の末尾は切り詰めて残すので、
        # 集約は末尾から決め直さず、この種類をそのまま使う（印の無い古い記録だけを決め直す）。
        event = {"type": kind, "tool": payload.get("tool_name"),
                 "command": mask(command), "output_tail": tail, "judged": "response"}
        if kind == "recording_gap":
            event["reason"] = "unknown_not_run"
        return [event]
    if exit_code not in (0, None):
        head = git(root, "rev-parse", "HEAD")
        return [{"type": "tool_failure", "tool": payload.get("tool_name"),
                 "command": mask(command), "exit_code": exit_code,
                 "output_tail": tail, "head": head.strip() if head else None}]
    return []


def write_event(root, cwd, path, via, config):
    if not isinstance(path, str) or not path:
        return []
    relative = rel_path(root, path, cwd)
    if ignored(relative, config):
        return []
    head = git(root, "rev-parse", "HEAD")
    return [{"type": "write", "path": relative, "hash": blob_hashes(root, [relative])[relative],
             "via": via, "head": head.strip() if head else None}]


def append_events(retro_dir, events):
    os.makedirs(retro_dir, exist_ok=True)
    data = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in events)
    fd = os.open(os.path.join(retro_dir, "events.jsonl"),
                 os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)


@contextmanager
def recording_lock(retro_dir):
    """同時フックによる state.json と JSONL の競合を POSIX/Windows 両方で防ぐ。"""
    os.makedirs(retro_dir, exist_ok=True)
    fd = os.open(os.path.join(retro_dir, "record.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        elif msvcrt:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        locked = True
        yield
    finally:
        if locked and msvcrt and not fcntl:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def mark_retro(retro_dir, session_id, stamp):
    """このセッションで振り返りが始まった時刻を残す。集約は、それ以降を開発の記録から除く。"""
    if not session_id:
        return
    state = load_state(retro_dir)
    marks = state.get("retro_sessions")
    if not isinstance(marks, dict):
        marks = state["retro_sessions"] = {}
    if session_id not in marks:
        marks[session_id] = stamp
        save_state(retro_dir, state)


def main():
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    cwd = payload.get("cwd") or os.getcwd()
    root = find_root(cwd)
    config = load_config(root)
    retro_dir = os.path.join(root, ".kiro", "kiroku", "data")
    hook = payload.get("hook_event_name")
    tool = payload.get("tool_name")

    session_id = payload.get("session_id") or os.environ.get("KIRO_SESSION_ID")
    with recording_lock(retro_dir):
        events = []
        if hook in ("PostFileSave", "PostFileCreate"):
            events = write_event(root, cwd, payload.get("file_path"), hook, config)
        elif hook == "UserPromptSubmit":
            text = payload.get("prompt")
            if not isinstance(text, str):
                text = os.environ.get("USER_PROMPT") or ""
            # 次の発話が来たなら、前のターンの実行は終わっている。
            events = close_starts(retro_dir, session_id)
            # 振り返りの起動そのものは記録しない。
            if text.lstrip().startswith("/kiroku-retro"):
                mark_retro(retro_dir, session_id, now())
            elif text.strip():
                events.append({"type": "prompt"})
        elif hook == "Stop":
            events = close_starts(retro_dir, session_id)
        elif hook == "PreToolUse" and tool in ("execute_bash", "execute_pwsh"):
            begin_test(payload, root, cwd, retro_dir, config, session_id)
        elif hook == "PostToolUse" and tool in ("execute_bash", "execute_pwsh"):
            command = (payload.get("tool_input") or {}).get("command")
            if commands.own_script(command) == 'aggregate.py':
                mark_retro(retro_dir, session_id, now())
            events = shell_events(payload, root, cwd, retro_dir, config)
        elif hook == "PostToolUse" and tool in config["write_tools"]:
            # 整形フックなどが PostFileSave で書き換えた後の内容を、ここで取り直す。
            tool_input = payload.get("tool_input") or {}
            events = write_event(root, cwd, tool_input.get("path"), "PostToolUse:%s" % tool, config)

        if events:
            stamp = now()
            for event in events:
                event.update({"v": SCHEMA_VERSION, "ts": stamp, "session_id": session_id})
            append_events(retro_dir, events)
    return retro_dir


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 記録の失敗で本来の作業を止めない
        try:
            log_dir = os.path.join(find_root(os.getcwd()), ".kiro", "kiroku", "data")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "record-errors.log"), "a", encoding="utf-8") as log:
                log.write("%s %s: %s\n" % (now(), type(exc).__name__, exc))
        except Exception:
            pass
    sys.exit(0)
