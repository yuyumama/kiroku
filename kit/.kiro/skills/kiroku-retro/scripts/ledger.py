#!/usr/bin/env python3
"""改善案の採用・見送りを記録する台帳。人が実行する。

使い方（リポジトリのルートで）:
  python3 .kiro/skills/kiroku-retro/scripts/ledger.py list
  python3 .kiro/skills/kiroku-retro/scripts/ledger.py adopt P1
  python3 .kiro/skills/kiroku-retro/scripts/ledger.py reject P2 --reason "今は不要"
  python3 .kiro/skills/kiroku-retro/scripts/ledger.py adopt 2026-11-13-0856#P1

案は、レポートの「### P1. 見出し」と「- 対象の型：`種類:名前`」の行から読む。
番号だけを書いた場合は、最新のレポートの案を指す。
記録は .kiro/kiroku/data/ledger.jsonl に残り、集約が「採用した案の型が、その後に減ったか」を出す。
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.dont_write_bytecode = True  # 導入先に __pycache__ を作らない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from record import script_root  # noqa: E402
import skeleton  # noqa: E402

HEADING = re.compile(r"^###\s+(P\d+)[.．:：]?\s*(.*)$")
TARGET = re.compile(r"対象の型[：:]\s*`([^`]+)`")
TARGET_KINDS = ("topic", "tool", "denied", "cancelled", "timeout", "test")
YOUNG_DAYS = 3  # これより日が浅いうちは、効果を判定しない


def data_dir(root):
    return os.path.join(root, ".kiro", "kiroku", "data")


def _is_draft(path):
    """骨組みのまま、書き終えていないレポートか。書きかけは、レポートとして数えない。"""
    try:
        with open(path, encoding="utf-8") as handle:
            return skeleton.is_draft(handle.read())
    except (OSError, UnicodeError):
        return False


def report_count(root):
    """(書き終えたレポートの数, 書きかけの骨組みの数)。latest_report と同じ判定で分ける。"""
    directory = os.path.join(data_dir(root), "reports")
    try:
        paths = [os.path.join(directory, n) for n in os.listdir(directory)
                 if n.endswith(".md") and os.path.isfile(os.path.join(directory, n))]
    except OSError:
        return 0, 0
    drafts = sum(1 for path in paths if _is_draft(path))
    return len(paths) - drafts, drafts


def latest_report(root, before=None):
    """案が0件のファイルも含む、最新のレポート名。書きかけの骨組みは除く。

    before を渡すと、その名前より前のレポートの中で最新のものを返す。
    """
    directory = os.path.join(data_dir(root), "reports")
    try:
        names = [n[:-3] for n in os.listdir(directory)
                 if n.endswith(".md") and os.path.isfile(os.path.join(directory, n))
                 and (before is None or n[:-3] < before)
                 and not _is_draft(os.path.join(directory, n))]
    except OSError:
        return None
    return max(names) if names else None


def current_keys(root, report):
    """このレポートの「対象の型」が、今の見出しの形式で書かれているか。

    旧版の失敗の見出しは先頭3語だった。今の版が作るレポートには kiroku-facts の
    検証値が付くので、それがあるレポートを今の形式とみなす。レポートが読めない、
    または検証値が無いときは、旧版として扱う。
    """
    if not report:
        return False
    try:
        with open(os.path.join(data_dir(root), "reports", report + ".md"), encoding="utf-8") as handle:
            return re.search(r"^```kiroku-facts\s*$", handle.read(), re.M) is not None
    except OSError:
        return False


def proposals(root):
    """レポートから案を読む。新しいレポートが先。"""
    directory = os.path.join(data_dir(root), "reports")
    found = []
    try:
        names = sorted((n for n in os.listdir(directory) if n.endswith(".md")), reverse=True)
    except OSError:
        return found
    for name in names:
        current = None
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        if skeleton.is_draft(text):
            continue   # 書きかけの骨組み。欄の説明に出てくる見出しを、案として読まない
        lines = text.splitlines()
        for line in lines:
            heading = HEADING.match(line)
            if heading:
                current = {"id": "%s#%s" % (name[:-3], heading.group(1)), "report": name[:-3],
                           "number": heading.group(1), "title": heading.group(2).strip(), "target": None}
                found.append(current)
            elif line.startswith("#"):
                current = None
            elif current is not None and current["target"] is None:
                target = TARGET.search(line)
                if target:
                    current["target"] = target.group(1).strip()
    return found


def decisions(root):
    """案ごとの最新の判断。"""
    latest = {}
    try:
        with open(os.path.join(data_dir(root), "ledger.jsonl"), encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                    latest[entry["id"]] = entry
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return latest


def pending(root, before=None):
    """最新のレポートの案のうち、採用も見送りも記録されていないもの。

    before を渡すと、その名前のレポートを書く前の時点での「最新」を使う。

    それより古いレポートの未判断の案は、次の振り返りが引き継ぐか外すかを決めた後なので、対象にしない。
    これにより、判断するときは最新のレポートだけを見ればよい。
    """
    found = proposals(root)
    if not found:
        return []
    known = decisions(root)
    latest = latest_report(root, before)
    return [p for p in found if p["report"] == latest and p["id"] not in known]


def duplicated(found):
    """同じ ID（レポート名#番号）が複数の案を指すもの。その ID の判断は、どの案のものか決まらない。"""
    seen = {}
    for proposal in found:
        seen[proposal["id"]] = seen.get(proposal["id"], 0) + 1
    return {key for key, count in seen.items() if count > 1}


def resolve(root, name):
    """ID から案を引く。見つからなければ None、同じ ID の案が複数あれば、その一覧。"""
    found = proposals(root)
    if "#" not in name:
        if not found:
            return None
        name = "%s#%s" % (latest_report(root), name.upper())
    matched = [p for p in found if p["id"] == name]
    if len(matched) > 1:
        return matched
    return matched[0] if matched else None


def record(root, proposal, decision, target=None, reason=None):
    entry = {"id": proposal["id"], "decision": decision, "title": proposal["title"],
             "target": target or proposal["target"], "reason": reason,
             "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")}
    os.makedirs(data_dir(root), exist_ok=True)
    with open(os.path.join(data_dir(root), "ledger.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def _parse(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def tracking(root, occurrences, data_start, now):
    """採用・見送りした案について、対象の型がその後に何回出たかを数える。

    occurrences は、対象の型（`tool:npm run build` など）から発生時刻の一覧を返す関数。
    分からない型には None を返す。
    """
    adopted, rejected = [], []
    for entry in sorted(decisions(root).values(), key=lambda e: e["ts"]):
        decided = _parse(entry["ts"])
        row = {"id": entry["id"], "title": entry.get("title"), "target": entry.get("target"),
               "decided_at": entry["ts"], "reason": entry.get("reason")}
        times = occurrences(entry.get("target"), str(entry.get("id") or "").partition("#")[0]) \
            if entry.get("target") else None
        after_days = max((now - decided).total_seconds() / 86400, 0)
        row["after_days"] = round(after_days, 1)
        if times is None:
            row.update(before=None, after=None, before_days=None, verdict="対象の型を数えられない")
        else:
            # 採用後と同じ長さの期間を、採用前にも取る。記録が無い期間までは遡らない。
            available = max((decided - data_start).total_seconds() / 86400, 0) if data_start else 0
            before_days = min(max(after_days, 7), available)
            start = decided - datetime.timedelta(days=before_days)
            row["before"] = sum(1 for t in times if start <= t < decided)
            row["after"] = sum(1 for t in times if t >= decided)
            row["before_days"] = round(before_days, 1)
            if after_days < YOUNG_DAYS:
                row["verdict"] = "まだ日が浅い"
            elif row["before"] == 0:
                row["verdict"] = "採用前の記録が無い"
            elif row["after"] == 0:
                row["verdict"] = "再発なし"
            elif row["after"] / after_days < 0.5 * row["before"] / before_days:
                row["verdict"] = "減った"
            else:
                row["verdict"] = "減っていない"
        (adopted if entry["decision"] == "adopted" else rejected).append(row)
    return adopted, rejected


def main(argv=None):
    parser = argparse.ArgumentParser(description="改善案の採用・見送りを記録する")
    parser.add_argument("--root", default=None, help="リポジトリのルート（既定は現在の git リポジトリ）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="レポートの案と、これまでの判断を一覧にする")
    for name, text in (("adopt", "採用した"), ("reject", "見送った")):
        sub = commands.add_parser(name, help="案を%sと記録する" % text)
        sub.add_argument("id", help="案の番号（P1）または レポート名#P1")
        sub.add_argument("--target", help="対象の型。レポートに書かれていないときに指定する")
        sub.add_argument("--reason", help="理由のメモ")
    args = parser.parse_args(argv)
    root, refused = script_root(args.root)
    if refused:
        print(refused)
        return 1

    if args.command == "list":
        known = decisions(root)
        found = proposals(root)
        if not found:
            print("レポートに案がありません。")
            return 0
        labels = {"adopted": "採用", "rejected": "見送り"}
        latest = latest_report(root)
        ambiguous = duplicated(found)
        for proposal in found:
            # 古いレポートの未判断の案は、その後の振り返りが引き継ぐか外すかを決めている。
            undecided = "未判断" if proposal["report"] == latest else "古いレポート。最新を見てください"
            state = labels.get(known.get(proposal["id"], {}).get("decision"), undecided)
            if proposal["id"] in ambiguous:
                # 記録された判断が、同じ番号のどの案のものかは推測しない。
                recorded = known.get(proposal["id"])
                state = "番号が重複。%s" % (
                    "記録された判断（%s：%s）がどの案のものかは不明" % (
                        labels.get(recorded.get("decision"), "?"), recorded.get("title") or "題名なし")
                    if recorded else "この番号では判断を記録できません")
            print("%s  [%s]  %s  （対象の型：%s）" % (
                proposal["id"], state, proposal["title"], proposal["target"] or "記載なし"))
        return 0

    proposal = resolve(root, args.id)
    if proposal is None:
        print("案が見つかりません: %s（ledger.py list で確かめてください）" % args.id)
        return 1
    if isinstance(proposal, list):
        print("%s は、同じレポートの %d 個の案に使われています。どの案の判断か決まらないので、記録しません：%s"
              % (proposal[0]["id"], len(proposal), "／".join(p["title"] for p in proposal)))
        print("レポートの番号を通し番号に直してから、もう一度記録してください。")
        return 1
    target = args.target or proposal["target"]
    if target and target.split(":", 1)[0] not in TARGET_KINDS:
        print("対象の型は %s のいずれかで始めてください: %s" % ("、".join(k + ":" for k in TARGET_KINDS), target))
        return 1
    entry = record(root, proposal, "adopted" if args.command == "adopt" else "rejected",
                   target, args.reason)
    print("記録しました：%s を%s（対象の型：%s）" % (
        entry["id"], "採用" if entry["decision"] == "adopted" else "見送り", entry["target"] or "記載なし"))
    if not entry["target"]:
        print("対象の型が無いので、その後の増減は追跡できません。--target で指定できます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
