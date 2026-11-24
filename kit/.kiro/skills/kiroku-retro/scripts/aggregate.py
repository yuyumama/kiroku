#!/usr/bin/env python3
"""kiroku の集約。.kiro/kiroku/data/events.jsonl を読み、後計算で分類して要約を出す。

使い方:
  python3 .kiro/skills/kiroku-retro/scripts/aggregate.py [--since last] [--until 2026-12-01] [--by week] [--json]
  python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --section tests
  python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --session <session_id>

出力の長さ:
  全体の出力は、設定の retro_output_chars（既定 20000 文字）に収まるように、各一覧を上位だけに絞る。
  省いた分は、その一覧の末尾に件数を出す。--section <名前> で、その節だけを全件出す。

テスト失敗の分類（記録時ではなく、ここで計算する）:
  Red 候補    前回の全テスト成功より後に追加されたテスト、
              またはそのテストが最後に通った後にテストファイルが変更されたテストの失敗
  回帰        前回の全テスト成功の時点で存在し、通った記録があり、そのテストが最後に通った後に
              テストファイルが変わっていない（または、どのファイルか特定できない）テストの失敗
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

手直し候補:
  エージェントが書き込みツールで最後に書いた内容と、いまの内容が違うファイル。誰が変えたかは証明しない
  （エージェントがシェルで書き換えた場合も入る。その後のシェル実行にファイルの名前が出れば、手がかりに添える）。
  merge、pull、rebase、checkout、reset などによる変更は除く。
"""
import argparse
import collections
import datetime
import hashlib
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
import skeleton  # noqa: E402
import citations  # noqa: E402
import reads  # noqa: E402
import diagnose  # noqa: E402
import answers  # noqa: E402
import debug  # noqa: E402
from record import (count_setting, load_config, load_state, mask, not_run_kind, recording_lock,  # noqa: E402
                    recording_start, save_state, script_root)

RED, REGRESSION, MERGE, OTHER = "red", "regression", "merge", "other"
LABELS = {RED: "Red 候補", REGRESSION: "回帰候補", MERGE: "マージ中の失敗", OTHER: "その他"}
TOOL_KINDS = (("tool_failure", "tool", "ツール失敗"), ("tool_cancelled", "cancelled", "人による取り消し"),
              ("tool_timeout", "timeout", "タイムアウト"), ("tool_denied", "denied", "権限の規則による拒否"))
# 実行後のフックが呼ばれず、記録に残らなかったシェル実行。セッションログから拾う。
UNRECORDED_KINDS = (
    ("blocked", "実行前に止められた呼び出し",
     "フックの記録には無く、セッションログから拾ったものです。止めたフックと理由はログに残らないため未確定です。"
     "候補のフックを原因と書くときは、そのスクリプトを読んで確かめてください。"
     "追跡の対象（対象の型）にはなりません。"),
    ("interrupted", "実行中に中断された呼び出し",
     "フックの記録には無く、セッションログから拾ったものです。実行が始まったあとに中断されたもので、"
     "承認されなかった実行（人による取り消し）とは別です。途中まで実行された可能性があります。"
     "追跡の対象（対象の型）にはなりません。"),
)
GAP_REASONS = {
    "test_end_missing": "テスト実行の終わりが記録されなかった（別のフックによる停止、または中断の可能性）",
    "unknown_not_run": "実行が完了しなかった理由を判定できない",
    "unknown_shell_response": "シェルの応答から終了コードを読めない",
    "unreadable_tool_call": "セッションログで完了していないシェル実行だが、結果の行が無いか、形式が想定と違うため判定できない",
}
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
        self._tree = {}
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

    def tree(self, commit):
        """あるコミットのファイルの一覧。復元できなければ空。"""
        if commit not in self._tree:
            out = self.git("ls-tree", "-r", "--name-only", "-z", commit) if commit else None
            self._tree[commit] = frozenset(p for p in (out or "").split("\0") if p)
        return self._tree[commit]

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


ERROR_STAMP = re.compile(r"(\d{4}-\d\d-\d\dT\S+)\s")   # record.py が record-errors.log の行の先頭に書く時刻


def record_error_lines(data_dir, until=None):
    """record-errors.log の行の数。until を渡すと、その時刻より前に書かれた行だけを数える。

    例外の文面が改行を含むと、1件が複数の行になる。時刻の無い行は、直前の行と同じ時刻に
    書かれたものとして扱う。最初の時刻より前の行は、until より前とみなす。
    """
    count, written = 0, None
    try:
        with open(os.path.join(data_dir, "record-errors.log"), encoding="utf-8", errors="replace") as handle:
            for line in handle:
                found = ERROR_STAMP.match(line)
                moment = excerpt.parse_time(found.group(1)) if found else None
                if moment:
                    written = moment
                if until is None or written is None or written < until:
                    count += 1
    except OSError:
        return 0
    return count


def record_errors_at(data_dir, state, name):
    """そのレポートの骨組みを作ったときの、記録フックのエラーの行数。分からなければ None。

    骨組みを作った後も、記録フックは動く（振り返り自身のシェル実行でも動く）。検査のときの行数で
    骨組みを作り直すと、スクリプトが書いた「記録フックのエラー：N 行」と合わなくなる。
    骨組みを作ったときに控えた数を使う。控えの無い以前の版のレポートは、作った時刻（report_made）
    までの行を数える。どちらも無ければ None（検査のときの行数で比べる。以前の動き）。
    """
    kept = state.get("report_record_errors")
    value = kept.get(name) if isinstance(kept, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    made = state.get("report_made")
    moment = excerpt.parse_time(made.get(name)) if isinstance(made, dict) and isinstance(made.get(name), str) else None
    return record_error_lines(data_dir, until=moment) if moment else None


def since_last(root):
    """`--since last`：前回の振り返りの後から。経過した日数で、読む範囲を変えないための既定。

    書き終えた最新のレポートの検証値の終わり（until）から読む。そのレポートに未読が残っていた
    （unread が 0 でない）なら、読み残しを落とさないよう、その始まり（since）から読む。
    レポートが無い、検証値を読めないときは None（すべて。記録の始まりは aggregate の clamp が当てる）。
    """
    name = ledger.latest_report(root)
    if not name:
        return None
    try:
        with open(os.path.join(root, ".kiro", "kiroku", "data", "reports", name + ".md"), encoding="utf-8") as handle:
            facts = reportcheck.extract(handle.read())
    except (OSError, UnicodeError, ValueError):
        return None
    bound = facts.get("since") if facts.get("unread") else facts.get("until")
    return excerpt.parse_time(bound) if isinstance(bound, str) else None


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
                    if event.get("judged") == "response":
                        pass   # 記録の時点で、応答の全体から種類を決めてある。切り詰めた末尾で決め直さない
                    elif event.get("type") == "tool_denied":
                        # 古い版は、終了コード -1 のうち取り消し以外をすべて拒否として記録していた。
                        event["type"] = not_run_kind(event.get("output_tail"))
                        if event["type"] == "recording_gap":
                            event["reason"] = "unknown_not_run"
                    elif event.get("type") == "recording_gap" and event.get("reason") == "unknown_not_run":
                        # 記録の時点で種類を決められなかったもの。今の判定で読み直す。
                        kind = not_run_kind(event.get("output_tail"))
                        if kind != "recording_gap":
                            event["type"] = kind
                            del event["reason"]
                    if event.get("started_at"):
                        # 終わりが記録されなかった実行。閉じた時刻ではなく、始まった時刻で扱う。
                        event["_time"] = parse_time(event["started_at"])
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


# テストのファイルらしい拡張子。classname がこれで終わるときは、パスとして読む（vitest は classname がパス）。
SOURCE_SUFFIX = re.compile(r"\.(?:[cm]?[jt]sx?|py|rb|go|rs|java|kts?|php|cs|swift|vue|svelte|exs?|scala|dart)$", re.I)


class FileIndex:
    """ある時点のリポジトリのファイルを、最後の名前で引けるようにしたもの。"""

    def __init__(self, files):
        self.by_name, self.by_stem = {}, {}
        for path in files:
            self.by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)
            stem = os.path.splitext(path)[0]
            self.by_stem.setdefault(stem.rsplit("/", 1)[-1], []).append((stem, path))


def files_at(repo, run):
    """その実行の時点でリポジトリにあったファイル（コミットの内容に、作業ツリーの変更を重ねたもの）。"""
    files = set(repo.tree(run.get("head")))
    for path, value in (run.get("dirty") or {}).items():
        if value:
            files.add(path)
        else:
            files.discard(path)
    return FileIndex(files)


def test_file_candidates(test_id, known_file, index):
    """テストID から、そのテストが書かれたファイルの候補。分からなければ空の集合。

    JUnit の形はランナーごとに違う（docs/retro/judgment-review.md）。
      - file 属性（pytest の xunit1、kiroku のデモ）：パス。テストを実行した場所からの相対のこともある。
      - classname がパス（vitest の既定）：vitest の root からの相対パス。
      - classname がモジュール名（pytest の既定、Java）：tests.test_a、com.example.LoginTest。
      - classname が題（jest-junit の既定）やパッケージ（go）：ファイルに結びつかない。
    パスは、末尾が一致するファイルを探す。サブディレクトリで実行したときの相対パスにも当たるようにするため。
    """
    refs = [("path", known_file)] if known_file else []
    classname = test_id.split("::", 1)[0] if "::" in test_id else ""
    if classname:
        path_like = "/" in classname or "\\" in classname or SOURCE_SUFFIX.search(classname)
        refs.append(("path" if path_like else "module", classname))
    for how, ref in refs:
        found = set()
        if how == "path":
            ref = ref.replace("\\", "/")
            while ref.startswith("./"):
                ref = ref[2:]
            found = {path for path in index.by_name.get(ref.rsplit("/", 1)[-1], ())
                     if path == ref or path.endswith("/" + ref)}
        else:
            tokens = [t for t in re.split(r"[./\\]", ref) if t]
            for size in range(len(tokens), 0, -1):
                prefix = "/".join(tokens[:size])
                found = {path for stem, path in index.by_stem.get(tokens[size - 1], ())
                         if stem == prefix or stem.endswith("/" + prefix)}
                if found:
                    break
        if found:
            return found
    return set()


def test_file_changed(test_id, known_file, changed, index):
    """テストのファイルが変更一覧に含まれるか。分からなければ None。

    候補が見つからないとき、同じ名前の候補が複数あって一部だけが変わったときは、どのファイルのテストか
    決まらないので None にする（「変更なし」と断定しない）。
    """
    candidates = test_file_candidates(test_id, known_file, index)
    if not candidates:
        return None
    hit = candidates & set(changed)
    if not hit:
        return False
    return True if hit == candidates else None


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
    """全期間のテスト実行を時刻順に見て、失敗を1件ずつ分類する。

    回帰かどうかは、そのテストが最後に通った実行を基準に、テストファイルが変わったかで見る。
    直近の「全テスト成功」は、一部のテストだけを流した実行でも成り立つので、それを基準にすると、
    その間に変わったテストファイルを見落とす。
    """
    failures = []
    green = None          # 直近の全テスト成功（その実行に含まれたテストが、すべて通ったもの）
    first_seen = {}       # テストID -> 初めて現れた実行の番号
    last_pass = {}        # テストID -> (最後に通った実行の番号, その実行)
    green_index = -1
    change_cache, index_cache = {}, {}
    for index, run in enumerate(runs):
        results = run.get("results")
        if results:
            for test_id in results.get("failed", []) + results.get("error", []):
                known_file = (run.get("test_files") or {}).get(test_id)
                if green is None:
                    kind, basis = OTHER, "基準になる全テスト成功の記録が無い"
                elif test_id not in first_seen or first_seen[test_id] > green_index:
                    kind, basis = RED, "前回の全テスト成功より後に追加されたテスト"
                elif test_id not in last_pass:
                    kind, basis = OTHER, "通った記録が無い"
                else:
                    base_index, base = last_pass[test_id]
                    if (base_index, index) not in change_cache:
                        change_cache[(base_index, index)] = changed_between(repo, base, run)
                    changed = change_cache[(base_index, index)]
                    if changed is None:
                        kind, basis = OTHER, "git から当時の状態を復元できない"
                    else:
                        if index not in index_cache:
                            index_cache[index] = files_at(repo, run)
                        verdict = test_file_changed(test_id, known_file, changed, index_cache[index])
                        if verdict:
                            kind, basis = RED, "このテストが最後に通った後に、テストファイルが変更された"
                        else:
                            kind = REGRESSION
                            basis = "通っていたテストの失敗（このテストが最後に通った後、テストファイルは%s）" % (
                                "特定できず" if verdict is None else "変更なし")
                            merging = merge_basis(repo, run)
                            if merging:
                                kind, basis = MERGE, merging
                            else:
                                merged = merged_between(repo, base, run)
                                if merged:
                                    basis += "。このテストが最後に通った後に %s あり" % "、".join(merged)
                failures.append({
                    "test_id": test_id, "class": kind, "basis": basis, "ts": run["ts"],
                    "_time": run["_time"], "session_id": run.get("session_id"),
                    "command": run.get("command"),
                })
            for test_id in all_ids(run):
                first_seen.setdefault(test_id, index)
            for test_id in results.get("passed", []):
                last_pass[test_id] = (index, run)
        if is_green(run):
            green, green_index = run, index
    return failures


def mentions(command, path):
    """コマンドの文面に、そのファイルの名前が語として出るか（パスの一部として出る場合を含む）。"""
    name = path.rsplit("/", 1)[-1]
    return bool(re.search(r"(?:^|[\s/'\"=(:])" + re.escape(name) + r"(?=$|[\s'\";:)|&>,])", command))


def fix_candidates(repo, writes, calls=()):
    """エージェントが書き込みツールで最後に書いた内容と、いまの内容が違うファイルを探す。

    誰が変えたかは分からない。人の手直しのほか、エージェントがシェルで書き換えた場合（ヒアドキュメント、
    sed、整形コマンド）も、書き込みの記録が残らないので、ここに入る。calls（セッションログのシェル実行。
    [(時刻, コマンド)]）のうち、書き込みの後にそのファイルの名前が出るものを、手がかりとして添える。
    """
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
            later = []
            for moment, command in sorted(calls, key=lambda c: c[0]):
                head = shorten(mask(command.split("\n", 1)[0]), 80)   # 1行目（ヒアドキュメントの本文は出さない）
                if moment >= event["_time"] and mentions(command, path) and head not in later:
                    later.append(head)
            if later:
                row["shell_after"] = later[:2]
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


def counted_run(event):
    """回数に数えるテスト実行か。文面だけで一致して結果も取り込めなかったもの（実行か言及か分からない）と、
    バックグラウンドで起動しただけのもの（終わったか分からない）は数えない。"""
    return event.get("type") == "test_run" and not (event.get("results") is None and (
        event.get("matched") == "text" or event.get("background")))


def after_writes(events):
    """書き込みのあったターンの数と、そのうち最後の書き込みの後に、同じターンでテストを実行したものの数。

    ターンは、同じセッションの発話の記録（prompt）から次の発話の記録まで。発話の記録が無い部分は、
    1つのターンとみなす。確かめる手段（V3）と早く知らせる仕組み（V4）の手がかりにする。
    テストで確かめる必要の無い書き込み（文書など）も含むので、割合の良し悪しは、読む側が決める。
    """
    turns, state = collections.Counter(), {}
    for event in events:
        session, kind = event.get("session_id"), event.get("type")
        if kind == "prompt":
            turns[session] += 1
        elif kind == "write":
            state[(session, turns[session])] = False
        elif counted_run(event) and (session, turns[session]) in state:
            state[(session, turns[session])] = True
    return {"turns": len(state), "tested": sum(1 for tested in state.values() if tested)}


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


def clamp(root, since):
    """reset.py で記録をやり直したなら、その時刻より前は数えない（Kiro のセッションログは残るため）。"""
    start = recording_start(os.path.join(root, ".kiro", "kiroku", "data"))
    return start if start and (since is None or since < start) else since


def aggregate(root, since=None, until=None, by="week", with_prompts=False,
              prompt_offset=0, prompt_limit=20, before_report=None):
    repo = Repo(root)
    data_dir = os.path.join(root, ".kiro", "kiroku", "data")
    since = clamp(root, since)
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

    def occurrences(target, report=None):
        """台帳の「対象の型」から、発生時刻の一覧を返す。数えられない型は None。

        report は、その案が書かれたレポート名。失敗の見出しの形式を見分けるのに使う。
        """
        kind, _, name = (target or "").partition(":")
        if kind == "topic":
            return [f["_time"] for f in findings
                    if f["status"] == "verified" and f["topic"] == name and f["_time"]]
        if kind in ("tool", "denied", "cancelled", "timeout"):
            wanted = {short: name for name, short, _ in TOOL_KINDS}[kind]
            matching = [e for e in all_events if e.get("type") == wanted]
            exact = [e['_time'] for e in matching if command_key(e.get('command')) == name]
            if ledger.current_keys(root, report):
                return exact
            # 旧版のレポートの対象は「先頭3語が同じ失敗すべて」を指す。その失敗がすべて
            # 今の同じ見出しに入るときだけ引き継ぐ。完全に一致する実行があっても、
            # ほかの見出しに分かれる実行が含まれるなら、対象が狭まるので数えない。
            legacy = {command_key(e.get('command')) for e in matching
                      if ' '.join((e.get('command') or '').split()[:3])[:60] == name}
            if len(legacy) > 1:
                return None
            if legacy and legacy != {name}:
                return [e['_time'] for e in matching if command_key(e.get('command')) in legacy]
            return exact
        if kind == "test":
            return [f["_time"] for f in all_failures if f["class"] == REGRESSION and f["test_id"] == name]
        return None

    read_after = {}

    def exposure(target, start):
        """境目の後に、その型が起こりうる作業をどれだけ観測できたか。発生しなかったことと、
        作業が無かったことを分けるために使う。数えられない型は None。"""
        kind, _, name = (target or "").partition(":")
        if kind == "test":
            # そのテストを含む、結果を取り込めたテスト実行。成功も数える。
            count = sum(1 for e in all_events if e.get("type") == "test_run" and e["_time"] >= start
                        and name in all_ids(e))
            return {"unit": "そのテストを含むテスト実行", "count": count, "note": None}
        if kind in ("tool", "denied", "cancelled", "timeout"):
            # 成功したコマンドは記録しないので、実行の総数は分からない。記録のあるセッションを数える。
            count = len({e.get("session_id") for e in all_events if e["_time"] >= start})
            return {"unit": "フックの記録があるセッション", "count": count, "note": "実行総数は不明"}
        if kind == "topic":
            # 発話があり、ノートで読み終えたセッション。読んでいないセッションでは、起きたかが分からない。
            key = start.isoformat()
            if key not in read_after:
                read_after[key] = sum(1 for s in prompts.sessions(root, all_events, start, None)
                                      if s["prompts"] and s["note"] == "fresh")
            return {"unit": "発話を読み終えたセッション", "count": read_after[key], "note": None}
        return None

    starts = [e["_time"] for e in all_events[:1]] + [f["_time"] for f in findings if f["_time"]]
    adopted, rejected = ledger.tracking(root, occurrences, min(starts) if starts else None,
                                        datetime.datetime.now(datetime.timezone.utc), exposure)
    pending = []
    for proposal in ledger.pending(root, before_report):
        times = occurrences(proposal["target"], proposal["report"]) if proposal["target"] else None
        recent = None if times is None else sum(
            1 for t in times if (since is None or t >= since) and (until is None or t < until))
        pending.append(dict(proposal, recent=recent))

    events = [e for e in events if inside(e)]
    failures = [f for f in failures if inside(f)]

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
            if counted_run(event):
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
    grouped_unrecorded = {}
    # 発話が期間の外でも、呼び出しが期間内なら拾う。対象は、このリポジトリのセッションすべて。
    known = [s["session_id"] for s in session_rows]
    known += [s for s in {e.get("session_id") for e in all_events if e.get("session_id")} if s not in known]
    known += [s for s in prompts.discover(root, prompts.sessions_dir(root)) if s not in known]
    calls, undetermined = prompts.unrecorded_calls(root, known, since, until)
    writes = [e for e in events if e.get("type") == "write"]
    candidates, excluded = fix_candidates(repo, writes, prompts.shell_calls(
        root, known, min(e["_time"] for e in writes)) if writes else [])
    for call in calls:
        entry = grouped_unrecorded.setdefault((call["kind"], command_key(call["command"])), {
            "kind": call["kind"], "command_key": command_key(call["command"]),
            "count": 0, "sessions": set(), "hooks": []})
        entry["count"] += 1
        entry["sessions"].add(call["session_id"])
        entry["hooks"] += [name for name in call["hooks"] if name not in entry["hooks"]]
        if entry.get("last_ts") is None or call["ts"] >= entry["last_ts"]:
            entry.update(last_ts=call["ts"], last_session=call["session_id"], last_command=call["command"])
    first = all_events[0]["_time"] if all_events else None
    log_only = [s for s in session_rows if not s["recorded"]]
    before = sum(1 for s in log_only if first and s["last_activity"]
                 and parse_time(s["last_activity"]) < first)
    record_errors = record_error_lines(data_dir)
    coverage = {"sessions": len(session_rows), "recorded": len(session_rows) - len(log_only),
                "log_only": len(log_only), "first_event": first.isoformat() if first else None,
                "before_first": before, "after_first": len(log_only) - before if first else None,
                "record_errors": record_errors, "retro_sessions": len(retro),
                "retro_excluded": retro_excluded}
    diagnostics = diagnose.diagnose(root, load_config(root), prompts.hook_triggers(root))
    if diagnostics["sensors"]["test_configs"]:
        diagnostics["sensors"]["after_writes"] = after_writes(events)
    result = {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "event_count": len(events),
        "periods": periods,
        "test_failures": finish(grouped_tests.values()),
        "tool_failures": finish(grouped_tools.values()),
        "coverage": coverage,
        "recording_gaps": [{"session_id": e.get("session_id"), "ts": e.get("started_at") or e["ts"],
                            "reason": e.get("reason"), "tool": e.get("tool"),
                            "count": e["count"] if isinstance(e.get("count"), int) and e["count"] > 0 else 1}
                           for e in events if e.get("type") == "recording_gap"]
        + [{"session_id": u["session_id"], "ts": u["ts"], "reason": "unreadable_tool_call",
            "tool": u["tool"], "count": 1} for u in undetermined],
        "test_result_gaps": [{"session_id": e.get("session_id"), "ts": e["ts"],
                              "command": e.get("command"), "reason": e.get("note") or 'unknown',
                              "matched": e.get("matched") or 'invocation',
                              "background": bool(e.get("background"))}
                             for e in events if e.get("type") == "test_run" and e.get("results") is None],
        "test_configuration": [{k: mask(t.get(k)) for k in ('match', 'report', 'format')}
                               for t in load_config(root)['tests']],
        "execution_context": prompts.execution_context(root),
        "rework_candidates": rework_candidates(events),
        "prompt_page": prompts.page(root, events, since, until, prompt_offset, prompt_limit,
                                    session_ids=[s["session_id"] for s in session_rows])
        if with_prompts else None,
        "harness": harness.inventory(root),
        "steering": harness.steering_weight(root),
        "diagnostics": diagnostics,
        "recording_start": (lambda m: m.isoformat() if m else None)(recording_start(data_dir)),
        "topics": notes.topics(findings, since, until),
        "note_problems": note_problems,
        "adopted": adopted,
        "ambiguous_decisions": ledger.ambiguous_decisions(root),
        "pending": pending,
        "rejected": rejected,
        "sessions": session_rows,
        "unrecorded_calls": finish(grouped_unrecorded.values()),
        "reading_plan": reading_plan(session_rows, load_config(root)["retro_read_chars"]),
        "fix_candidates": candidates,
        "fix_excluded": excluded,
    }
    result['report_facts'] = reportcheck.facts(result)
    result['burden'] = diagnose.burden(result)
    result['one_step_max'] = count_setting(load_config(root), 'one_step_max')
    result['question_max'] = count_setting(load_config(root), 'question_max')
    # 以前のレポートで利用者に確かめたこと。同じことを聞き直さないために出す（検証値には入れない）。
    result['answers'] = answers.previous(root, before_report)
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
    if data.get("recording_start"):
        lines.append("- 記録の始まり：%s（reset.py でやり直した。これより前の記録と会話は数えない）"
                     % data["recording_start"])
    lines.append("- イベント数：%d" % data["event_count"])
    context = data['execution_context']
    lines.append('- 実行エージェント：%s（%s）' % (context['agent'] or '未確認', context['status']))
    lines.append('- レポートの検証値は `--section facts`、保存後の照合は `--check-report <パス>` で確認します。')
    lines.append("- レポートの保存先：.kiro/kiroku/data/reports/%s.md"
                 % datetime.datetime.now().strftime("%Y-%m-%d-%H%M"))
    if "question_max" in data:
        asked = data["question_max"]
        lines.append("- 利用者に聞く質問の上限：%s（設定の question_max。0 なら聞かない）" % (
            "なし。答えで変わる案が大きい順に並べる" if asked is None else "%d 問" % asked))
    if section is None and cap is not None:
        lines.append("- 各一覧は上位だけを出しています。節ごとの全件は `--section <%s>` で出します。"
                     % "|".join(SECTIONS))
    if want("plan"):
        lines += render_plan(data["reading_plan"])
    if want("review"):
        lines += render_review(data, cap)
    if want("summary"):
        lines += render_summary(data, cap)
    recorded = bool(data["event_count"])
    if want("tests") and recorded:
        lines += render_tests(data, cap)
    if want("tools"):
        if recorded:
            lines += render_tools(data, cap)
        # フックの記録が1件も無いセッションでも、止められた呼び出しはログから出す。
        if recorded or data["unrecorded_calls"]:
            lines += render_unrecorded(data, cap)
    if want("files") and recorded:
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
                '（バックグラウンドで起動。終わる前に記録されたので、成否は未確認）' if row['background']
                else '（文面で一致。テストの実行か、名前への言及かは未確認）' if row['matched'] == 'text' else '',
                shorten(row['command'], 200)))
        lines += more

    if data["recording_gaps"]:
        lines += ["", "## 記録の不足（成功・失敗を判定できない実行：%d 件）"
                  % sum(g["count"] for g in data["recording_gaps"]), ""]
        rows, more = capped(data["recording_gaps"], cap, "summary")
        for gap in rows:
            lines.append("- %s session `%s` tool `%s`: %s%s%s" % (
                gap["ts"], gap["session_id"], gap["tool"], gap["reason"],
                "（%s）" % GAP_REASONS[gap["reason"]] if gap["reason"] in GAP_REASONS else "",
                "、%d 回" % gap["count"] if gap["count"] > 1 else ""))
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


def render_unrecorded(data, cap):
    lines = []
    for kind, title, note in UNRECORDED_KINDS:
        found = [r for r in data["unrecorded_calls"] if r["kind"] == kind]
        lines += ["", "## %s（%d 種類）" % (title, len(found)), "", note, ""]
        if not found:
            lines.append("なし")
        rows, more = capped(found, cap, "tools")
        for row in rows:
            lines.append("- `%s`：%d 回、%d セッション。最後は %s（session `%s`）" % (
                row["command_key"], row["count"], row["sessions"], row["last_ts"], row["last_session"]))
            lines.append("  - コマンド：`%s`" % shorten(row["last_command"], 300))
            if kind == "blocked":
                lines.append("  - 同じターンでそれまでに動いた実行前のフック（候補。止めたフックとは限らない）：%s" % (
                    "、".join("`%s`" % name for name in row["hooks"]) or "記録なし"))
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

    lines += ["", "## 手直し候補（%d 件）" % len(data["fix_candidates"]), "",
              "エージェントが書き込みツールで最後に書いた後に、内容が変わったファイルです。人の手直しのほか、"
              "エージェントがシェルで書き換えた場合（ヒアドキュメント、sed、整形コマンド）も入ります。"
              "誰が変えたかは証明しません。案にするときは、抜粋で確かめます。", ""]
    if not data["fix_candidates"]:
        lines.append("なし")
    rows, more = capped(data["fix_candidates"], cap, "files")
    for row in rows:
        hint = "。その後のシェル実行に、このファイルの名前が出る：%s（エージェントのシェルでの変更の可能性）" % (
            "、".join("`%s`" % key for key in row["shell_after"])) if row.get("shell_after") else ""
        lines.append("- `%s`：%s。エージェントの最後の書き込みは %s（session `%s`）%s" % (
            row["path"], row["status"], row["written_at"], row["session_id"], hint))
    lines += more
    if data["fix_excluded"]:
        lines += ["", "merge などによる変更として除いたもの：%d 件" % len(data["fix_excluded"])]
    return lines


def render_harness(data, cap):
    lines = ["", "## ハーネスの診断（ファイルから決まる値。一歩先の改善案の手がかり）", ""]
    lines += diagnose.lines(data["diagnostics"], data["burden"], cap)
    lines += ["", "## 比較対象の所在（本文は未確認）", ""]
    for entry in data["harness"]:
        lines.append("- %s `%s`: %s" % (entry["scope"], entry["path"], entry["status"]))
        rows, _ = capped(entry["files"], cap, "harness")
        lines.extend("  - `%s`" % path for path in rows)
        if len(rows) < len(entry["files"]):
            lines.append("  - ほか %d 個。全件は `--section harness` で出します。" % (len(entry["files"]) - len(rows)))
    return lines + render_steering(data["steering"])


NOTE_LABELS = {"none": "未読", "fresh": "ノートあり", "stale": "ノートの後に発話あり",
               "uncovered": "読んだ範囲の外に発話あり", "broken": "ノートを読めない"}
# 発話が無く、読む対象にならないセッション。「未読」と書くと、未読の合計と食い違って見える。
NO_PROMPT_LABELS = {True: "対象外（期間内の発話なし）", False: "対象外（ログが無い・読めない）"}


def note_label(row):
    if row["note"] not in ("fresh", "broken") and not row["prompts"]:
        return NO_PROMPT_LABELS[row["log"] in ("ok", "partial_log")]
    return NOTE_LABELS[row["note"]]


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
    if any(row["display"].endswith("/AGENTS.md") and not row["display"].startswith(("~", ".kiro/"))
           for row in weight["files"]):
        lines.append("サブディレクトリの AGENTS.md は、公式資料で常に読み込むとされているものです。"
                     "そのセッションで実際に読み込まれたかは、ここでは確かめていません。")
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
             "期間内に述べた継続的な方針は、1回でも候補にします。" % notes.PATTERN_SESSIONS, ""]
    if not candidates:
        lines.append("なし")
    for topic in candidates:
        unsure = "、うち確認事項だけのセッション %d 件" % topic["unsure_only_sessions"] \
            if topic["unsure_only_sessions"] else ""
        lines.append("- `topic:%s`：%d セッション%s、%d 回（%s）。%s" % (
            topic["topic"], topic["sessions"], unsure, topic["count"],
            "、".join(notes.KINDS[k] for k in topic["period_kinds"]), topic["summary"]))
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
        split = row.get("split", "採用")
        if row["before"] is None:
            counts = row["verdict"]
        elif row["verdict"] == "まだ日が浅い":
            counts = "%sから %s 日。まだ日が浅い（%d 日後から判定）" % (split, row["after_days"], ledger.YOUNG_DAYS)
        else:
            counts = "%s前 %d 回／%s 日、%s後 %d 回／%s 日。%s" % (
                split, row["before"], row["before_days"], split, row["after"], row["after_days"], row["verdict"])
        if row.get("observed"):
            counts += "。%s後の観測：%s %d%s" % (split, row["observed"]["unit"], row["observed"]["count"],
                                            "（%s）" % row["observed"]["note"] if row["observed"]["note"] else "")
        if row.get("applied_at"):
            applied = "、反映 %s（%s）" % (row["applied_at"], row["applied_basis"])
        elif row.get("files") and row.get("applied_basis"):
            applied = "、反映は判別できない（%s）" % row["applied_basis"]
        elif row.get("files"):
            applied = "、反映は未確認（対象のファイルに、レポートの後の変更なし）"
        else:
            applied = "、反映日は不明（対象のファイルの記載なし。採用の時刻で比べる）"
        lines.append("- `%s` %s（対象の型 `%s`、採用 %s%s）：%s" % (
            row["id"], row["title"], row["target"], row["decided_at"], applied, counts))
    lines += more_adopted
    if data.get("ambiguous_decisions"):
        lines += ["", "番号が重複した案への判断（%s）は、どの案のものか決まらないので、追跡から外しています。"
                  "レポートの番号を通し番号に直してから、判断を記録し直してください。"
                  % "、".join("`%s`" % key for key in data["ambiguous_decisions"])]
    if data["pending"]:
        lines += ["", "## 前回のレポートの未判断の案（%d 件）" % len(data["pending"]), "",
                  "最新のレポートにあり、採用も見送りも記録されていない案です。"
                  "今回のレポートに引き継ぐか、外すかを決めます。", ""]
        for row in data["pending"]:
            recent = "数えられない" if row["recent"] is None else "%d 回" % row["recent"]
            lines.append("- `%s` %s（対象の型 `%s`）：期間内の発生 %s" % (
                row["id"], row["title"], row["target"] or "記載なし", recent))
    if data.get("answers"):
        lines += ["", "## 前回までの回答（%d 件）" % len(data["answers"]), "",
                  "以前のレポートで、利用者に確かめたことです。新しい観測が無ければ、同じことを聞き直しません。"
                  "使うときは「前回の回答（<レポート名>#Q1）による」と書きます。", ""]
        answered, more = capped(data["answers"], cap, "review")
        for row in answered:
            lines.append("- `%s` %s：%s（反映：%s）" % (row["id"], row["title"], row["answer"], row["reflected"]))
        lines += more
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
            row["log"], row["prompts"], row["prompt_chars"], note_label(row),
            mask(row.get('agent') or '未確認')))
    if more:
        lines += [""] + more
    return lines


def render_session(view):
    lines = ["# セッションの要約表示 `%s`" % view["session_id"], "",
             "- ログの状態：%s" % view["status"],
             "- 記録されたエージェント：`%s`（session.json、取れない場合は未確認。途中の切り替えは未復元）" % mask(view.get('agent') or '未確認'),
             "- ユーザー発話：%d 件" % len(view["turns"]),
             "- ユーザー発話は原文、ツール実行は件数、応答は最後の1件の冒頭だけです。"
             "シェル実行は、終了コードが 0 以外のコマンドと、テストの記録設定に一致した実行の終了コードを添えます。",
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
        commands_failed = list((turn.get("failed_commands") or {}).items())
        if commands_failed:
            lines.append("- 終了コードが 0 以外のシェル実行：%s%s" % (
                "、".join("`%s`×%d" % item for item in commands_failed[:3]),
                "、ほか %d 種類" % (len(commands_failed) - 3) if len(commands_failed) > 3 else ""))
        tests = turn.get("tests") or []
        if turn.get("tests_not_run"):
            lines.append("- テストの記録設定に一致したが、実行が完了しなかった呼び出し（終了コード -1。"
                         "承認されなかった、止められた、など）：%d 回。実行には数えない" % turn["tests_not_run"])
        if tests:
            text_only = sum(1 for t in tests if t["matched"] == "text")
            piped = sum(1 for t in tests if t["matched"] == "pipe")
            lines.append("- テストの記録設定に一致したシェル実行：%d 回（終了コード %s%s）%s%s" % (
                len(tests), "…→" if len(tests) > 5 else "", "→".join(str(t["exit_code"]) for t in tests[-5:]),
                "。うちパイプの後ろの終了コード %d 回（テストの成否ではない。集約の結果は JUnit から取る）" % piped
                if piped else "",
                "。うち文面だけで一致 %d 回（実行か言及かは未確認）" % text_only if text_only else ""))
        if turn["reply"]:
            lines.append("- 応答（index=%d、%d 文字）：%s" % (
                turn["reply"]["index"], turn["reply"]["chars"], turn["reply"]["text"]))
        else:
            lines.append("- 応答：なし")
    return "\n".join(lines) + "\n"


MAX_REPORT_SUFFIX = 9   # 同じ秒に作る骨組みの数の上限（名前の並びを崩さないため、1桁まで）


def report_names(moment):
    """骨組みの名前の候補。分、秒、秒＋番号の順。どれも文字列の大小で時刻の順に並ぶ。"""
    minute = moment.strftime("%Y-%m-%d-%H%M")
    second = minute + moment.strftime("%S")
    return [minute, second] + ["%s-%d" % (second, n) for n in range(2, MAX_REPORT_SUFFIX + 1)]


def as_version(data, version):
    """集約を、以前の版のレポートが作られたときの形にそろえる。検証値と骨組みの再現に使う。

    目印の無いレポート（0）と版1・版2のときは、検証値と骨組みに効く次の3つを、その版のときの形に戻す。
      - 版3で足した所在（サブディレクトリを含む AGENTS.md）を除く。
      - 未読の数を、版2までの既読の判定（ノートを書いた後に発話が無ければ既読）で数え直す。
      - 会話からの型の候補を、版2までの判定（期間の外の継続方針も例外にする）で決め直す。
    更新しただけで、以前に検査を通ったレポートが不一致にならないようにするため。
    振り返りで読むセッションは、これまでどおり今の判定で決める（この関数は検査だけに使う）。
    版5は節を1つ足しただけで、検証値は版4と同じ。
    """
    if version >= 4:
        return data
    shaped = dict(data, diagnostics=None)   # 版4で足した診断値は、版3までの検証値に無い
    if version < 3:
        harness_rows = [row for row in data["harness"] if row.get("added_in", 0) <= max(version, 2)]
        sessions = [dict(row, note=row.get("note_v2", row["note"])) for row in data["sessions"]]
        plan = reading_plan([dict(row) for row in sessions], data["reading_plan"]["budget"])
        topics = sorted((dict(row, candidate=row.get("candidate_v2", row["candidate"])) for row in data["topics"]),
                        key=notes.order)   # 骨組みの検証値の行は、版2のときの並びで書かれている
        shaped.update(harness=harness_rows, sessions=sessions, reading_plan=plan, topics=topics)
    shaped["report_facts"] = reportcheck.facts(shaped)
    return shaped


def clues(data):
    """利用者への質問の手がかりに書ける名前。

    集約に出た型、診断値、トピック、テスト失敗と、集約の出力に `…` で出たもの（コマンド、パスなど）。
    手がかりの無い一般的な質問をさせないために使う。名前が集約にあることだけを確かめ、
    その手がかりが質問に合っているかは判定しない。
    """
    facts = data["report_facts"]
    known = set(facts.get("targets", {})) | set(facts.get("diagnostics") or [])
    known |= {"topic:" + row["topic"] for row in data["topics"]}
    known |= {"test:" + row["test_id"] for row in data["test_failures"]}
    known |= set(re.findall(r"`([^`]+)`", render(data)))
    return known


def new_report(root, data, with_debug=False):
    """レポートの骨組みを保存先に作る。

    同じ名前のファイルがあるとき、作り直すのは、このセッションが作ったまま手を付けていない骨組み
    （作った時点の内容と一致するもの）だけ。それ以外は上書きせず、別の名前で作る。
    作成は排他的に行い、同時に始めた2回の振り返りが同じファイルを使わないようにする。
    with_debug は /kiroku-retro --debug。デバッグの節に書く値を、骨組みを作った時点で控える。
    """
    directory = os.path.join(root, ".kiro", "kiroku", "data", "reports")
    data_dir = os.path.join(root, ".kiro", "kiroku", "data")
    session = os.environ.get("KIRO_SESSION_ID")
    if session and not re.fullmatch(r"[A-Za-z0-9._-]+", session):
        session = None
    found = debug.facts(root, skeleton.VERSION, session) if with_debug else None
    text = skeleton.build(root, data, GAP_REASONS, session, debug=found)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    os.makedirs(directory, exist_ok=True)
    chosen, skipped = None, []
    with recording_lock(data_dir):
        state = load_state(data_dir)
        made = state.get("report_sessions") if isinstance(state.get("report_sessions"), dict) else {}
        drafts = state.get("report_drafts") if isinstance(state.get("report_drafts"), dict) else {}
        for name in report_names(datetime.datetime.now()):
            path = os.path.join(directory, name + ".md")
            try:
                with open(path, "x", encoding="utf-8") as handle:
                    handle.write(text)
                chosen = name
                break
            except FileExistsError:
                pass
            try:
                with open(path, encoding="utf-8") as handle:
                    existing = handle.read()
            except OSError:
                existing = None
            untouched = existing is not None and drafts.get(name) == hashlib.sha256(
                existing.encode("utf-8")).hexdigest() and made.get(name) == session
            if untouched:
                with open(path + ".tmp", "w", encoding="utf-8") as handle:
                    handle.write(text)
                os.replace(path + ".tmp", path)
                chosen = name
                break
            skipped.append(name)
        if chosen is None:
            print("骨組みを作れませんでした。同じ時刻のレポートが多すぎます。1分待って実行してください。")
            return 1
        drafts[chosen] = digest
        state["report_drafts"] = drafts
        # 作った正確な時刻。名前は分（または秒）までなので、反映の判定の基準にはこちらを使う。
        made_at = state.get("report_made") if isinstance(state.get("report_made"), dict) else {}
        made_at[chosen] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        state["report_made"] = made_at
        # そのときの HEAD。反映の判定で、同じ秒のコミットの前後を時刻に頼らず決めるのに使う。
        head = Repo(root).git("rev-parse", "--verify", "--quiet", "HEAD")
        heads = state.get("report_heads") if isinstance(state.get("report_heads"), dict) else {}
        if head and head.strip():
            heads[chosen] = head.strip()
        else:
            heads.pop(chosen, None)
        state["report_heads"] = heads
        # 骨組みに書いた、記録フックのエラーの行数。検査は、この数で骨組みを作り直す（record_errors_at）。
        kept = state.get("report_record_errors") if isinstance(state.get("report_record_errors"), dict) else {}
        kept[chosen] = data["coverage"]["record_errors"]
        state["report_record_errors"] = kept
        # デバッグの節に書いた値。検査は、この控えで節を作り直す。指定が無ければ控えを消す。
        debugged = state.get("report_debug") if isinstance(state.get("report_debug"), dict) else {}
        if found is not None:
            debugged[chosen] = found
        else:
            debugged.pop(chosen, None)
        state["report_debug"] = debugged
        if session:
            made[chosen] = session
            state["report_sessions"] = made
        else:
            made.pop(chosen, None)
        save_state(data_dir, state)
    shown = os.path.relpath(os.path.join(directory, chosen + ".md"), root).replace(os.sep, "/")
    if skipped:
        print("%s は書き始められているか、別の振り返りが作ったものです。上書きせず、別の名前で作りました。"
              % "、".join("%s.md" % name for name in skipped))
    print("レポートの骨組みを作りました：%s" % shown)
    print("- 書くのは「【ここに書く…】」の欄だけです。欄を、内容で置き換えます。該当が無ければ「なし」と書きます。")
    print("- それ以外の行は、スクリプトが記録から書いたものです。消したり、言い換えたり、まとめ直したりしません。")
    print("- 欄は %d 個あります。書き終えたら、次で検査します。" % len(skeleton.SLOT.findall(text)))
    print("  aggregate.py --check-report %s" % shown)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="kiroku の記録を集約する")
    parser.add_argument("--root", default=None, help="リポジトリのルート（既定は現在の git リポジトリ）")
    parser.add_argument("--since", default="last",
                        help="開始。last（前回の振り返りの後から。書き終えたレポートが無ければすべて）、"
                             "14d のような日数、日付、または all（指定が無ければ last）")
    parser.add_argument("--until", default=None, help="終了。日数または日付")
    parser.add_argument("--by", choices=("day", "week", "all"), default="week", help="要約の単位")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    parser.add_argument("--require-agent", action="store_true", help="専用エージェントの起動を確認してから進む")
    parser.add_argument("--check-report", help="レポートの検証値と対象の型を記録と照合する")
    parser.add_argument("--new-report", action="store_true",
                        help="レポートの骨組みを保存先に作る。事実の部分はスクリプトが書く")
    parser.add_argument("--debug", action="store_true",
                        help="--new-report と一緒に使う。骨組みに、kiroku の不具合の候補（デバッグ）の節を足す")
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

    root, refused = script_root(args.root)
    if refused:
        print(refused)
        return 1
    if args.require_agent:
        context = prompts.execution_context(root)
        if context['status'] != 'verified':
            print('専用エージェントを確認できません。kiro-cli --agent kiroku-retro で起動し、/kiroku-retro を実行してください。')
            if context['status'] == 'wrong_agent':
                print('- 確認できたエージェント：%s' % mask(context['agent']))
            elif context['reason']:
                print('- 確認できなかった理由：%s' % mask(context['reason']))
            return 1
    now = datetime.datetime.now(datetime.timezone.utc)
    if args.check_report:
        notices = []
        try:
            with open(args.check_report, encoding='utf-8') as handle:
                body = handle.read()
            declared = reportcheck.extract(body)
            start = parse_bound(declared['since'], now) if declared['since'] else None
            end = parse_bound(declared['until'], now) if declared['until'] else None
            name = os.path.basename(args.check_report)
            name = name[:-3] if name.endswith('.md') else name
            data = aggregate(root, start, end, before_report=name)
            form = skeleton.version(body)
            data = as_version(data, form)   # 以前の版のレポートは、その版の検証値と骨組みで比べる
            data_dir = os.path.join(root, ".kiro", "kiroku", "data")
            state = load_state(data_dir)
            kept = record_errors_at(data_dir, state, name)
            if kept is not None:   # 骨組みを作った後に増えた記録フックのエラーで、不一致にしない
                data = dict(data, coverage=dict(data["coverage"], record_errors=kept))
            # 版ごとの規則は、版の番号で固定する（版を上げても、以前の版のレポートの扱いを変えないため）。
            # 案の番号の重複の検査は版4から、今の探し方での「確認済み」の照合も版4から当てはめる。
            errors = reportcheck.validate(body, data['report_facts'], numbers=form >= 4)
            if form:
                # 骨組みを作ったセッションは、エージェントが書き込めない state に控えてある。
                # レポートの中の記載ではなく、控えのほうで作り直して比べる。
                made_by = (state.get("report_sessions") or {}).get(name, reads.session_of(body))
                # デバッグの節も、エージェントが書き込めない state の控えから作り直して比べる。
                debugged = state.get("report_debug") if isinstance(state.get("report_debug"), dict) else {}
                found = debugged.get(name) if form >= 5 and isinstance(debugged.get(name), dict) else None
                # 版1は表記が違うので、行は照合しない（欄の残りだけを見る）。
                try:
                    expected = skeleton.build(root, data, GAP_REASONS, made_by, version=min(form, skeleton.VERSION),
                                              debug=found) if form >= 2 else ""
                except (KeyError, TypeError, ValueError):   # 控えの形が読めない
                    found = None
                    expected = skeleton.build(root, data, GAP_REASONS, made_by, version=min(form, skeleton.VERSION))
                errors += skeleton.validate(body, expected)
                if debug.HEADING in body.splitlines():
                    if found is None:
                        notices.append("kiroku の不具合の候補の節は、控えが無いので、スクリプトが書いた行を照合していません。")
                    bugs, unverified = debug.validate(root, body, {l.rstrip() for l in expected.splitlines()},
                                                      skeleton.FACTS_INTRO)
                    errors += bugs
                    notices += unverified
            if form >= 4:
                errors += reportcheck.one_step(body, data['report_facts'], data['one_step_max'])
            if skeleton.version(body) >= 2:
                errors += citations.validate(root, body)
                unread, unchecked = reads.validate(root, body, legacy=form < 4)
                errors += unread
                notices = list(notices) + unchecked
            if form >= 5:
                asked, unverified = answers.validate(root, body, clues(data), data['question_max'])
                errors += asked
                notices = list(notices) + unverified
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors, notices = ['レポートを検査できません：' + str(exc)], []
        for line in errors + notices:
            print(mask(line))
        if not errors:
            print('検証値、対象の型、スクリプトが書いた部分は記録と一致し、引用は元の会話・ファイルに実在します。'
                  '引用の解釈、本文の意味、提案の妥当性は別に確認してください。')
        return 1 if errors else 0
    if args.since == "last":
        since = since_last(root)
    else:
        since = parse_bound(args.since, now) if args.since not in ("all", "") else None
    until = parse_bound(args.until, now) if args.until else now
    if args.session:
        since = clamp(root, since)
        if args.user_chars < 1 or args.reply_chars < 1:
            parser.error("user-chars と reply-chars は1以上にしてください")
        view = prompts.distill(root, args.session, since, until, args.user_chars, args.reply_chars)
        if view["status"] in ("ok", "partial_log"):
            prompts.record_shown(root, args.session, since, until)
        if args.json:
            json.dump(view, sys.stdout, ensure_ascii=False, indent=2, default=str)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_session(view))
        return
    data = aggregate(root, since, until, args.by, args.prompts,
                     args.prompt_offset, args.prompt_limit)
    if args.new_report:
        return new_report(root, data, with_debug=args.debug)
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
