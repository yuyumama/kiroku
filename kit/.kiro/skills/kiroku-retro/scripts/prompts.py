"""対象セッションの会話を、本文を保存せずにログから読む。

sessions()  セッションの一覧。発話数、ログの状態、振り返りノートの有無。
distill()   1セッションの要約表示。ユーザー発話は原文、その後のツール実行は1行、応答は冒頭だけ。
page()      全セッションのユーザー発話を全文でページ表示する（要約表示で足りないときの予備）。
"""
import collections
import glob
import json
import os
import re
import time

import excerpt
from record import load_config, load_state, mask, recording_lock, save_state
import commands

DUPLICATE_WINDOW = 8    # 同じ発話の二重記録とみなす、ログ上の距離（項目数）
DUPLICATE_MIN_CHARS = 12  # これより短い発話は、同じ内容でも重複とみなさない


def sessions_dir(root):
    directory = load_config(root).get("sessions_dir")
    if directory:
        directory = os.path.expanduser(directory)
        if not os.path.isabs(directory):
            directory = os.path.join(root, directory)
    return directory


def notes_dir(root):
    return os.path.join(root, ".kiro", "kiroku", "data", "notes")


def _agent_of(path):
    """ログの隣の session.json に記録されたプロファイル名。無ければ推測せず None。"""
    try:
        with open(os.path.join(os.path.dirname(path), 'session.json'), encoding='utf-8-sig') as handle:
            meta = json.load(handle)
        agent = meta.get('agentMode')
        return agent if isinstance(agent, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def session_agent(root, session):
    """session.json に記録されたプロファイル名。無ければ推測せず None。"""
    path = excerpt.find_log(session, sessions_dir(root)) if session else None
    return _agent_of(path) if path else None


def running_session_log(root, session):
    """実行中のセッション（振り返りそのもの）のログ。

    sessions_dir は過去の開発セッションを探す場所（IDE のエクスポートなど）。実行中のセッションの
    ログは Kiro の既定の置き場に書かれるので、sessions_dir に無ければそちらも見る（CLI で実測した置き場）。
    """
    if not session:
        return None
    configured = sessions_dir(root)
    return (excerpt.find_log(session, configured) if configured else None) or excerpt.find_log(session, None)


def execution_context(root):
    """現在のセッションのプロファイルを確認する。取れないときは未確認とし、どの確認に失敗したかを返す。"""
    session = os.environ.get('KIRO_SESSION_ID')
    path = running_session_log(root, session)
    agent = _agent_of(path) if path else None
    if not session:
        reason = 'KIRO_SESSION_ID がありません（Kiro の外で実行したか、この環境では渡されない）'
    elif not path:
        reason = 'セッション %s のログが、sessions_dir にも Kiro の既定の置き場にもありません' % session
    elif agent is None:
        reason = 'セッションのメタデータ（session.json）に agentMode がありません'
    else:
        reason = None
    return {'session_id': session, 'agent': agent, 'reason': reason,
            'status': 'unverified' if agent is None else 'verified' if agent == 'kiroku-retro' else 'wrong_agent'}


def _in_period(moment, since, until):
    return not ((since and moment < since) or (until and moment >= until))


def _user_text(payload):
    text = excerpt.content_text(payload)
    if not text or not text.strip() or text.lstrip().startswith("/kiroku-retro"):
        return None
    return text


def duplicates(rows):
    """二重に記録されたユーザー発話を探す。{重複の index: 最初の index}。

    作業中に割り込んで送った発話は、ログに2回残ることがある。近くにあり、内容が同じか、
    後の発話が前の発話に丸ごと含まれるものは、1件として扱う。
    後の発話のほうが長いとき（前の発話に指示を足したもの）は、新しい内容があるので別の発話とする。
    """
    recent, found = [], {}
    for index, (_, payload) in enumerate(rows):
        if payload.get("type") != "user":
            continue
        text = _user_text(payload)
        if text is None:
            continue
        folded = "".join(text.split())
        recent = [item for item in recent if index - item[0] <= DUPLICATE_WINDOW]
        if len(folded) < DUPLICATE_MIN_CHARS:
            continue  # 「はい」「続けて」のような短い発話は、本当に繰り返すことがある
        for first, other in recent:
            if folded in other:
                found[index] = first
                break
        else:
            recent.append((index, folded))
    return found


SHOWN_LIMIT = 50   # セッションごとに控える、要約表示した範囲の数。最初の値で、根拠は薄い


def record_shown(root, session, since, until):
    """要約表示（aggregate.py --session）で見せた期間を控える。

    ノートが「読み終えた」とみなせるのは、ノートを書く前に、この範囲で見せた発話だけ。
    期間を広げたときに、前回の範囲の外にあった発話を既読と取り違えないために使う。
    控えるのは時刻だけで、会話の本文は保存しない。
    """
    data_dir = os.path.join(root, ".kiro", "kiroku", "data")
    entry = [since.isoformat() if since else None, until.isoformat() if until else None, time.time()]
    with recording_lock(data_dir):
        state = load_state(data_dir)
        shown = state.get("shown_ranges")
        if not isinstance(shown, dict):
            shown = state["shown_ranges"] = {}
        ranges = shown.get(session) if isinstance(shown.get(session), list) else []
        shown[session] = (ranges + [entry])[-SHOWN_LIMIT:]
        save_state(data_dir, state)


def shown_ranges(root):
    """{session_id: [(開始 or None, 終わり or None, 見せた時刻), ...]}。読めない項目は飛ばす。"""
    shown = load_state(os.path.join(root, ".kiro", "kiroku", "data")).get("shown_ranges")
    result = {}
    for session, ranges in (shown.items() if isinstance(shown, dict) else []):
        for item in ranges if isinstance(ranges, list) else []:
            try:
                start, end, at = item
                start = excerpt.parse_time(start) if start else None
                end = excerpt.parse_time(end) if end else None
                if not isinstance(at, (int, float)) or isinstance(at, bool):
                    continue
            except (TypeError, ValueError):
                continue
            result.setdefault(session, []).append((start, end, float(at)))
    return result


NOTE_TOLERANCE = 2   # 秒。ファイルの更新時刻の粒度（FAT などは2秒）


def note_state(path, moments, ranges):
    """ノートの状態。none／broken／fresh／stale／uncovered。

    fresh は、期間内の発話がすべて、ノートを書く前に要約表示で見せた範囲に入っているもの。
    stale は、見せていない発話が、すべてノートを書いた後のもの。
    uncovered は、ノートより前の発話に、見せた記録の無いものがあるもの（期間を広げた、
    範囲の記録が無い以前の版のノート、など）。読み直しの対象にする。
    """
    if not os.path.isfile(path):
        return "none"
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
            return "broken"
        written = os.path.getmtime(path)
    except (OSError, ValueError):
        return "broken"
    before = [(start, end) for start, end, at in ranges if at <= written + NOTE_TOLERANCE]
    unread = [m for m in moments
              if not any((start is None or m >= start) and (end is None or m < end) for start, end in before)]
    if not unread:
        return "fresh"
    if all(m.timestamp() > written for m in unread):
        return "stale"
    return "uncovered"


def legacy_note_state(path, latest):
    """版2までの既読の判定。ノートがあり、ノートを書いた後に発話が無ければ fresh。

    以前の版で作ったレポートを検査するとき、その版が数えた未読の数を再現するために使う。
    振り返りで読むセッションの判定には使わない（note_state を使う）。
    """
    if not os.path.isfile(path):
        return "none"
    return "stale" if latest and latest.timestamp() > os.path.getmtime(path) else "fresh"


def retro_marks(root):
    """記録フックが残した、振り返りの開始時刻。{session_id: 時刻}。"""
    marks = load_state(os.path.join(root, ".kiro", "kiroku", "data")).get("retro_sessions")
    result = {}
    for session, stamp in (marks.items() if isinstance(marks, dict) else []):
        moment = excerpt.parse_time(stamp)
        if moment:
            result[session] = moment
    return result


def retro_start(rows, mark=None):
    """このセッションで振り返りが始まった時刻。始まっていなければ None。

    /kiroku-retro の発話か、集約スクリプトの実行を目印にする。それ以降は振り返りの会話なので、
    開発の記録としては数えない。
    """
    moments = [mark] if mark else []
    for stamp, payload in rows or []:
        kind = payload.get("type")
        if kind == "user":
            hit = excerpt.content_text(payload).lstrip().startswith("/kiroku-retro")
        elif kind == "tool_call":
            command = (payload.get("args") or {}).get("command")
            hit = commands.own_script(command) == 'aggregate.py'
        else:
            hit = False
        moment = excerpt.parse_time(stamp) if hit else None
        if moment:
            moments.append(moment)
            break
    return min(moments) if moments else None


def retro_starts(root, session_ids):
    """記録に出たセッションについて、振り返りの開始時刻をまとめて求める。"""
    directory, marks, result = sessions_dir(root), retro_marks(root), {}
    for session in session_ids:
        rows = None
        path = excerpt.find_log(session, directory)
        if path:
            try:
                rows = excerpt.load(path)
            except (OSError, UnicodeError):
                rows = None
        start = retro_start(rows, marks.get(session))
        if start:
            result[session] = start
    return result


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
    # sessions_dir 自体がセッションのディレクトリである配置（excerpt.find_log が読める配置）も探す。
    for pattern in (os.path.join(base, "session.json"), os.path.join(base, "*", "session.json"),
                    os.path.join(base, "*", "*", "session.json")):
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
    marks = retro_marks(root)
    shown = shown_ranges(root)
    last_event = {}
    for event in events:
        if event.get("session_id"):
            last_event[event["session_id"]] = event["ts"]
    names = list(last_event) + [s for s in discover(root, directory) if s not in last_event]
    rows = []
    for session in names:
        recorded = session in last_event
        row = {"session_id": session, "agent": session_agent(root, session),
               "last_event": last_event.get(session), "recorded": recorded,
               "log": "ok", "prompts": 0, "prompt_chars": 0, "note": "none"}
        latest, moments = None, []
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
            repeated = duplicates(messages or [])
            cutoff = retro_start(messages, marks.get(session))
            for index, (stamp, payload) in enumerate(messages or []):
                moment = excerpt.parse_time(stamp)
                if payload.get("type") != "user" or moment is None or index in repeated:
                    continue
                if cutoff and moment >= cutoff:
                    continue
                text = _user_text(payload)
                if text and _in_period(moment, since, until):
                    row["prompts"] += 1
                    row["prompt_chars"] += len(text)
                    moments.append(moment)
                    latest = moment if latest is None or moment > latest else latest
            if messages is not None and issues:
                row["log"] = "partial_log"
        if not recorded and not row["prompts"]:
            continue  # ログだけのセッションは、期間内に発話があるものだけを載せる
        # ノートを書いた後に発話が増えた、または要約表示で見せていない発話があれば、読み直しが要る。
        note_path = os.path.join(notes_dir(root), session + ".json")
        row["note"] = note_state(note_path, moments, shown.get(session, []))
        row["note_v2"] = legacy_note_state(note_path, latest)   # 以前の版のレポートの検査に使う
        activity = [m for m in (excerpt.parse_time(row["last_event"]) if recorded else None, latest) if m]
        row["_activity"] = max(activity) if activity else None
        row["last_activity"] = row["_activity"].isoformat() if row["_activity"] else None
        rows.append(row)
    rows.sort(key=lambda r: r["_activity"].timestamp() if r["_activity"] else 0, reverse=True)
    for row in rows:
        del row["_activity"]
    return rows


SHELL_TOOLS = ("execute_bash", "execute_pwsh")
EXIT_CODE_LINE = re.compile(r"(?m)^Exit Code:\s*(-?\d+)\s*$")   # 応答の、行としての終了コード
OWN_HOOK_PREFIX = "kiroku-record-"


def _kiro_meta(payload):
    meta = payload.get("_meta")
    kiro = meta.get("kiro") if isinstance(meta, dict) else None
    return kiro if isinstance(kiro, dict) else {}


def hook_triggers(root):
    """フックの定義ファイルから、{フック名: トリガー（小文字）} を読む。

    プロジェクト内（.kiro/hooks/）と、ユーザー共通（~/.kiro/hooks/、KIRO_HOME があればその配下）。
    セッションログのフックの記録には、どのトリガーで動いたかが無い。実行を止められるのは
    実行前（PreToolUse）のフックだけなので、候補を絞るために定義から引く。
    """
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    found = {}
    for directory in (os.path.join(home, "hooks"), os.path.join(root, ".kiro", "hooks")):
        for path in sorted(glob.glob(os.path.join(glob.escape(directory), "*.json"))):
            try:
                with open(path, encoding="utf-8-sig") as handle:
                    hooks = json.load(handle).get("hooks")
            except (OSError, ValueError, AttributeError):
                continue
            for hook in hooks if isinstance(hooks, list) else []:
                if isinstance(hook, dict) and isinstance(hook.get("name"), str) \
                        and isinstance(hook.get("trigger"), str):
                    found[hook["name"]] = hook["trigger"].lower()
    return found


def unrecorded_calls(root, session_ids, since=None, until=None):
    """実行後のフックが呼ばれなかったシェル実行を、セッションログから拾う。

    別のフックが実行前に止めた呼び出しと、実行中に中断された呼び出しでは、PostToolUse が
    呼ばれず、events.jsonl に何も残らない。ログには残るので、集約のときにここで数える。

    拾うのは、次をすべて満たすものだけ（Kiro CLI 3.3.0 で実測した形。verification.md）。
      - シェル実行の tool_call で、状態が denied または failed
      - 対応する tool_result の本文に Exit Code の行が無い（あるものは PostToolUse で記録済み）
      - 権限の規則による拒否（policyDenial）ではない
      - 承認画面での取り消し・拒否（interaction_resolved の cancelled／reject）ではない
    止めたフックと理由はログに無い。同じターンでそれまでに動いたフックの名前を、候補として添える。
    今の定義で実行前（PreToolUse）以外と分かるフックは、実行を止められないので候補から外す。
    定義が見つからないフックは、外さずに残す。
    ログの形式は文書化されていない。完了していないシェル実行のうち、結果の行が無いもの、
    本文が文字列でないもの、知らない状態のものは、止められたとも中断とも決めず、
    判定できなかった呼び出しとして別に返す（集約は「記録の不足」に出す）。
    戻り値は (拾った呼び出し, 判定できなかった呼び出し)。
    """
    directory, marks, found, unknown = sessions_dir(root), retro_marks(root), [], []
    triggers = hook_triggers(root)
    for session in session_ids:
        path = excerpt.find_log(session, directory)
        if not path:
            continue
        try:
            # 期間の始まりより前から更新されていないログには、期間内の呼び出しは無い。
            if since and os.path.getmtime(path) < since.timestamp():
                continue
            rows = excerpt.load(path, types=None)
        except (OSError, UnicodeError):
            continue
        cutoff = retro_start([r for r in rows if r[1].get("type") in excerpt.SHOWN_TYPES],
                             marks.get(session))
        results, resolved = {}, {}
        for _, payload in rows:
            call_id = payload.get("toolCallId")
            if not isinstance(call_id, str):
                continue
            if payload.get("type") == "tool_result":
                results[call_id] = payload
            elif payload.get("type") == "interaction_resolved":
                resolved.setdefault(call_id, []).append(payload)
        hooks = collections.defaultdict(list)   # ターンごとの、それまでに動いたフックの名前
        for stamp, payload in rows:
            kind = payload.get("type")
            if kind == "ContextualHookInvoked":
                name = payload.get("name")
                if isinstance(name, str) and not name.startswith(OWN_HOOK_PREFIX) \
                        and triggers.get(name, "pretooluse") == "pretooluse" \
                        and name not in hooks[payload.get("turnId")]:
                    hooks[payload.get("turnId")].append(name)
                continue
            if kind != "tool_call" or payload.get("toolName") not in SHELL_TOOLS:
                continue
            status, call_id = payload.get("status"), payload.get("toolCallId")
            if status == "completed":
                continue
            moment = excerpt.parse_time(stamp)
            if moment is None or not _in_period(moment, since, until) or (cutoff and moment >= cutoff):
                continue
            command = (payload.get("args") or {}).get("command") if isinstance(payload.get("args"), dict) else None
            result = results.get(call_id)
            content = result.get("content") if result else None
            if not isinstance(command, str) or result is None or not isinstance(content, str):
                unknown.append({"session_id": session, "ts": moment.isoformat(),
                                "tool": payload.get("toolName")})
                continue
            if EXIT_CODE_LINE.search(content):
                continue   # 実行後のフックで記録済み
            if "policyDenial" in _kiro_meta(payload):
                continue
            if any(item.get("outcome") == "cancelled" or item.get("selectedOption") == "reject"
                   for item in resolved.get(call_id, [])):
                continue
            if status not in ("denied", "failed") or result.get("success") is not False:
                unknown.append({"session_id": session, "ts": moment.isoformat(),
                                "tool": payload.get("toolName")})
                continue
            found.append({"session_id": session, "ts": moment.isoformat(),
                          "kind": "blocked" if status == "denied" else "interrupted",
                          "command": mask(command),
                          "hooks": list(hooks[payload.get("turnId")]) if status == "denied" else []})
    return found, unknown


def shell_calls(root, session_ids, since=None):
    """セッションログに残ったシェル実行。[(時刻, コマンド)]。振り返りの後の実行と、kiroku 自身のスクリプトは除く。

    書き込みツールを使わずにシェルでファイルを書き換えた実行（ヒアドキュメント、sed、整形コマンド）は、
    書き込みの記録に残らない。手直し候補の手がかりにするため、コマンドの文面だけを返す。
    """
    directory, marks, found = sessions_dir(root), retro_marks(root), []
    for session in session_ids:
        path = excerpt.find_log(session, directory)
        if not path:
            continue
        try:
            if since and os.path.getmtime(path) < since.timestamp():
                continue   # 期間の始まりより前から更新されていないログには、その後の実行は無い
            rows = excerpt.load(path)
        except (OSError, UnicodeError):
            continue
        cutoff = retro_start(rows, marks.get(session))
        for stamp, payload in rows:
            if payload.get("type") != "tool_call" or payload.get("toolName") not in SHELL_TOOLS:
                continue
            args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
            command = args.get("command")
            moment = excerpt.parse_time(stamp)
            if not isinstance(command, str) or moment is None or (cutoff and moment >= cutoff) \
                    or commands.own_script(command):
                continue
            found.append((moment, command))
    return found


def _piped_runner(command, config):
    """パイプや連結の手前のコマンドが、テストの記録設定の起動先に一致するか。"""
    head = commands.invocation(command, through_pipes=True)
    if not head:
        return False
    for entry in config.get("tests") or []:
        try:
            if isinstance(entry, dict) and re.search(entry.get("match", "(?!)"), head):
                return True
        except re.error:
            continue
    return False


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
    シェル実行は、終了コードが 0 以外のコマンド（見出しにまとめたもの）と、テストの記録設定に一致した
    実行の終了コードを添える。試行錯誤や、確かめずに終えたターンを、抜粋を開かずに見つけるため。
    """
    result = {"session_id": session, "agent": session_agent(root, session),
              "status": "ok", "issues": [], "turns": []}
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
    repeated = duplicates(rows)
    cutoff = retro_start(rows, retro_marks(root).get(session))
    by_index = {}
    config = load_config(root)
    shells = {}   # 結果を待っているシェル実行。toolCallId -> コマンド
    for index, (stamp, payload) in enumerate(rows):
        kind = payload.get("type")
        if kind == "user":
            if index in repeated:
                # 二重に記録された発話。最初の1件に寄せ、続きの応答もそちらに付ける。
                if repeated[index] in by_index:
                    by_index[repeated[index]]["duplicates"].append(index)
                continue
            turn = None
            moment = excerpt.parse_time(stamp)
            text = _user_text(payload)
            if moment is None or text is None or not _in_period(moment, since, until):
                continue
            if cutoff and moment >= cutoff:
                continue
            turn = {"index": index, "ts": stamp, "chars": len(text),
                    "text": _clip(mask(text), user_chars), "duplicates": [],
                    "tools": collections.Counter(), "tool_failures": 0, "reply": None,
                    "failed_commands": collections.Counter(), "tests": [], "tests_not_run": 0}
            by_index[index] = turn
            result["turns"].append(turn)
        elif turn is None:
            continue
        elif kind == "tool_call":
            turn["tools"][payload.get("toolName") or "unknown"] += 1
            args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
            if payload.get("toolName") in SHELL_TOOLS and isinstance(args.get("command"), str):
                shells[payload.get("toolCallId")] = args["command"]
        elif kind == "tool_result":
            if payload.get("success") is False:
                turn["tool_failures"] += 1
            command = shells.pop(payload.get("toolCallId"), None)
            found = EXIT_CODE_LINE.search(payload.get("content")) \
                if command is not None and isinstance(payload.get("content"), str) else None
            if found:
                code = int(found.group(1))
                entry, how = commands.test_match(command, config)
                if entry is not None and code == -1:
                    turn["tests_not_run"] += 1   # 承認されない、止められた、など。実行には数えない
                elif entry is not None:
                    if how == "text" and _piped_runner(command, config):
                        how = "pipe"   # パイプの手前が起動先。終了コードはパイプの後ろのもの
                    turn["tests"].append({"exit_code": code, "matched": how})
                elif code not in (0, -1):
                    turn["failed_commands"][commands.key(mask(command))] += 1
        elif kind == "assistant":
            text = excerpt.content_text(payload)
            # 実機のログでは、各ターンの最後に "..." だけの応答が入る。文字のある応答だけを採る。
            if any(ch.isalnum() for ch in text):
                flat = " ".join(mask(text).split())
                turn["reply"] = {"index": index, "chars": len(text),
                                 "text": flat[:assistant_chars] + ("…" if len(flat) > assistant_chars else "")}
    for turn in result["turns"]:
        turn["tools"] = dict(turn["tools"])
        turn["failed_commands"] = dict(turn["failed_commands"].most_common())
    return result


def page(root, events, since=None, until=None, offset=0, size=20, chunk_chars=2000, session_ids=None):
    """全発話を全文でページ表示する。

    session_ids を渡すと、そのセッションを対象にする（集約のセッション一覧と同じ集合。
    フックの記録が無く、ログだけがあるセッションも含む）。渡さなければ、記録に出たセッション。
    """
    if offset < 0 or size < 1 or chunk_chars < 1:
        raise ValueError("offset は0以上、size/chunk_chars は1以上が必要です")
    directory = sessions_dir(root)
    chunks, coverage = [], []
    if session_ids is None:
        session_ids = {e["session_id"] for e in events if e.get("session_id")}
    session_ids = sorted(set(session_ids))
    marks = retro_marks(root)
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
        repeated = duplicates(rows)
        cutoff = retro_start(rows, marks.get(session))
        for index, (stamp, payload) in enumerate(rows):
            if payload.get("type") != "user" or index in repeated:
                continue
            moment = excerpt.parse_time(stamp)
            if moment is None:
                status["issues"].append("unknown_timestamp:index_%d" % index)
                continue
            if (since and moment < since) or (until and moment >= until):
                continue
            if cutoff and moment >= cutoff:
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
