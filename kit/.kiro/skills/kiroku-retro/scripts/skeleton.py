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
#   3：節の中の欄（引き継がなかった案の理由、記録の不足への補足）に見出しの行を付け、
#      欄が空のままのレポートを、骨組みが無くても見分けられるようにする
#   4：一歩先の改善案の節と、ハーネスの診断の節を足し、人が読む順に並べ替える
#   5：冒頭の要約の次に、利用者に確かめたこと（案を作る前の質問と答え）の節を足す
#   6：手作業の確認のあるトピックを、セッションの数にかかわらず対象の型にする（検証値が変わる）。
#      一歩先の改善案と質問の手がかりに、比べたハーネスの表のファイルを書けるようにする
#   7：ハーネスの診断の後に、ベストプラクティス集との照らし合わせの節を足す（骨組みを作った時点の控えから書く）。
#      候補と未確認の practice:<ID> を、一歩先の改善案の手がかりと、対象の型に書けるようにする
#   8：承認の繰り返しの候補（approval:<見出し>）を、対象の型に足す（検証値が変わる）
#   9：approval:・practice: を書いた案に、対象の型の行を求める（骨組みの文面は版8と同じ）
#   10：冒頭に「まとめ」の欄を足す。専門家ではない利用者が、最初の数行で要点と決めることを読めるようにする
VERSION = 10
SUMMARY_HEADING = "## まとめ"
PRACTICES_HEADING = "## ベストプラクティス集との照らし合わせ"
ONE_STEP_HEADING = "## 一歩先の改善案"
ONE_STEP_DEFAULT = None   # 一歩先の改善案の上限の既定値（設定の one_step_max）。None は上限なし
QUESTION_HEADING = "## 利用者に確かめたこと"
QUESTION_DEFAULT = None   # 利用者に聞く質問の上限の既定値（設定の question_max）。None は上限なし
FACTS_INTRO = "照合用の検証値です（スクリプトが書いたもの。読む必要はありません）。"
MARK = "<!-- kiroku-report:%d -->" % VERSION
MARK_RE = re.compile(r"<!-- kiroku-report:(\d+) -->")
SLOT = re.compile(r"【ここに書く[^】]*】")
OWN_FILES = (".kiro/skills/kiroku-retro/", ".kiro/hooks/kiroku.json", ".kiro/agents/kiroku-retro.json",
             ".kiro/kiroku/")
SCOPES = {"project": "プロジェクト内", "user": "ユーザー共通"}
OWN_COUNT = "kiroku 自身のファイル（"   # 表から除いた kiroku のファイルの数の行の頭
UNREAD_STATE = "未確認"   # 「確認の状態」に、読んでいないことを書くときの言葉


def _how_many(limit):
    """欄の説明に書く件数。上限が無ければ、あるだけ全部。"""
    return "件数の上限なし（あれば全部）" if limit is None else "最大 %d 件" % limit


def table_paths(root, data):
    """比べたハーネスの表に、ファイルとして出るパス（kiroku 自身のファイルを除く）。

    版6から、一歩先の改善案と質問の手がかりに書ける。文書に書かれた手作業の確認から案を作るため。
    """
    names = []
    for entry in data["harness"]:
        if entry["status"] != "present":
            continue
        for path in entry["files"]:
            name = _display(root, entry["scope"], path)
            if not (entry["scope"] == "project" and name.startswith(OWN_FILES)):
                names.append(name)
    return names


def slot(hint=""):
    return "【ここに書く%s】" % ("：" + hint if hint else "")


def version(text):
    """骨組みの形式の版。骨組みから作ったレポートでなければ 0。"""
    found = MARK_RE.search(text)
    return int(found.group(1)) if found else 0


# 欄を埋めたあとも値が空であってはならない行と節。目印だけを消した空欄を、書き終えたとみなさない。
REQUIRED_LINES = ("- 読んだセッション：", "- 読めなかった範囲（未確認）：",
                  "- 載せなかった案と理由：", "- 補足と、利用者に確かめたいこと：")
REQUIRED_SECTIONS = (SUMMARY_HEADING, QUESTION_HEADING, "## 一歩先の改善案", "## 観測した型・継続方針",
                     "## ハーネスへの改善案（プロジェクト内）", "## ハーネスへの改善案（ユーザー共通）", "## 様子見")
TABLE_HEAD = "| 範囲 | パス | 存在 | 確認の状態 | 役割と読み込みの条件 |"


def blank_fields(text):
    """欄のうち、値が空のもの。目印を消しただけの空欄を見つける。意味は判定しない。

    「なし」と書いた欄は空ではない。版3以降のレポートだけに当てはめる。版2までのレポートは、
    作ったときの版の規則（目印が残っていないこと）のまま読む。更新しただけで、以前に書き終えた
    レポートが書きかけに変わらないようにするため。
    """
    if version(text) < 3:
        return []
    lines = text.splitlines()
    found = []
    for label in REQUIRED_LINES:
        for line in lines:
            if line.startswith(label) and not line[len(label):].strip():
                found.append(label[2:].rstrip("："))
    for heading in REQUIRED_SECTIONS:
        if heading not in lines:
            continue
        body = []
        for line in lines[lines.index(heading) + 1:]:
            if line.startswith("## "):
                break
            if line.strip():
                body.append(line)
        if not body:
            found.append(heading[3:])
    if TABLE_HEAD in lines:
        for line in lines[lines.index(TABLE_HEAD) + 2:]:
            if not line.startswith("|"):
                break
            cells = _cells(line)
            if len(cells) != 5:
                found.append("比べたハーネスの表の行（列が %d 個）：%s" % (len(cells), line.strip()))
            elif cells[2] == "あり" and not (cells[3] and cells[4]):
                found.append("比べたハーネスの表の「確認の状態」または「役割」：%s" % cells[1])
    return found


def is_draft(text):
    """骨組みのまま、書いていない欄が残っているレポートか。目印だけを消した空欄も、書きかけとする。"""
    return version(text) > 0 and (SLOT.search(text) is not None or bool(blank_fields(text)))


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


def build(root, data, reasons=None, session=None, version=VERSION, debug=None, practices=None):
    """集約から、レポートの骨組みを作る。同じ集約からは、いつも同じ文面になる。

    practices は、ベストプラクティス集との照らし合わせの控え（practices.snapshot）。版7から、渡したときだけ
    ハーネスの診断の後に節を足す。集を更新しても、以前のレポートの行が変わらないよう、控えから書く。

    debug は、/kiroku-retro --debug のときに骨組みを作った時点で控えた値（debug.facts）。
    渡したときだけ、末尾の検証値の前に「kiroku の不具合の候補（デバッグ）」の節を足す（版5から）。

    session は、振り返りをしているセッション。「確認済み」の照合に使うので、骨組みに控える。
    version は形式の版。以前の版のレポートを検査するとき、その版の骨組みを作り直すのに使う
    （data も、その版の検証値にそろえたものを渡す。aggregate.as_version）。
    版4は、人が読む順に並べ替えた。一歩先の改善案を先頭に置き、比較表と診断値は後ろに回す。
    版5は、冒頭の要約の次に、利用者に確かめたことを置く。案は、その答えを前提にするため。
    版4までは、作ったときの並びのまま作る（以前のレポートの検査で、行を照合するため）。
    """
    plan, cover = data["reading_plan"], data["coverage"]
    rows, own = _harness_rows(root, data)
    day = (data["until"] or "")[:10] or "期間の終わりなし"
    header = ["<!-- kiroku-report:%d -->" % version] + (["<!-- kiroku-session:%s -->" % session] if session else []) + [
        "# 振り返り %s" % day,
        "",
        "- 対象期間：%s 〜 %s（UTC）" % (_moment(data["since"], "最初"), _moment(data["until"], "現在")),
        "- イベント数、セッション数：%d 件、%d セッション（フックの記録あり %d、ログのみ %d）" % (
            data["event_count"], cover["sessions"], cover["recorded"], cover["log_only"]),
        "- 未読のセッション：%d 件" % (len(plan["read"]) + plan["rest"]),
        "- 読んだセッション：%s" % slot("N 件（うち今回ノートを書いたもの M 件）"),
        "- 読めなかった範囲（未確認）：%s" % slot("無ければ「なし」。下の表に「%s」があるときは、ここにも書く" % UNREAD_STATE),
    ]
    table = [
        "",
        "## 比べたハーネス",
        "",
        "「存在」まではスクリプトが書いています。行を足したり、まとめ直したりしません。",
        "",
        "| 範囲 | パス | 存在 | 確認の状態 | 役割と読み込みの条件 |",
        "|---|---|---|---|---|",
    ] + rows
    if own:
        table += ["", OWN_COUNT + "%d 個）は、表から除いています。" % own]
    body = [
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
        body.append("前回のレポートの未判断の案は、次の %d 件です。" % len(data["pending"]))
        body += ["- `%s` %s" % (row["id"], row["title"]) for row in data["pending"]]
        body += ["", ("- 載せなかった案と理由：" if version >= 3 else "") + slot("上のうち、今回のレポートに載せなかったものと、その理由。すべて引き継いだなら「なし」")]
    else:
        body.append("前回のレポートに、未判断の案はありません。")
    body += [
        "",
        "## 様子見",
        "",
        slot("無ければ「なし」"),
    ]
    tail = [
        "",
        "## 記録の不足、確認事項",
        "",
        "次は、スクリプトが記録とログから数えたものです。",
        "",
    ] + _gap_lines(data, reasons or {}) + [
        "",
        "- 補足と、利用者に確かめたいこと：" + slot("上への補足。無ければ「なし」") if version >= 3
        else slot("上への補足と、利用者に確かめたいこと。無ければ「なし」"),
        "",
        FACTS_INTRO,
        "",
        "```kiroku-facts",
        json.dumps(data["report_facts"], ensure_ascii=False),   # 1行にする。人が読む部分を埋もれさせない
        "```",
        "",
    ]
    if version < 4:
        return "\n".join(header + table + body + tail)
    import diagnose   # 版4から（ledger → skeleton の読み込みの順に関わらないよう、ここで読む）
    limit = data.get("one_step_max", ONE_STEP_DEFAULT)
    one_step = [
        "",
        ONE_STEP_HEADING,
        "",
        slot("書かない設定（one_step_max が 0）なので「なし」" if limit == 0 else
             "観測した型・診断値より一段上の案を、%s。`### P1. …` の見出しで書き、番号は下の改善案と通しにする。"
             "観点・段・手がかり%s・確かめ方を書く。無ければ「なし」" % (
                 _how_many(limit), "（集約の型、診断値、比べたハーネスの表のファイル。ファイルなら、その引用も）"
                 if version >= 6 else "")),
    ]
    diagnostics = [
        "",
        "## ハーネスの診断",
        "",
        "次は、スクリプトがファイルと記録から決めた値です。一歩先の改善案の手がかりに使います。",
        "",
    ] + diagnose.lines(data["diagnostics"], data["burden"], diagnose.REPORT_CAP)
    if version < 5:
        return "\n".join(header + one_step + body + table + diagnostics + tail)
    asked = data.get("question_max", QUESTION_DEFAULT)
    questions = [
        "",
        QUESTION_HEADING,
        "",
        slot("質問しない設定（question_max が 0）なので「なし」" if asked == 0 else
             "案を作る前に利用者に聞いた質問を、%s。`### Q1. …` の見出しで、手がかり、見立て、回答、"
             "引用（回答）、反映を書く。聞かなかったときは「なし」" % _how_many(asked)),
    ]
    if version >= 7 and practices is not None:
        diagnostics += [
            "",
            PRACTICES_HEADING,
            "",
            "次は、スクリプトがファイルと記録から決めた状態です（集の版 %s。骨組みを作った時点の控え）。"
            "「候補」と「未確認」の `practice:<ID>` は、一歩先の改善案の手がかりと、対象の型に使えます。"
            "集の文面は引用しません。" % practices.get("version"),
            "",
        ] + list(practices.get("lines") or [])
    if debug is not None:
        import debug as debugging   # debug は reads を経て skeleton を読むので、ここで読む
        at = tail.index(FACTS_INTRO)
        tail = tail[:at] + [
            debugging.HEADING,
            "",
            debugging.PREAMBLE + "kiroku の開発者に渡すためのものです。"
            "渡す前に、パス、コマンド、引用に、見せたくないものが無いかを確かめてください。",
            "",
            "次は、スクリプトが記録から書いたものです。",
            "",
        ] + debugging.lines(debug) + [
            "",
            slot("kiroku の不具合と推定されるものを、`### B1. …` の見出しで、現象、期待、根拠と引用、"
                 "再現の手がかり、確度、影響を書く。無ければ「なし」"),
            "",
        ] + tail[at:]
    summary = [
        "",
        SUMMARY_HEADING,
        "",
        slot("専門用語を使わず、5行以内。1行目に、いちばん大事なこと。続けて、決めてほしいこと（案の番号と、"
             "ふだんの言葉でひとこと）。変えることが無ければ「今回は、変えることはありません」"),
    ] if version >= 10 else []
    return "\n".join(header + summary + questions + one_step + body + table + diagnostics + tail)


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
    blank = blank_fields(report)
    if blank:
        errors.append("欄が空のままです。内容を書くか、該当が無ければ「なし」と書いてください：%s"
                      % "、".join(blank))
    position = 0
    for part in fixed_parts(expected):
        # kiroku 自身のファイルの数は、kiroku を更新すると変わる（ファイルが増える）。記録の事実ではないので、
        # 数までは照合しない。照合すると、更新しただけで以前のレポートが不一致になる（2026-11-25 の実機の確認）。
        if part.startswith(OWN_COUNT):
            part = OWN_COUNT
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
