"""振り返りレポートの骨組みを作り、保存後にその部分が残っているかを確かめる。

集約が持っている事実（期間、件数、ハーネスの所在、記録の不足）は、ここで文章にする。
振り返りのエージェントが書くのは、「【ここに書く…】」の欄だけ。事実を書き写したり
言い換えたりするところで起きる誤りを、エージェントに書かせないことで避ける。
文章の意味と、改善案の妥当性は、ここでは判定しない。
"""
import collections
import datetime
import json
import os
import re
from pathlib import Path

# この形のレポートであることの目印。数字は形式の版。
#   1：骨組みをスクリプトが書く（最初の形。期間と検証値の表記が違うので、行の照合はしない）
#   2：型と改善案の主張に引用を付け、検査が引用の実在を確かめる
MARK = "<!-- kiroku-report:2 -->"
MARK_RE = re.compile(r"<!-- kiroku-report:(\d+) -->")
SLOT = re.compile(r"【ここに書く[^】]*】")
OWN_FILES = (".kiro/skills/kiroku-retro/", ".kiro/hooks/kiroku.json", ".kiro/agents/kiroku-retro.json",
             ".kiro/kiroku/")
SCOPES = {"project": "プロジェクト内", "user": "ユーザー共通"}
UNREAD_STATE = "未確認"   # 「確認の状態」に、読んでいないことを書くときの言葉


def slot(hint=""):
    return "【ここに書く%s】" % ("：" + hint if hint else "")


def version(text):
    """骨組みの形式の版。骨組みから作ったレポートでなければ 0。"""
    found = MARK_RE.search(text)
    return int(found.group(1)) if found else 0


def is_draft(text):
    """骨組みのまま、書いていない欄が残っているレポートか。"""
    return version(text) > 0 and SLOT.search(text) is not None


def _display(root, scope, path):
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    base, prefix = (Path(root), "") if scope == "project" else (Path(home), "~/.kiro/")
    try:
        return prefix + Path(path).relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _moment(value, missing):
    """人が読むための時刻。分まで、UTC。正確な値は、末尾の検証値にある。"""
    if not value:
        return missing
    try:
        moment = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _harness_rows(root, data):
    rows, own = [], 0
    for entry in data["harness"]:
        scope = SCOPES.get(entry["scope"], entry["scope"])
        if entry["status"] == "absent":
            rows.append("| %s | `%s` | なし | — | — |" % (scope, _display(root, entry["scope"], entry["path"])))
            continue
        if entry["status"] != "present":
            rows.append("| %s | `%s` | 読めない | %s | — |" % (
                scope, _display(root, entry["scope"], entry["path"]), UNREAD_STATE))
            continue
        shown = 0
        for path in entry["files"]:
            name = _display(root, entry["scope"], path)
            if entry["scope"] == "project" and name.startswith(OWN_FILES):
                own += 1
                continue
            shown += 1
            rows.append("| %s | `%s` | あり | %s | %s |" % (
                scope, name, slot("確認済み／%s" % UNREAD_STATE), slot("役割と読み込みの条件")))
        if not shown:
            rows.append("| %s | `%s` | あり（比べるファイルなし） | — | — |" % (
                scope, _display(root, entry["scope"], entry["path"])))
    return rows, own


def _gap_lines(data, reasons):
    lines = []
    cover = data["coverage"]
    if cover["log_only"]:
        lines.append("- フックの記録が無く、ログだけのセッション：%d 件（全 %d 件のうち）。"
                     "テスト失敗、ツール失敗、書き込みの数は、記録があるセッションだけが対象です。"
                     % (cover["log_only"], cover["sessions"]))
    if cover.get("record_errors"):
        lines.append("- 記録フックのエラー：%d 行（`.kiro/kiroku/data/record-errors.log`）。" % cover["record_errors"])
    broken = [s for s in data["sessions"] if s["log"] != "ok"]
    for row in broken:
        lines.append("- セッション `%s` のログ：%s。" % (row["session_id"], row["log"]))
    counted = collections.Counter()
    for gap in data["recording_gaps"]:
        counted[gap["reason"]] += gap["count"]
    for reason, count in sorted(counted.items(), key=lambda item: str(item[0])):
        lines.append("- 記録の不足 `%s`：%d 件%s。" % (
            reason, count, "（%s）" % reasons[reason] if reason in reasons else ""))
    counted = collections.Counter(gap["reason"] for gap in data["test_result_gaps"])
    for reason, count in sorted(counted.items()):
        lines.append("- テスト結果の記録不足 `%s`：%d 件。" % (reason, count))
    for row in data["unrecorded_calls"]:
        if row["kind"] == "blocked":
            hooks = "、".join("`%s`" % name for name in row["hooks"]) or "記録なし"
            lines.append("- 実行前に止められた呼び出し：`%s`、%d 回、%d セッション。止めたフックと理由は未確定"
                         "（同じターンで動いた実行前のフック：%s。止めたフックとは限らない）。"
                         % (row["command_key"], row["count"], row["sessions"], hooks))
        else:
            lines.append("- 実行中に中断された呼び出し：`%s`、%d 回、%d セッション。"
                         "承認されて実行が始まったあとの中断です。" % (
                             row["command_key"], row["count"], row["sessions"]))
    if data["note_problems"]:
        lines.append("- ノートの検査で外した項目：%d 件（引用をログと照合できなかったもの）。" % len(data["note_problems"]))
    return lines or ["- スクリプトが検出した記録の不足はありません。"]


def build(root, data, reasons=None):
    """集約から、レポートの骨組みを作る。同じ集約からは、いつも同じ文面になる。"""
    plan, cover = data["reading_plan"], data["coverage"]
    rows, own = _harness_rows(root, data)
    day = (data["until"] or "")[:10] or "期間の終わりなし"
    lines = [
        MARK,
        "# 振り返り %s" % day,
        "",
        "- 対象期間：%s 〜 %s（UTC）" % (_moment(data["since"], "最初"), _moment(data["until"], "現在")),
        "- イベント数、セッション数：%d 件、%d セッション（フックの記録あり %d、ログのみ %d）" % (
            data["event_count"], cover["sessions"], cover["recorded"], cover["log_only"]),
        "- 未読のセッション：%d 件" % (len(plan["read"]) + plan["rest"]),
        "- 読んだセッション：%s" % slot("N 件（うち今回ノートを書いたもの M 件）"),
        "- 読めなかった範囲（未確認）：%s" % slot("無ければ「なし」。下の表に「%s」があるときは、ここにも書く" % UNREAD_STATE),
        "",
        "## 比べたハーネス",
        "",
        "「存在」まではスクリプトが書いています。行を足したり、まとめ直したりしません。",
        "",
        "| 範囲 | パス | 存在 | 確認の状態 | 役割と読み込みの条件 |",
        "|---|---|---|---|---|",
    ] + rows
    if own:
        lines += ["", "kiroku 自身のファイル（%d 個）は、表から除いています。" % own]
    lines += [
        "",
        "## 観測した型・継続方針",
        "",
        slot("型ごとに `### 1. …` の見出しで書く。事実を述べる行には引用を付ける。無ければ「なし」"),
        "",
        "## ハーネスへの改善案（プロジェクト内）",
        "",
        slot("案ごとに `### P1. …` の見出しで書く。既存のファイルを変える案には、そのファイルの引用を付ける。無ければ「なし」"),
        "",
        "## ハーネスへの改善案（ユーザー共通）",
        "",
        slot("無ければ「なし」"),
        "",
        "## 引き継がなかった案",
        "",
    ]
    if data["pending"]:
        lines.append("前回のレポートの未判断の案は、次の %d 件です。" % len(data["pending"]))
        lines += ["- `%s` %s" % (row["id"], row["title"]) for row in data["pending"]]
        lines += ["", slot("上のうち、今回のレポートに載せなかったものと、その理由。すべて引き継いだなら「なし」")]
    else:
        lines.append("前回のレポートに、未判断の案はありません。")
    lines += [
        "",
        "## 様子見",
        "",
        slot("無ければ「なし」"),
        "",
        "## 記録の不足、確認事項",
        "",
        "次は、スクリプトが記録とログから数えたものです。",
        "",
    ] + _gap_lines(data, reasons or {}) + [
        "",
        slot("上への補足と、利用者に確かめたいこと。無ければ「なし」"),
        "",
        "照合用の検証値です（スクリプトが書いたもの。読む必要はありません）。",
        "",
        "```kiroku-facts",
        json.dumps(data["report_facts"], ensure_ascii=False),   # 1行にする。人が読む部分を埋もれさせない
        "```",
        "",
    ]
    return "\n".join(lines)


def fixed_parts(text):
    """骨組みのうち、スクリプトが書いた部分。行ごとに、最初の欄より前の文面。"""
    parts = []
    for line in text.splitlines():
        found = SLOT.search(line)
        head = (line[:found.start()] if found else line).rstrip()
        if head.strip():
            parts.append(head)
    return parts


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def validate(report, expected):
    """保存したレポートに、骨組みのスクリプトが書いた部分が、順番どおり残っているかを確かめる。"""
    errors = []
    lines = report.splitlines()
    if version(report) < version(expected):
        expected = ""   # 以前の版の骨組み。表記が違うので、行の照合はせず、欄の残りだけを見る
    left = SLOT.findall(report)
    if left:
        errors.append("欄の目印が %d 個残っています。内容を書き足すのではなく、目印（【ここに書く…】）ごと"
                      "置き換えてください。該当が無ければ「なし」に置き換えます：%s"
                      % (len(left), "、".join(left[:3])))
    position = 0
    for part in fixed_parts(expected):
        for index in range(position, len(lines)):
            if lines[index].rstrip().startswith(part):
                position = index + 1
                break
        else:
            errors.append("スクリプトが書いた行が、消えているか、書き換えられています：" + part)
    # 表に「未確認」があるのに、冒頭で「なし」としていないか。
    unread = [_cells(line)[1] for line in lines
              if line.startswith("|") and len(_cells(line)) >= 5 and UNREAD_STATE in _cells(line)[3]]
    for line in lines:
        if line.startswith("- 読めなかった範囲（未確認）："):
            value = line.split("：", 1)[1].strip()
            if unread and value in ("なし", "なし。", "無し"):
                errors.append("表で「%s」とした行があるのに、冒頭の「読めなかった範囲」が「なし」です：%s"
                              % (UNREAD_STATE, "、".join(unread[:3])))
    return errors
