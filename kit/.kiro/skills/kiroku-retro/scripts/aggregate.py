#!/usr/bin/env python3
"""kiroku の集約。.kiro/kiroku/data/events.jsonl を読み、後計算で分類して要約を出す。

使い方:
  python3 .kiro/skills/kiroku-retro/scripts/aggregate.py [--since 7d] [--until 2026-12-01] [--by week] [--json]
  python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --section tests
  python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --session <session_id>

出力の長さ:
  全体の出力は、設定の retro_output_chars（既定 20000 文字）に収まるように、各一覧を上位だけに絞る。
  省いた分は、その一覧の末尾に件数を出す。--section <名前> で、その節だけを全件出す。

テスト失敗の分類（記録時ではなく、ここで計算する）:
  Red 候補    前回の全テスト成功より後に追加されたテスト、
              または前回の全テスト成功以降にテストファイルが変更されたテストの失敗
  回帰        前回の全テスト成功の時点で存在し、通っていて、テストファイルが変わっていないテストの失敗
  マージ中    回帰と同じ条件で、merge や rebase の最中（コンフリクトの解消中など）に起きた失敗
  その他      基準になる全テスト成功が無い、通った記録が無い、git から状態を復元できない、など

会話:
  集約結果のセッション一覧で、発話数と振り返りノートの有無を示す。
  二重に記録された発話は1件として数える。振り返りを始めた後の発話と記録は、開発の記録から除く。
  --session で1セッションを要約表示する。ユーザー発話は原文、ツール実行は件数、応答は冒頭だけ。
  全文が要る箇所は excerpt.py で前後を読む。--prompts は全発話を全文でページ表示する予備。
  ログが欠落したセッションでは会話に基づく判断を保留する。

書き直し候補:
  発話タイミングをまたぐファイルの書き直し。本文は保存せず時刻でログと照合する。

人間の手直し候補:
  エージェントが最後に書いた内容と、いまの内容が違うファイル。
  merge、pull、rebase、checkout、reset などによる変更は除く。
"""
import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True  # 導入先に __pycache__ を作らない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompts  # noqa: E402
import harness  # noqa: E402
import notes  # noqa: E402
import ledger  # noqa: E402
import excerpt  # noqa: E402
import commands  # noqa: E402
import reportcheck  # noqa: E402
from record import load_config, mask, not_run_kind  # noqa: E402

RED, REGRESSION, MERGE, OTHER = "red", "regression", "merge", "other"
LABELS = {RED: "Red 候補", REGRESSION: "回帰候補", MERGE: "マージ中の失敗", OTHER: "その他"}
TOOL_KINDS = (("tool_failure", "tool", "ツール失敗"), ("tool_cancelled", "cancelled", "人による取り消し"),
              ("tool_timeout", "timeout", "タイムアウト"), ("tool_denied", "denied", "権限の規則による拒否"))
SECTIONS = ("plan", "review", "summary", "tests", "tools", "files", "harness", "sessions", "facts")
OUTPUT_CAPS = (12, 6, 3)  # 一覧1つあたりの件数。出力が長すぎるときは、順に小さくする
VCS_ACTIONS = ("merge", "pull", "rebase", "checkout", "reset", "cherry-pick", "revert",
               "commit (merge)", "am", "switch", "restore")


class Repo:
    """集約に必要な git の読み取りだけを行う。"""

    def __init__(self, root):
        self.root = root
        self._blob = {}
        self._diff = {}
        self._moves = None

    def git(self, *args, stdin=None):
        try:
            done = subprocess.run(["git", "-C", self.root] + list(args), input=stdin,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def blob(self, commit, path):
        """あるコミットでのファイル内容のハッシュ。無ければ None。"""
        key = (commit, path)
        if key not in self._blob:
            out = self.git("rev-parse", "--verify", "--quiet", "%s:%s" % (commit, path))
            self._blob[key] = out.strip() if out else None
        return self._blob[key]

    def diff_names(self, old, new):
        """2つのコミットの間で変わったパス。復元できなければ None。"""
        if old == new:
            return set()
        key = (old, new)
        if key not in self._diff:
            out = self.git("diff", "--name-only", "-z", old, new)
            self._diff[key] = None if out is None else {p for p in out.split("\0") if p}
        return self._diff[key]

    def current_hashes(self, paths):
        existing = [p for p in paths if os.path.isfile(os.path.join(self.root, p))]
        hashes = {p: None for p in paths}
        if existing:
            out = self.git("hash-object", "--stdin-paths", stdin="\n".join(existing) + "\n")
            if out is not None:
                hashes.update(zip(existing, out.split()))
        return hashes

    def head_moves(self):
        """HEAD の移動の履歴。(時刻, 種別, 移動前, 移動後) の一覧。"""
        if self._moves is None:
            self._moves = self._head_moves()
        return self._moves

    def _head_moves(self):
        out = self.git("reflog", "show", "--date=unix", "--format=%H%x09%gd%x09%gs", "HEAD")
        if not out:
            return []
        rows = []
        for line in out.splitlines():
            parts = line.split("\t", 2)
            found = re.search(r"\{(\d+)\}", parts[1]) if len(parts) == 3 else None
            if found:
                rows.append((int(found.group(1)), parts[2], parts[0]))
        moves = []
        for (when, subject, new), (_, _, old) in zip(rows, rows[1:]):
            moves.append((when, subject, old, new))
        return moves


def parse_time(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def parse_bound(text, now):
    """`14d` のような日数、または日付・日時を解釈する。"""
    found = re.fullmatch(r"(\d+)d", text)
    if found:
        return now - datetime.timedelta(days=int(found.group(1)))
    value = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)


def load_events(path):
    events = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                    event["_time"] = parse_time(event["ts"])
                    if event.get("type") == "tool_denied":
                        # 古い版は、終了コード -1 のうち取り消し以外をすべて拒否として記録していた。
                        event["type"] = not_run_kind(event.get("output_tail"))
                        if event["type"] == "recording_gap":
                            event["reason"] = "unknown_not_run"
                    events.append(event)
                except (ValueError, KeyError, TypeError):
                    continue  # 壊れた行は飛ばす
    except OSError:
        pass
    events.sort(key=lambda e: e["_time"])
    return events


def is_green(run):
    results = run.get("results")
    return bool(results) and run.get("exit_code") == 0 and bool(results.get("passed")) \
        and not results.get("failed") and not results.get("error")


def all_ids(run):
    results = run.get("results") or {}
    return set().union(*(results.get(k, []) for k in ("passed", "failed", "error", "skipped")))


def file_state(repo, run, path):
    """テスト実行の時点でのファイル内容のハッシュ。"""
    dirty = run.get("dirty") or {}
    if path in dirty:
        return dirty[path]
    return repo.blob(run["head"], path) if run.get("head") else None


def changed_between(repo, base, run):
    """2つのテスト実行の間で内容が変わったパス。復元できなければ None。"""
    if base.get("dirty") is None or run.get("dirty") is None:
        return None
    if base.get("dirty_truncated") or run.get("dirty_truncated"):
        return None
    candidates = set(base["dirty"]) | set(run["dirty"])
    if base.get("head") != run.get("head"):
        if not base.get("head") or not run.get("head"):
            return None
        names = repo.diff_names(base["head"], run["head"])
        if names is None:
            return None
        candidates |= names
    return {p for p in candidates if file_state(repo, base, p) != file_state(repo, run, p)}


def test_file_changed(test_id, known_file, changed):
    """テストのファイルが変更一覧に含まれるか。分からなければ None。"""
    if known_file:
        return known_file in changed
    classname = test_id.split("::", 1)[0] if "::" in test_id else ""
    tokens = [t for t in re.split(r"[./\\]", classname) if t]
    if not tokens:
        return None
    for path in changed:
        stem = os.path.splitext(path)[0]
        for size in range(len(tokens), 0, -1):
            prefix = "/".join(tokens[:size])
            if stem == prefix or stem.endswith("/" + prefix):
                return True
    return False


def merge_basis(repo, run):
    """merge や rebase の最中の実行なら、その根拠を返す。そうでなければ None。"""
    if run.get("vcs"):
        return "%s の最中の失敗（コンフリクトの解消中など）" % run["vcs"]
    when = int(run["_time"].timestamp())
    for moved, subject, old, _ in repo.head_moves():
        # 記録に印が無い古い実行。手で作ったマージコミットが、同じコミットの上に後からできていれば、最中とみる。
        if moved >= when and subject.startswith("commit (merge)") and old == run.get("head"):
            return "merge の最中の失敗と推定（この実行の後に、同じコミットからマージコミットが作られた）"
    return None


def merged_between(repo, base, run):
    """2つのテスト実行の間に取り込まれた変更の種別（merge、pull、rebase）。"""
    start, end = int(base["_time"].timestamp()), int(run["_time"].timestamp())
    return sorted({subject.split(":", 1)[0].split(" ", 1)[0] for moved, subject, _, _ in repo.head_moves()
                   if start <= moved <= end and subject.startswith(("merge", "pull", "rebase"))})


def classify_failures(repo, runs):
    """全期間のテスト実行を時刻順に見て、失敗を1件ずつ分類する。"""
    failures = []
    green = None          # 直近の全テスト成功
    first_seen = {}       # テストID -> 初めて現れた実行の番号
    passed_before = set()
    green_index = -1
    change_cache = {}
    for index, run in enumerate(runs):
        results = run.get("results")
        if results:
            for test_id in results.get("failed", []) + results.get("error", []):
                known_file = (run.get("test_files") or {}).get(test_id)
                if green is None:
                    kind, basis = OTHER, "基準になる全テスト成功の記録が無い"
                elif test_id not in first_seen or first_seen[test_id] > green_index:
                    kind, basis = RED, "前回の全テスト成功より後に追加されたテスト"
                elif test_id not in passed_before:
                    kind, basis = OTHER, "通った記録が無い"
                else:
                    if index not in change_cache:
                        change_cache[index] = changed_between(repo, green, run)
                    changed = change_cache[index]
                    if changed is None:
                        kind, basis = OTHER, "git から当時の状態を復元できない"
                    else:
                        verdict = test_file_changed(test_id, known_file, changed)
                        if verdict:
                            kind, basis = RED, "前回の全テスト成功以降にテストファイルが変更された"
                        else:
                            kind = REGRESSION
                            basis = "通っていたテストの失敗（テストファイルは%s）" % (
                                "特定できず" if verdict is None else "変更なし")
                            merging = merge_basis(repo, run)
                            if merging:
                                kind, basis = MERGE, merging
                            else:
                                merged = merged_between(repo, green, run)
                                if merged:
                                    basis += "。前回の全テスト成功の後に %s あり" % "、".join(merged)
                failures.append({
                    "test_id": test_id, "class": kind, "basis": basis, "ts": run["ts"],
                    "_time": run["_time"], "session_id": run.get("session_id"),
                    "command": run.get("command"),
                })
            for test_id in all_ids(run):
                first_seen.setdefault(test_id, index)
            passed_before |= set(results.get("passed", []))
        if is_green(run):
            green, green_index = run, index
    return failures


def fix_candidates(repo, writes):
    """エージェントが最後に書いた内容と、いまの内容が違うファイルを探す。"""
    last = {}
    for event in writes:
        last[event["path"]] = event
    current = repo.current_hashes(sorted(last))
    moves = repo.head_moves()
    candidates, excluded = [], []
    for path, event in sorted(last.items()):
        if current[path] == event.get("hash"):
            continue
        written = int(event["_time"].timestamp())
        cause = None
        for when, subject, old, new in moves:
            if when < written or not subject.startswith(VCS_ACTIONS):
                continue
            names = repo.diff_names(old, new)
            if names is None or path in names:
                cause = subject.split(":", 1)[0]
                break
        row = {"path": path, "written_at": event["ts"], "session_id": event.get("session_id"),
               "status": "削除" if current[path] is None else "変更"}
        if cause:
            row["cause"] = cause
            excluded.append(row)
        else:
            candidates.append(row)
    return candidates, excluded


def shorten(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def rework_candidates(events):
    """同じセッションで、複数の発話にまたがって書き直されたファイルを探す。"""
    turn = {}          # session_id -> いま何番目の発話か
    prompt_of = {}     # (session_id, 発話の番号) -> 発話
    touched = {}       # (session_id, path) -> 書き込みがあった発話の番号
    for event in events:
        session = event.get("session_id")
        if event.get("type") == "prompt":
            turn[session] = turn.get(session, 0) + 1
            prompt_of[(session, turn[session])] = event
        elif event.get("type") == "write" and turn.get(session):
            turns = touched.setdefault((session, event["path"]), [])
            if turn[session] not in turns:
                turns.append(turn[session])
    rows = []
    for (session, path), turns in touched.items():
        if len(turns) < 2:
            continue
        rows.append({
            "path": path, "session_id": session, "turns": len(turns),
            "prompts": [{"ts": prompt_of[(session, n)]["ts"]}
                        for n in turns[1:]],
        })
    return sorted(rows, key=lambda r: (-r["turns"], r["path"]))


def command_key(command):
    """作業場所の指定、リダイレクト、パイプ以降を除いた実コマンド。解釈できなければ先頭の3語。"""
    return commands.key(mask(command or ""))


def period_key(moment, by):
    local = moment.astimezone()
    if by == "day":
        return local.strftime("%Y-%m-%d")
    if by == "week":
        monday = local.date() - datetime.timedelta(days=local.weekday())
        return "%s の週" % monday.isoformat()
    return "全期間"


def aggregate(root, since=None, until=None, by="week", with_prompts=False,
              prompt_offset=0, prompt_limit=20):
    repo = Repo(root)
    data_dir = os.path.join(root, ".kiro", "kiroku", "data")
    events = load_events(os.path.join(data_dir, "events.jsonl"))
    # 振り返りを始めた後の記録は、振り返りの会話そのものなので、開発の記録から除く。
    retro = prompts.retro_starts(root, sorted({e["session_id"] for e in events if e.get("session_id")}))
    recorded_total = len(events)
    events = [e for e in events if not (e.get("session_id") in retro and e["_time"] >= retro[e["session_id"]])]
    retro_excluded = recorded_total - len(events)
    runs = [e for e in events if e.get("type") == "test_run"]
    failures = classify_failures(repo, runs)  # 基準が要るので、分類は全期間で行う

    def inside(item):
        return (since is None or item["_time"] >= since) and (until is None or item["_time"] < until)

    all_events, all_failures = events, failures
    findings, note_problems = notes.verify(root)

    def occurrences(target):
        """台帳の「対象の型」から、発生時刻の一覧を返す。数えられない型は None。"""
        kind, _, name = (target or "").partition(":")
        if kind == "topic":
            return [f["_time"] for f in findings
                    if f["status"] == "verified" and f["topic"] == name and f["_time"]]
        if kind in ("tool", "denied", "cancelled", "timeout"):
            wanted = {short: name for name, short, _ in TOOL_KINDS}[kind]
            matching = [e for e in all_events if e.get("type") == wanted]
            exact = [e['_time'] for e in matching if command_key(e.get('command')) == name]
            if exact:
                return exact
            # 旧版の先頭3語キーは、一意に対応するときだけ引き継ぐ。
            legacy = [e for e in matching if ' '.join((e.get('command') or '').split()[:3])[:60] == name]
            if len({command_key(e.get('command')) for e in legacy}) > 1:
                return None
            return [e['_time'] for e in legacy]
        if kind == "test":
            return [f["_time"] for f in all_failures if f["class"] == REGRESSION and f["test_id"] == name]
        return None

    starts = [e["_time"] for e in all_events[:1]] + [f["_time"] for f in findings if f["_time"]]
    adopted, rejected = ledger.tracking(root, occurrences, min(starts) if starts else None,
                                        datetime.datetime.now(datetime.timezone.utc))
    pending = []
    for proposal in ledger.pending(root):
        times = occurrences(proposal["target"]) if proposal["target"] else None
        recent = None if times is None else sum(
            1 for t in times if (since is None or t >= since) and (until is None or t < until))
        pending.append(dict(proposal, recent=recent))

    events = [e for e in events if inside(e)]
    failures = [f for f in failures if inside(f)]
    candidates, excluded = fix_candidates(repo, [e for e in events if e.get("type") == "write"])

    periods = collections.OrderedDict()
    columns = ("sessions", "test_runs", RED, REGRESSION, MERGE, OTHER, "tool_failures", "cancelled",
               "timeouts", "denied", "writes", "prompts")
    last_writes, turns = {}, collections.Counter()

    def bucket(moment):
        return periods.setdefault(period_key(moment, by), {c: 0 for c in columns} | {"_s": set()})

    for event in events:
        row = bucket(event["_time"])
        row["_s"].add(event.get("session_id"))
        kind = event.get("type")
        if kind == "test_run":
            # 文面だけで一致し、結果も取り込めなかったものは、実行か言及か分からないので数えない。
            if event.get("matched") != "text" or event.get("results") is not None:
                row["test_runs"] += 1
        elif kind == "prompt":
            row["prompts"] += 1
            turns[event.get("session_id")] += 1
        elif kind == "tool_failure":
            row["tool_failures"] += 1
        elif kind == "tool_cancelled":
            row["cancelled"] += 1
        elif kind == "tool_timeout":
            row["timeouts"] += 1
        elif kind == "tool_denied":
            row["denied"] += 1
        elif kind == "write":
            # 1回の書き込みは PostFileSave と PostToolUse の両方で記録されるので、まとめて数える。
            session = event.get("session_id")
            key = (session, event.get("path"))
            version = (event.get("hash"), turns[session], period_key(event["_time"], by))
            if key not in last_writes or last_writes[key] != version:
                last_writes[key] = version
                row["writes"] += 1
    for failure in failures:
        bucket(failure["_time"])[failure["class"]] += 1
    for row in periods.values():
        row["sessions"] = len(row.pop("_s"))

    grouped_tests = {}
    for failure in failures:
        entry = grouped_tests.setdefault((failure["class"], failure["test_id"]), {
            "class": failure["class"], "test_id": failure["test_id"], "count": 0,
            "sessions": set(), "basis": failure["basis"]})
        entry["count"] += 1
        entry["sessions"].add(failure["session_id"])
        entry.update(last_ts=failure["ts"], last_session=failure["session_id"],
                     command=failure["command"])

    grouped_tools = {}
    for event in events:
        if event.get("type") not in [name for name, _, _ in TOOL_KINDS]:
            continue
        entry = grouped_tools.setdefault((event["type"], command_key(event.get("command"))), {
            "type": event["type"], "command_key": command_key(event.get("command")),
            "count": 0, "sessions": set(), "exit_codes": set()})
        entry["count"] += 1
        entry["sessions"].add(event.get("session_id"))
        if event.get("exit_code") is not None:
            entry["exit_codes"].add(event["exit_code"])
        entry.update(last_ts=event["ts"], last_session=event.get("session_id"),
                     last_command=event.get("command"),
                     last_output_tail=(event.get("output_tail") or "")[-400:])

    def finish(entries):
        rows = []
        for entry in entries:
            entry["sessions"] = len(entry["sessions"])
            if "exit_codes" in entry:
                entry["exit_codes"] = sorted(entry["exit_codes"])
            rows.append(entry)
        return sorted(rows, key=lambda r: (-r["count"], r.get("test_id") or r.get("command_key")))

    session_rows = prompts.sessions(root, events, since, until)
    first = all_events[0]["_time"] if all_events else None
    log_only = [s for s in session_rows if not s["recorded"]]
    before = sum(1 for s in log_only if first and s["last_activity"]
                 and parse_time(s["last_activity"]) < first)
    try:
        with open(os.path.join(data_dir, "record-errors.log"), encoding="utf-8", errors="replace") as handle:
            record_errors = sum(1 for _ in handle)
    except OSError:
        record_errors = 0
    coverage = {"sessions": len(session_rows), "recorded": len(session_rows) - len(log_only),
                "log_only": len(log_only), "first_event": first.isoformat() if first else None,
                "before_first": before, "after_first": len(log_only) - before if first else None,
                "record_errors": record_errors, "retro_sessions": len(retro),
                "retro_excluded": retro_excluded}
    result = {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "event_count": len(events),
        "periods": periods,
        "test_failures": finish(grouped_tests.values()),
        "tool_failures": finish(grouped_tools.values()),
        "coverage": coverage,
        "recording_gaps": [{"session_id": e.get("session_id"), "ts": e["ts"],
                            "reason": e.get("reason"), "tool": e.get("tool")}
                           for e in events if e.get("type") == "recording_gap"],
        "test_result_gaps": [{"session_id": e.get("session_id"), "ts": e["ts"],
                              "command": e.get("command"), "reason": e.get("note") or 'unknown',
                              "matched": e.get("matched") or 'invocation'}
                             for e in events if e.get("type") == "test_run" and e.get("results") is None],
        "test_configuration": [{k: mask(t.get(k)) for k in ('match', 'report', 'format')}
                               for t in load_config(root)['tests']],
        "execution_context": prompts.execution_context(root),
        "rework_candidates": rework_candidates(events),
        "prompt_page": prompts.page(root, events, since, until, prompt_offset, prompt_limit)
        if with_prompts else None,
        "harness": harness.inventory(root),
        "steering": harness.steering_weight(root),
        "topics": notes.topics(findings, since, until),
        "note_problems": note_problems,
        "adopted": adopted,
        "pending": pending,
        "rejected": rejected,
        "sessions": session_rows,
        "reading_plan": reading_plan(session_rows, load_config(root)["retro_read_chars"]),
        "fix_candidates": candidates,
        "fix_excluded": excluded,
    }
    result['report_facts'] = reportcheck.facts(result)
    return result


def capped(rows, cap, section):
    """一覧を上位 cap 件に絞る。省いた件数の注記を添えて返す。"""
    if cap is None or len(rows) <= cap:
        return rows, []
    return rows[:cap], ["- ほか %d 件。全件は `--section %s` で出します。" % (len(rows) - cap, section)]


def render(data, show_prompts=False, section=None, cap=None):
    """集約をテキストにする。section を指定すると、その節だけを全件出す。"""
    if section == 'facts':
        return '# レポート用の検証値\n\n```kiroku-facts\n' + json.dumps(
            data['report_facts'], ensure_ascii=False, indent=2) + '\n```\n'
    def want(name):
        return section in (None, name)

    lines = ["# kiroku 集約", ""]
    lines.append("- 期間：%s 〜 %s" % (data["since"] or "最初", data["until"] or "現在"))
    lines.append("- イベント数：%d" % data["event_count"])
    context = data['execution_context']
    lines.append('- 実行エージェント：%s（%s）' % (context['agent'] or '未確認', context['status']))
    lines.append('- レポートの検証値は `--section facts`、保存後の照合は `--check-report <パス>` で確認します。')
    lines.append("- レポートの保存先：.kiro/kiroku/data/reports/%s.md"
                 % datetime.datetime.now().strftime("%Y-%m-%d-%H%M"))
    if section is None and cap is not None:
        lines.append("- 各一覧は上位だけを出しています。節ごとの全件は `--section <%s>` で出します。"
                     % "|".join(SECTIONS))
    if want("plan"):
        lines += render_plan(data["reading_plan"])
    if want("review"):
        lines += render_review(data, cap)
    if want("summary"):
        lines += render_summary(data, cap)
    if data["event_count"]:
        if want("tests"):
            lines += render_tests(data, cap)
        if want("tools"):
            lines += render_tools(data, cap)
        if want("files"):
            lines += render_files(data, cap)
    if want("harness"):
        lines += render_harness(data, cap)
    if want("sessions"):
        lines += render_sessions(data["sessions"], None if cap is None else cap * 3)
    page = data.get("prompt_page")
    if page is not None:
        lines += ["", "## ユーザー発話（ログから取得・本文の保存なし）", ""]
        for entry in page["coverage"]:
            lines.append("- session `%s`: %s、対象発話 %d 件" % (
                entry["session_id"], entry["status"], entry["prompts"]))
            if entry["status"] != "ok":
                lines.append("  - 会話に基づく判断を保留：%s" % ", ".join(entry["issues"]))
        lines.append("- 分割数 %d、今回の開始位置 %d" % (page["total_chunks"], page["offset"]))
        for chunk in page["chunks"]:
            lines += ["", "### session `%s` index=%d [%s] 部分 %d/%d" % (
                chunk["session_id"], chunk["index"], chunk["ts"], chunk["part"], chunk["parts"]),
                "", chunk["text"]]
        if page["next_offset"] is not None:
            lines += ["", "続きあり：同じ期間で --prompts --prompt-offset %d を指定してください。"
                      % page["next_offset"]]
        else:
            lines += ["", "ユーザー発話の最終ページです。"]
    return "\n".join(lines) + "\n"


def render_summary(data, cap):
    lines = []
    if not data["event_count"]:
        lines += ["", "この期間の記録はありません。"]
    else:
        lines += ["", "## 期間ごとの要約", "",
                  "| 期間 | セッション | 発話 | テスト実行 | Red 候補 | 回帰候補 | マージ中 | その他 | ツール失敗 "
                  "| 取り消し | タイムアウト | 拒否 | 書き込み |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, row in data["periods"].items():
            lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d |" % (
                name, row["sessions"], row["prompts"], row["test_runs"], row[RED], row[REGRESSION],
                row[MERGE], row[OTHER], row["tool_failures"], row["cancelled"], row["timeouts"],
                row["denied"], row["writes"]))

    cover = data["coverage"]
    lines += ["", "## 記録の範囲", ""]
    lines.append("- セッション %d 件のうち、記録（フックによるイベント）があるもの %d 件、ログのみ %d 件。" % (
        cover["sessions"], cover["recorded"], cover["log_only"]))
    if cover["log_only"]:
        lines.append("  - テスト失敗、ツール失敗、書き込みの数は、記録がある %d 件だけが対象です。"
                     "ログのみのセッションで何も起きなかったという意味ではありません。" % cover["recorded"])
        if cover["first_event"]:
            lines.append("  - 現在残っている最古の記録は %s。ログのみのうち、それより前のもの %d 件（記録が無い原因は未確認）、"
                         "後のもの %d 件（フックが動かなかった可能性。原因は未確認）。" % (
                             cover["first_event"], cover["before_first"], cover["after_first"]))
        else:
            lines.append("  - 記録が1件もありません。フックが読み込まれているかを確かめてください。")
    if cover["record_errors"]:
        lines.append("- 記録処理のエラー（data/record-errors.log）：%d 行" % cover["record_errors"])
    if cover["retro_sessions"]:
        lines.append("- 振り返りを始めた後の発話と記録は除いています（%d セッション、イベント %d 件）。" % (
            cover["retro_sessions"], cover["retro_excluded"]))

    if not any(p['test_runs'] for p in data['periods'].values()):
        lines.append('- テスト実行の記録が0件です。テストが成功した証拠ではありません。実行コマンド・検出設定・JUnit 出力を照合してください。')
    if data['test_result_gaps']:
        lines += ['', '## テスト結果の記録不足（%d 件）' % len(data['test_result_gaps']), '']
        rows, more = capped(data['test_result_gaps'], cap, 'summary')
        for row in rows:
            lines.append('- %s session `%s`: %s%s。コマンド `%s`' % (
                row['ts'], row['session_id'], row['reason'],
                '（文面で一致。テストの実行か、名前への言及かは未確認）' if row['matched'] == 'text' else '',
                shorten(row['command'], 200)))
        lines += more

    if data["recording_gaps"]:
        lines += ["", "## 記録の不足（成功・失敗を判定できない実行：%d 件）" % len(data["recording_gaps"]), ""]
        rows, more = capped(data["recording_gaps"], cap, "summary")
        for gap in rows:
            lines.append("- %s session `%s` tool `%s`: %s" % (
                gap["ts"], gap["session_id"], gap["tool"], gap["reason"]))
        lines += more
    return lines


def render_tests(data, cap):
    lines = []
    for kind in (REGRESSION, MERGE, OTHER, RED):
        found = [r for r in data["test_failures"] if r["class"] == kind]
        if kind == MERGE and not found:
            continue
        lines += ["", "## テスト失敗：%s（%d 種類）" % (LABELS[kind], len(found)), ""]
        if kind == MERGE:
            lines += ["取り込んだ変更の影響を含みます。回帰候補には数えません。", ""]
        if not found:
            lines.append("なし")
        rows, more = capped(found, cap, "tests")
        for row in rows:
            lines.append("- `%s`：%d 回、%d セッション。%s。最後は %s（session `%s`）" % (
                row["test_id"], row["count"], row["sessions"], row["basis"],
                row["last_ts"], row["last_session"]))
            if kind == REGRESSION:
                lines.append("  - 対象の型：`test:%s`" % row["test_id"])
        lines += more
    return lines


def render_tools(data, cap):
    lines = []
    tail_lines = 8 if cap is None else 4
    for kind, short, title in TOOL_KINDS:
        found = [r for r in data["tool_failures"] if r["type"] == kind]
        lines += ["", "## %s（%d 種類）" % (title, len(found)), ""]
        if not found:
            lines.append("なし")
        rows, more = capped(found, cap, "tools")
        for row in rows:
            codes = "、終了コード %s" % ",".join(map(str, row["exit_codes"])) if row["exit_codes"] else ""
            lines.append("- `%s`：%d 回、%d セッション%s。最後は %s（session `%s`）" % (
                row["command_key"], row["count"], row["sessions"], codes,
                row["last_ts"], row["last_session"]))
            lines.append("  - 対象の型：`%s:%s`" % (short, row["command_key"]))
            lines.append("  - コマンド：`%s`" % shorten(row["last_command"], 300))
            if row["last_output_tail"]:
                lines.append("  - 出力の末尾：")
                lines += ["    > " + text[:200] for text in row["last_output_tail"].splitlines()[-tail_lines:]]
        lines += more
    return lines


def render_files(data, cap):
    rework = data["rework_candidates"]
    lines = ["", "## 書き直し候補（%d 件）" % len(rework), "",
             "同じセッションの中で、複数の発話にまたがって書き直されたファイルです。"
             "発話の時刻からセッションログを確認してください。", ""]
    if not rework:
        lines.append("なし")
    rows, more = capped(rework, cap, "files")
    for row in rows:
        lines.append("- `%s`：%d 回の発話で書き込み（session `%s`）" % (
            row["path"], row["turns"], row["session_id"]))
        for prompt in row["prompts"][:3 if cap is not None else 5]:
            lines.append("  - 発話の時刻：%s" % prompt["ts"])
    lines += more

    lines += ["", "## 人間の手直し候補（%d 件）" % len(data["fix_candidates"]), ""]
    if not data["fix_candidates"]:
        lines.append("なし")
    rows, more = capped(data["fix_candidates"], cap, "files")
    for row in rows:
        lines.append("- `%s`：%s。エージェントの最後の書き込みは %s（session `%s`）" % (
            row["path"], row["status"], row["written_at"], row["session_id"]))
    lines += more
    if data["fix_excluded"]:
        lines += ["", "merge などによる変更として除いたもの：%d 件" % len(data["fix_excluded"])]
    return lines


def render_harness(data, cap):
    lines = ["", "## 比較対象の所在（本文は未確認）", ""]
    for entry in data["harness"]:
        lines.append("- %s `%s`: %s" % (entry["scope"], entry["path"], entry["status"]))
        rows, _ = capped(entry["files"], cap, "harness")
        lines.extend("  - `%s`" % path for path in rows)
        if len(rows) < len(entry["files"]):
            lines.append("  - ほか %d 個。全件は `--section harness` で出します。" % (len(entry["files"]) - len(rows)))
    return lines + render_steering(data["steering"])


NOTE_LABELS = {"none": "未読", "fresh": "ノートあり", "stale": "ノートの後に発話あり"}
STEERING_GUIDE_CHARS = 8000  # 常時読み込みの目安。超えたら、足す前に分割や条件付きを検討する


def render_steering(weight):
    lines = ["", "## steering の重さ", ""]
    if not weight["files"]:
        lines.append("steering と AGENTS.md はありません。")
        return lines
    lines += ["| 範囲 | パス | 読み込みの条件 | 文字数 |", "|---|---|---|---|"]
    for row in weight["files"]:
        lines.append("| %s | `%s` | %s | %d |" % (row["scope"], row["display"], row["inclusion"], row["chars"]))
    note = "。目安の %d 文字を超えています" % STEERING_GUIDE_CHARS \
        if weight["always_total"] > STEERING_GUIDE_CHARS else ""
    lines += ["", "常時読み込みの合計：%d 文字（プロジェクト内 %d、ユーザー共通 %d）%s" % (
        weight["always_total"], weight["always_chars"]["project"], weight["always_chars"]["user"], note)]
    return lines


REPLY_ESTIMATE_CHARS = 350  # 要約表示で、発話1件に添えられる応答とツール実行の行の見積もり


def reading_plan(sessions, budget):
    """今回の振り返りで読むセッションを決める。

    件数ではなく、読む量（文字数の見積もり）で区切る。短いセッションは多く、長いセッションは少なく読む。
    未読が残っても、もう一度振り返れば続きから読める。
    """
    unread = [s for s in sessions if s["note"] != "fresh" and s["prompts"] and s["log"] in ("ok", "partial_log")]
    for row in unread:
        row["estimate"] = row["prompt_chars"] + REPLY_ESTIMATE_CHARS * row["prompts"]
    chosen, total = [], 0
    for row in unread:  # 新しい順
        if chosen and total + row["estimate"] > budget:
            break
        chosen.append(row)
        total += row["estimate"]
    rest = unread[len(chosen):]
    return {"budget": budget, "read": [r["session_id"] for r in chosen], "read_chars": total,
            "rest": len(rest), "rest_chars": sum(r["estimate"] for r in rest),
            "unreadable": sum(1 for s in sessions if s["note"] != "fresh" and s["prompts"] == 0
                              and s["log"] not in ("ok", "partial_log"))}


def render_plan(plan):
    total = len(plan["read"]) + plan["rest"]
    lines = ["", "## 未読のセッション（合計 %d 件）" % total, ""]
    if not total:
        lines.append("未読のセッションはありません。")
    else:
        lines.append("- 未読の合計：%d 件。レポートの「未読」には、ノートを書き終えた後の集約に出るこの数を書きます。" % total)
        lines.append("- 1回の振り返りで読む分：次の %d 件（見積もり %d 文字）。" % (
            len(plan["read"]), plan["read_chars"]))
    for session in plan["read"]:
        lines.append("  - `%s`" % session)
    if plan["rest"]:
        lines.append("- その後に残る分：%d 件（見積もり %d 文字）。1回に読む量の上限（%d 文字）を超えるためです。"
                     "もう一度 /kiroku-retro を実行すると続きを読みます。" % (
                         plan["rest"], plan["rest_chars"], plan["budget"]))
    if plan["unreadable"]:
        lines += ["", "ログが無い、または読めないセッションが %d 件あります。会話に基づく判断はできません。"
                  % plan["unreadable"]]
    return lines


def render_review(data, cap=None):
    """ノートと台帳から求めたもの。会話からの型の候補、ノートの検査、案の追跡。"""
    topics = data["topics"]
    candidates = [t for t in topics if t["candidate"]]
    lines = ["", "## 会話からの型の候補（%d 件）" % len(candidates), "",
             "振り返りノートの引用をログと照合し、%d つ以上の別のセッションに出たトピックです。"
             "継続的な方針は1回でも候補にします。" % notes.PATTERN_SESSIONS, ""]
    if not candidates:
        lines.append("なし")
    for topic in candidates:
        unsure = "、うち確認事項だけのセッション %d 件" % topic["unsure_only_sessions"] \
            if topic["unsure_only_sessions"] else ""
        lines.append("- `topic:%s`：%d セッション%s、%d 回（%s）。%s" % (
            topic["topic"], topic["sessions"], unsure, topic["count"],
            "、".join(notes.KINDS[k] for k in topic["kinds"]), topic["summary"]))
        for example in topic["examples"]:
            lines.append("  - session `%s` index=%s 「%s」" % (
                example["session_id"], example["index"], shorten(example["quote"], 120)))
            if example.get("context"):
                lines.append("    - ノートに書かれた経緯：%s" % shorten(example["context"], 120))
    others = [t for t in topics if not t["candidate"]]
    if others:
        lines += ["", "### まだ型ではないトピック（同じ話題には、この名前を使う）", ""]
        rows, more = capped(others, None if cap is None else cap * 2, "review")
        for topic in rows:
            lines.append("- `topic:%s`：期間内 %d セッション、全期間 %d セッション。%s" % (
                topic["topic"], topic["sessions"], topic["all_sessions"], topic["summary"]))
        lines += more
    if data["note_problems"]:
        lines += ["", "## ノートの検査で外したもの（%d 件）" % len(data["note_problems"]), ""]
        rows, more = capped(data["note_problems"], cap, "review")
        for problem in rows:
            lines.append("- session `%s`：%s" % (problem["session_id"], problem["problem"]))
        lines += more

    lines += ["", "## 採用した案の追跡（%d 件）" % len(data["adopted"]), ""]
    if not data["adopted"]:
        lines.append("なし")
    adopted, more_adopted = capped(data["adopted"], cap, "review")
    for row in adopted:
        if row["before"] is None:
            counts = row["verdict"]
        elif row["verdict"] == "まだ日が浅い":
            counts = "採用から %s 日。まだ日が浅い（%d 日後から判定）" % (row["after_days"], ledger.YOUNG_DAYS)
        else:
            counts = "採用前 %d 回／%s 日、採用後 %d 回／%s 日。%s" % (
                row["before"], row["before_days"], row["after"], row["after_days"], row["verdict"])
        lines.append("- `%s` %s（対象の型 `%s`、採用 %s）：%s" % (
            row["id"], row["title"], row["target"], row["decided_at"], counts))
    lines += more_adopted
    if data["pending"]:
        lines += ["", "## 前回のレポートの未判断の案（%d 件）" % len(data["pending"]), "",
                  "最新のレポートにあり、採用も見送りも記録されていない案です。"
                  "今回のレポートに引き継ぐか、外すかを決めます。", ""]
        for row in data["pending"]:
            recent = "数えられない" if row["recent"] is None else "%d 回" % row["recent"]
            lines.append("- `%s` %s（対象の型 `%s`）：期間内の発生 %s" % (
                row["id"], row["title"], row["target"] or "記載なし", recent))
    if data["rejected"]:
        lines += ["", "## 見送った案（%d 件）" % len(data["rejected"]), "",
                  "同じ対象の型への案は、見送りの後に新しい発生がある場合だけ出します。", ""]
        rejected, more = capped(data["rejected"], cap, "review")
        for row in rejected:
            after = "数えられない" if row["after"] is None else "%d 回" % row["after"]
            lines.append("- `%s` %s（対象の型 `%s`、見送り %s）：その後の発生 %s。理由：%s" % (
                row["id"], row["title"], row["target"], row["decided_at"], after, row["reason"] or "記載なし"))
        lines += more
    return lines


def render_sessions(sessions, cap=None):
    rows, more = capped(sessions, cap, "sessions")
    lines = ["", "## セッション一覧（%d 件、新しい順）" % len(sessions), "",
             "会話は `--session <session_id>` で1件ずつ読みます。", "",
             "| session | 最後の活動 | 記録 | ログ | 発話 | 発話の文字数 | 振り返りノート | 記録されたエージェント |",
             "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append("| `%s` | %s | %s | %s | %d | %d | %s | %s |" % (
            row["session_id"], row["last_activity"], "あり" if row["recorded"] else "ログのみ",
            row["log"], row["prompts"], row["prompt_chars"], NOTE_LABELS[row["note"]],
            mask(row.get('agent') or '未確認')))
    if more:
        lines += [""] + more
    return lines


def render_session(view):
    lines = ["# セッションの要約表示 `%s`" % view["session_id"], "",
             "- ログの状態：%s" % view["status"],
             "- 記録されたエージェント：`%s`（session.json、取れない場合は未確認。途中の切り替えは未復元）" % mask(view.get('agent') or '未確認'),
             "- ユーザー発話：%d 件" % len(view["turns"]),
             "- ユーザー発話は原文、ツール実行は件数、応答は最後の1件の冒頭だけです。",
             "- 応答やその後の作業を根拠に書くときは、抜粋で本文を確かめます。",
             "- 全文は `excerpt.py --session %s --index <index> --context 3` で読みます。" % view["session_id"]]
    if view["status"] != "ok":
        lines.append("- 会話に基づく判断を保留：%s" % (", ".join(view["issues"]) or view["status"]))
    for turn in view["turns"]:
        lines += ["", "## index=%d [%s] ユーザー（%d 文字）" % (turn["index"], turn["ts"], turn["chars"]),
                  "", turn["text"], ""]
        if turn.get("duplicates"):
            lines.append("- 同じ発話がログの index=%s にも記録されています。1件として数えます。"
                         % "、".join(map(str, turn["duplicates"])))
        if turn["tools"]:
            tools = "、".join("%s×%d" % item for item in sorted(turn["tools"].items()))
            failed = "、失敗 %d 件" % turn["tool_failures"] if turn["tool_failures"] else ""
            lines.append("- ツール実行：%s%s" % (tools, failed))
        if turn["reply"]:
            lines.append("- 応答（index=%d、%d 文字）：%s" % (
                turn["reply"]["index"], turn["reply"]["chars"], turn["reply"]["text"]))
        else:
            lines.append("- 応答：なし")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="kiroku の記録を集約する")
    parser.add_argument("--root", default=None, help="リポジトリのルート（既定は現在の git リポジトリ）")
    parser.add_argument("--since", default="7d", help="開始。14d のような日数、または日付（既定 7d）")
    parser.add_argument("--until", default=None, help="終了。日数または日付")
    parser.add_argument("--by", choices=("day", "week", "all"), default="week", help="要約の単位")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    parser.add_argument("--require-agent", action="store_true", help="専用エージェントの起動を確認してから進む")
    parser.add_argument("--check-report", help="レポートの検証値と対象の型を記録と照合する")
    parser.add_argument("--section", choices=SECTIONS, help="この節だけを、件数を絞らずに出す")
    parser.add_argument("--session", help="このセッションだけを要約表示する")
    parser.add_argument("--user-chars", type=int, default=1500, help="要約表示で、発話1件の最大文字数（既定 1500）")
    parser.add_argument("--reply-chars", type=int, default=300, help="要約表示で、応答の冒頭の文字数（既定 300）")
    parser.add_argument("--prompts", action="store_true", help="全発話を全文でページ表示する（予備）")
    parser.add_argument("--prompt-offset", type=int, default=0, help="発話の開始位置（既定 0）")
    parser.add_argument("--prompt-limit", type=int, default=20, help="1ページの発話分割数（既定 20）")
    args = parser.parse_args(argv)

    if args.prompt_offset < 0 or args.prompt_limit < 1:
        parser.error("prompt-offset は0以上、prompt-limit は1以上にしてください")

    root = args.root
    if root is None:
        top = Repo(os.getcwd()).git("rev-parse", "--show-toplevel")
        root = top.strip() if top else os.getcwd()
    if args.require_agent and prompts.execution_context(root)['status'] != 'verified':
        print('専用エージェントを確認できません。kiro-cli --agent kiroku-retro で起動し、/kiroku-retro を実行してください。')
        return 1
    now = datetime.datetime.now(datetime.timezone.utc)
    if args.check_report:
        try:
            with open(args.check_report, encoding='utf-8') as handle:
                body = handle.read()
            declared = reportcheck.extract(body)
            start = parse_bound(declared['since'], now) if declared['since'] else None
            end = parse_bound(declared['until'], now) if declared['until'] else None
            expected = aggregate(root, start, end)['report_facts']
            errors = reportcheck.validate(body, expected)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors = ['レポートを検査できません：' + str(exc)]
        for error in errors:
            print(mask(error))
        if not errors:
            print('検証値と対象の型は記録と一致します。本文の意味・提案の妥当性は別に確認してください。')
        return 1 if errors else 0
    since = parse_bound(args.since, now) if args.since not in ("all", "") else None
    until = parse_bound(args.until, now) if args.until else now
    if args.session:
        if args.user_chars < 1 or args.reply_chars < 1:
            parser.error("user-chars と reply-chars は1以上にしてください")
        view = prompts.distill(root, args.session, since, until, args.user_chars, args.reply_chars)
        if args.json:
            json.dump(view, sys.stdout, ensure_ascii=False, indent=2, default=str)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_session(view))
        return
    data = aggregate(root, since, until, args.by, args.prompts,
                     args.prompt_offset, args.prompt_limit)
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    elif args.section:
        sys.stdout.write(render(data, args.prompts, section=args.section))
    else:
        budget = load_config(root)["retro_output_chars"]
        for cap in OUTPUT_CAPS:
            text = render(data, args.prompts, cap=cap)
            if len(text) <= budget:
                break
        sys.stdout.write(text)


if __name__ == "__main__":
    sys.exit(main())
