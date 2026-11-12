#!/usr/bin/env python3
"""kiroku の集約。.kiro/retro/events.jsonl を読み、後計算で分類して要約を出す。

使い方:
  python3 .kiro/kiroku/aggregate.py [--since 14d] [--until 2026-12-01] [--by week] [--json]

テスト失敗の分類（記録時ではなく、ここで計算する）:
  正常な Red  前回の全テスト成功より後に追加されたテスト、
              または前回の全テスト成功以降にテストファイルが変更されたテストの失敗
  回帰        前回の全テスト成功の時点で存在し、通っていて、テストファイルが変わっていないテストの失敗
  その他      基準になる全テスト成功が無い、通った記録が無い、git から状態を復元できない、など

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

RED, REGRESSION, OTHER = "red", "regression", "other"
LABELS = {RED: "正常な Red", REGRESSION: "回帰", OTHER: "その他"}
VCS_ACTIONS = ("merge", "pull", "rebase", "checkout", "reset", "cherry-pick", "revert",
               "commit (merge)", "am", "switch", "restore")


class Repo:
    """集約に必要な git の読み取りだけを行う。"""

    def __init__(self, root):
        self.root = root
        self._blob = {}
        self._diff = {}

    def git(self, *args, stdin=None):
        try:
            done = subprocess.run(["git", "-C", self.root] + list(args), input=stdin,
                                  capture_output=True, text=True, timeout=60)
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
                        elif verdict is None:
                            kind, basis = REGRESSION, "通っていたテストの失敗（テストファイルは特定できず）"
                        else:
                            kind, basis = REGRESSION, "通っていたテストの失敗（テストファイルは変更なし）"
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


def command_key(command):
    """似たコマンドをまとめるための見出し。先頭の3語まで。"""
    return " ".join((command or "").split()[:3])[:60]


def period_key(moment, by):
    local = moment.astimezone()
    if by == "day":
        return local.strftime("%Y-%m-%d")
    if by == "week":
        monday = local.date() - datetime.timedelta(days=local.weekday())
        return "%s の週" % monday.isoformat()
    return "全期間"


def aggregate(root, since=None, until=None, by="week"):
    repo = Repo(root)
    events = load_events(os.path.join(root, ".kiro", "retro", "events.jsonl"))
    runs = [e for e in events if e.get("type") == "test_run"]
    failures = classify_failures(repo, runs)  # 基準が要るので、分類は全期間で行う

    def inside(item):
        return (since is None or item["_time"] >= since) and (until is None or item["_time"] < until)

    events = [e for e in events if inside(e)]
    failures = [f for f in failures if inside(f)]
    candidates, excluded = fix_candidates(repo, [e for e in events if e.get("type") == "write"])

    periods = collections.OrderedDict()
    columns = ("sessions", "test_runs", RED, REGRESSION, OTHER, "tool_failures", "cancelled",
               "denied", "writes")
    seen_writes = set()

    def bucket(moment):
        return periods.setdefault(period_key(moment, by), {c: 0 for c in columns} | {"_s": set()})

    for event in events:
        row = bucket(event["_time"])
        row["_s"].add(event.get("session_id"))
        kind = event.get("type")
        if kind == "test_run":
            row["test_runs"] += 1
        elif kind == "tool_failure":
            row["tool_failures"] += 1
        elif kind == "tool_cancelled":
            row["cancelled"] += 1
        elif kind == "tool_denied":
            row["denied"] += 1
        elif kind == "write":
            # 1回の書き込みは PostFileSave と PostToolUse の両方で記録されるので、まとめて数える。
            key = (event.get("session_id"), event.get("path"), event.get("hash"))
            if key not in seen_writes:
                seen_writes.add(key)
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
        if event.get("type") not in ("tool_failure", "tool_cancelled", "tool_denied"):
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

    return {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "event_count": len(events),
        "periods": periods,
        "test_failures": finish(grouped_tests.values()),
        "tool_failures": finish(grouped_tools.values()),
        "fix_candidates": candidates,
        "fix_excluded": excluded,
    }


def render(data):
    lines = ["# kiroku 集約", ""]
    lines.append("- 期間：%s 〜 %s" % (data["since"] or "最初", data["until"] or "現在"))
    lines.append("- イベント数：%d" % data["event_count"])
    lines.append("- レポートの保存先：.kiro/retro/reports/%s.md"
                 % datetime.datetime.now().strftime("%Y-%m-%d-%H%M"))
    if not data["event_count"]:
        lines += ["", "この期間の記録はありません。"]
        return "\n".join(lines) + "\n"

    lines += ["", "## 期間ごとの要約", "",
              "| 期間 | セッション | テスト実行 | 正常な Red | 回帰 | その他 | ツール失敗 | 取り消し | 拒否 | 書き込み |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for name, row in data["periods"].items():
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d | %d |" % (
            name, row["sessions"], row["test_runs"], row[RED], row[REGRESSION], row[OTHER],
            row["tool_failures"], row["cancelled"], row["denied"], row["writes"]))

    for kind in (REGRESSION, OTHER, RED):
        rows = [r for r in data["test_failures"] if r["class"] == kind]
        lines += ["", "## テスト失敗：%s（%d 種類）" % (LABELS[kind], len(rows)), ""]
        if not rows:
            lines.append("なし")
        for row in rows[:30]:
            lines.append("- `%s`：%d 回、%d セッション。%s。最後は %s（session `%s`）" % (
                row["test_id"], row["count"], row["sessions"], row["basis"],
                row["last_ts"], row["last_session"]))

    for kind, title in (("tool_failure", "ツール失敗"), ("tool_cancelled", "人による取り消し"),
                        ("tool_denied", "権限の規則による拒否")):
        rows = [r for r in data["tool_failures"] if r["type"] == kind]
        lines += ["", "## %s（%d 種類）" % (title, len(rows)), ""]
        if not rows:
            lines.append("なし")
        for row in rows[:30]:
            codes = "、終了コード %s" % ",".join(map(str, row["exit_codes"])) if row["exit_codes"] else ""
            lines.append("- `%s`：%d 回、%d セッション%s。最後は %s（session `%s`）" % (
                row["command_key"], row["count"], row["sessions"], codes,
                row["last_ts"], row["last_session"]))
            lines.append("  - コマンド：`%s`" % row["last_command"])
            if row["last_output_tail"]:
                lines.append("  - 出力の末尾：")
                lines += ["    > " + text for text in row["last_output_tail"].splitlines()[-8:]]

    lines += ["", "## 人間の手直し候補（%d 件）" % len(data["fix_candidates"]), ""]
    if not data["fix_candidates"]:
        lines.append("なし")
    for row in data["fix_candidates"][:50]:
        lines.append("- `%s`：%s。エージェントの最後の書き込みは %s（session `%s`）" % (
            row["path"], row["status"], row["written_at"], row["session_id"]))
    if data["fix_excluded"]:
        lines += ["", "merge などによる変更として除いたもの：%d 件" % len(data["fix_excluded"])]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="kiroku の記録を集約する")
    parser.add_argument("--root", default=None, help="リポジトリのルート（既定は現在の git リポジトリ）")
    parser.add_argument("--since", default="14d", help="開始。14d のような日数、または日付（既定 14d）")
    parser.add_argument("--until", default=None, help="終了。日数または日付")
    parser.add_argument("--by", choices=("day", "week", "all"), default="week", help="要約の単位")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    args = parser.parse_args(argv)

    root = args.root
    if root is None:
        top = Repo(os.getcwd()).git("rev-parse", "--show-toplevel")
        root = top.strip() if top else os.getcwd()
    now = datetime.datetime.now(datetime.timezone.utc)
    since = parse_bound(args.since, now) if args.since not in ("all", "") else None
    until = parse_bound(args.until, now) if args.until else None
    data = aggregate(root, since, until, args.by)
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(data))


if __name__ == "__main__":
    main()
