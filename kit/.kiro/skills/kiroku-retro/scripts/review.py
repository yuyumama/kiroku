#!/usr/bin/env python3
"""見直し用の要約。数だけを出し、コード・コマンド・発話・パスは出さない。

使い方（リポジトリのルートで）:
  python3 .kiro/skills/kiroku-retro/scripts/review.py [--since 14d]

kiroku の使い方を見直すとき（どの信号が役に立ったか、振り返りの質は十分か）に、
リポジトリの中身を見せずに状況を共有するためのもの。
"""
import argparse
import collections
import datetime
import os
import sys

sys.dont_write_bytecode = True  # 導入先に __pycache__ を作らない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aggregate  # noqa: E402
import ledger  # noqa: E402
import notes  # noqa: E402


def count_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def summarize(root, since, until):
    data = aggregate.aggregate(root, since, until, by="week")
    lines = ["# kiroku 見直し用の要約", "",
             "数だけを出しています。コード、コマンド、発話、パスは含みません。", "",
             "- 期間：%s 〜 %s" % (data["since"] or "最初", data["until"] or "現在"),
             "- イベント数：%d" % data["event_count"]]

    lines += ["", "## 週ごとの記録", "",
              "| 週 | セッション | 発話 | テスト実行 | Red 候補 | 回帰候補 | マージ中 | その他 | ツール失敗 "
              "| 取り消し | タイムアウト | 拒否 | 書き込み |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, row in data["periods"].items():
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d |" % (
            name, row["sessions"], row["prompts"], row["test_runs"], row[aggregate.RED],
            row[aggregate.REGRESSION], row[aggregate.MERGE], row[aggregate.OTHER], row["tool_failures"],
            row["cancelled"], row["timeouts"], row["denied"], row["writes"]))

    lines += ["", "## 信号ごとの種類の数と、繰り返し", "",
              "| 信号 | 種類 | うち2回以上 | うち2セッション以上 | 最多の回数 |", "|---|---|---|---|---|"]

    def signal(label, rows):
        counts = [r["count"] for r in rows]
        lines.append("| %s | %d | %d | %d | %d |" % (
            label, len(rows), sum(1 for c in counts if c >= 2),
            sum(1 for r in rows if r["sessions"] >= 2), max(counts) if counts else 0))

    for kind, label in ((aggregate.REGRESSION, "回帰候補"), (aggregate.MERGE, "マージ中の失敗"),
                        (aggregate.OTHER, "その他のテスト失敗"),
                        (aggregate.RED, "Red 候補")):
        signal(label, [r for r in data["test_failures"] if r["class"] == kind])
    for kind, label in (("tool_failure", "ツール失敗"), ("tool_cancelled", "人による取り消し"),
                        ("tool_timeout", "タイムアウト"), ("tool_denied", "権限による拒否")):
        signal(label, [r for r in data["tool_failures"] if r["type"] == kind])
    lines += ["", "- 書き直し候補：%d 件" % len(data["rework_candidates"]),
              "- 人間の手直し候補：%d 件（merge などで除いたもの %d 件）" % (
                  len(data["fix_candidates"]), len(data["fix_excluded"])),
              "- 判定できなかったシェル実行（recording_gap）：%d 件" % len(data["recording_gaps"]),
              "- 記録処理のエラー（record-errors.log）：%d 行" % count_lines(
                  os.path.join(root, ".kiro", "kiroku", "data", "record-errors.log"))]

    sessions = data["sessions"]
    logs = collections.Counter(s["log"] for s in sessions)
    noted = collections.Counter(s["note"] for s in sessions)
    lines += ["", "## セッションと会話", "",
              "- セッション：%d 件（記録あり %d、ログのみ %d）" % (
                  len(sessions), sum(1 for s in sessions if s["recorded"]),
                  sum(1 for s in sessions if not s["recorded"])),
              "- ログの状態：%s" % ("、".join("%s %d" % item for item in sorted(logs.items())) or "なし"),
              "- 振り返りノート：あり %d、ノートの後に発話あり %d、未読 %d" % (
                  noted["fresh"], noted["stale"], noted["none"]),
              "- 発話：合計 %d 件、%d 文字" % (sum(s["prompts"] for s in sessions),
                                         sum(s["prompt_chars"] for s in sessions))]

    topics = data["topics"]
    kinds = collections.Counter(k for t in topics for k in t["kinds"])
    spread = collections.Counter(min(t["sessions"], 3) for t in topics)
    lines += ["", "## 会話からの型", "",
              "- トピック：%d 個（型の候補 %d 個）" % (len(topics), sum(1 for t in topics if t["candidate"])),
              "- 出たセッション数ごとのトピック数：1件 %d、2件 %d、3件以上 %d" % (spread[1], spread[2], spread[3]),
              "- 種類：%s" % ("、".join("%s %d" % (notes.KINDS[k], n) for k, n in sorted(kinds.items())) or "なし"),
              "- ノートの検査で外したもの：%d 件" % len(data["note_problems"])]

    proposals = ledger.proposals(root)
    known = ledger.decisions(root)
    by_kind = collections.defaultdict(collections.Counter)
    for proposal in proposals:
        decision = known.get(proposal["id"], {}).get("decision", "undecided")
        target = known.get(proposal["id"], {}).get("target") or proposal["target"] or ""
        by_kind[target.split(":", 1)[0] if ":" in target else "対象の型なし"][decision] += 1
    verdicts = collections.Counter(row["verdict"] for row in data["adopted"])
    reports = os.path.join(root, ".kiro", "kiroku", "data", "reports")
    lines += ["", "## 改善案と、その後", "",
              "- レポート：%d 個、案：%d 件" % (
                  len([n for n in os.listdir(reports) if n.endswith(".md")]) if os.path.isdir(reports) else 0,
                  len(proposals)),
              "", "| 対象の型の種類 | 採用 | 見送り | 未判断 |", "|---|---|---|---|"]
    for kind, counts in sorted(by_kind.items()):
        lines.append("| %s | %d | %d | %d |" % (kind, counts["adopted"], counts["rejected"], counts["undecided"]))
    if not by_kind:
        lines.append("| （案なし） | 0 | 0 | 0 |")
    lines += ["", "- 採用した案の追跡：%s" % ("、".join("%s %d" % item for item in sorted(verdicts.items())) or "なし"),
              "- 見送った案のうち、その後も発生があるもの：%d 件" % sum(
                  1 for row in data["rejected"] if row["after"])]

    weight = data["steering"]
    modes = collections.Counter(f["inclusion"] for f in weight["files"])
    lines += ["", "## ハーネスの重さ", "",
              "- steering と AGENTS.md：%d 個（%s）" % (
                  len(weight["files"]), "、".join("%s %d" % item for item in sorted(modes.items())) or "なし"),
              "- 常時読み込みの合計：%d 文字（プロジェクト内 %d、ユーザー共通 %d）" % (
                  weight["always_total"], weight["always_chars"]["project"], weight["always_chars"]["user"])]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="見直し用に、数だけの要約を出す")
    parser.add_argument("--root", default=None, help="リポジトリのルート（既定は現在の git リポジトリ）")
    parser.add_argument("--since", default="14d", help="開始。14d のような日数、日付、または all（既定 14d）")
    args = parser.parse_args(argv)
    root = args.root
    if root is None:
        top = aggregate.Repo(os.getcwd()).git("rev-parse", "--show-toplevel")
        root = top.strip() if top else os.getcwd()
    now = datetime.datetime.now(datetime.timezone.utc)
    since = aggregate.parse_bound(args.since, now) if args.since not in ("all", "") else None
    sys.stdout.write(summarize(root, since, now))


if __name__ == "__main__":
    main()
