#!/usr/bin/env python3
"""レポートの「kiroku の不具合の候補（デバッグ）」の節を作り、確かめる。

/kiroku-retro --debug のときだけ、骨組みにこの節を足す（aggregate.py --new-report --debug）。
kiroku の開発者に渡すためのもの。環境と、振り返りの中で失敗したシェル実行は、スクリプトが記録から書く。
不具合の推定は、振り返りのエージェントが「現象、期待、根拠、再現の手がかり、確度、影響」の形で書く。
推定が本当に不具合かは判定しない。根拠の引用が実在するかは確かめる。

使い方（人が、導入先の指紋と kiroku のリポジトリの版を比べるとき）:
  python3 kit/.kiro/skills/kiroku-retro/scripts/debug.py --fingerprint
"""
import argparse
import collections
import hashlib
import os
import platform
import re
import sys

sys.dont_write_bytecode = True  # 導入先に __pycache__ を作らない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commands  # noqa: E402
import excerpt  # noqa: E402
import notes  # noqa: E402
import prompts  # noqa: E402
import reads  # noqa: E402
from record import mask, not_run_kind  # noqa: E402

HEADING = "## kiroku の不具合の候補（デバッグ）"
BUG = re.compile(r"^###\s+(B\d+)[.．:：]?\s*(.*)$")
FIELD = re.compile(r"^\s*[-*]\s*(現象|期待|根拠|再現の手がかり|確度|影響)[：:]\s*(.*)$")
REQUIRED = ("現象", "期待", "根拠", "再現の手がかり", "確度", "影響")
CONFIDENCE = ("確認済み", "推定")
OUTPUT = re.compile(r"引用（出力）[：:]\s*(.*)$")
FILE_CITATION = re.compile(r"引用（ファイル）[：:]")
INDEX = re.compile(r"index\s*[=＝]?\s*(\d+)")
SHELL_TOOLS = ("execute_bash", "execute_pwsh")
EXIT_CODE_LINE = re.compile(r"(?m)^Exit Code:\s*(-?\d+)\s*$")   # 応答の、行としての終了コード
ERROR_TYPE = re.compile(r"^\d{4}-\d\d-\d\dT\S+\s+([A-Za-z_][\w.]*)\s*:")   # record.py が書く「時刻 例外名: 文面」
TRACEBACK = "Traceback (most recent call last)"
# スクリプトが節に書く行の書き出し。控えが無く、骨組みを作り直せないときも、エージェントが書いた行と見分ける。
PREAMBLE = "この節は `/kiroku-retro --debug` のときだけ作ります。"
SCRIPT_LINES = (PREAMBLE, "次は、スクリプトが記録から書いたものです。", "- 環境：", "- Kiro の版：",
                "- この振り返りで失敗したシェル実行", "  - index ", "- 記録フックのエラー（骨組みを作った時点まで）")
NOT_RUN = {"tool_denied": "権限の規則で拒否", "tool_cancelled": "取り消し", "tool_timeout": "タイムアウト"}
CLIP = 200   # 骨組みに写すコマンドとエラーの行の、文字数の上限（表示の幅。判定には使わない）


def _clip(text):
    text = mask(" ".join(str(text).split())).replace("`", "'")
    return text if len(text) <= CLIP else text[:CLIP] + "…"


def fingerprint(directory=None):
    """kiroku のスクリプト（scripts/*.py）の指紋。SHA-256 の先頭12桁。

    kiroku には版の番号が無い。インストーラーはスクリプトを書き換えずに写すので（Python の起動名を
    置き換えるのは .json と .md だけ）、この指紋で導入した kiroku の版が分かる。改行の違い
    （Windows の CRLF）は、そろえてから計算する。
    """
    directory = directory or os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for name in sorted(n for n in os.listdir(directory) if n.endswith(".py")):
        with open(os.path.join(directory, name), "rb") as handle:
            content = handle.read().replace(b"\r\n", b"\n")
        digest.update(name.encode("utf-8") + b"\0" + content + b"\0")
    return digest.hexdigest()[:12]


def _command(payload):
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    command = args.get("command")
    return command if isinstance(command, str) else ""


def failed_calls(root, session):
    """この振り返りのログにある、失敗したシェル実行。読めなければ、その理由（文字列）。

    拾うのは、kiroku のスクリプトの失敗（終了コードが0以外、Traceback、実行が完了しなかった）と、
    権限の規則で拒否されたシェル実行（kiroku 以外のコマンドも含む。手順で許していないことを
    しようとした跡）。利用者が取り消した kiroku 以外のコマンドは、不具合ではないので拾わない。
    骨組みを作る呼び出し（--new-report）より前の項目だけを見る。
    """
    if not session:
        return "振り返りのセッションが分からない（KIRO_SESSION_ID が無い）"
    path = prompts.running_session_log(root, session)
    if not path:
        return "振り返りのセッションのログを読めない"
    try:
        rows = excerpt.load(path, types=None)
    except (OSError, UnicodeError):
        return "振り返りのセッションのログを読めない"
    end = len(rows)
    for position, (_, payload) in enumerate(rows):
        if payload.get("type") == "tool_call" and "--new-report" in _command(payload):
            end = position
    calls, found = {}, []
    for position, (_, payload) in enumerate(rows[:end]):
        kind = payload.get("type")
        if kind == "tool_call" and payload.get("toolName") in SHELL_TOOLS and _command(payload):
            calls[payload.get("toolCallId")] = (position, _command(payload))
            continue
        if kind != "tool_result" or payload.get("toolCallId") not in calls:
            continue
        position, command = calls.pop(payload.get("toolCallId"))
        content = payload.get("content") if isinstance(payload.get("content"), str) else excerpt.content_text(payload)
        codes = EXIT_CODE_LINE.findall(content)
        code = int(codes[-1]) if codes else None
        own = commands.own_script(command) is not None
        if code == -1 or (code is None and payload.get("success") is False):
            reason = NOT_RUN.get(not_run_kind(content), "実行が完了しなかった")
            if own or reason == NOT_RUN["tool_denied"]:
                found.append({"index": position, "command": _clip(command), "result": reason})
        elif own and (code not in (0, None) or TRACEBACK in content):
            result = "終了コード %s" % (code if code is not None else "なし")
            found.append({"index": position, "command": _clip(command),
                          "result": result + ("、Traceback あり" if TRACEBACK in content else "")})
    return found


def hook_errors(data_dir):
    """記録フックのエラー（record-errors.log）の行の数、例外の種類ごとの件数、最後の行。"""
    lines, kinds, last = 0, collections.Counter(), None
    try:
        with open(os.path.join(data_dir, "record-errors.log"), encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines += 1
                found = ERROR_TYPE.match(line)
                if found:
                    kinds[found.group(1)] += 1
                if line.strip():
                    last = line.strip()
    except OSError:
        pass
    return {"lines": lines, "types": dict(sorted(kinds.items())), "last": _clip(last) if last else None}


def facts(root, version, session):
    """骨組みを作るときに控える値。検査は、この控えから同じ文面を作り直す。"""
    return {"environment": {"os": "%s（%s）" % (platform.system() or "不明", platform.machine() or "不明"),
                            "python": platform.python_version(), "format": version, "fingerprint": fingerprint()},
            "failures": failed_calls(root, session),
            "errors": hook_errors(os.path.join(root, ".kiro", "kiroku", "data"))}


def lines(found):
    """控えた値を、骨組みの行にする。同じ控えからは、いつも同じ文面になる。"""
    env = found["environment"]
    out = ["- 環境：%s、Python %s、骨組みの版 %s、kiroku のスクリプトの指紋 `%s`"
           % (env["os"], env["python"], env["format"], env["fingerprint"]),
           "- Kiro の版：スクリプトでは取っていません（未確認）。共有するときは、`kiro-cli --version` の出力を添えてください。"]
    failures = found["failures"]
    if isinstance(failures, str):
        out.append("- この振り返りで失敗したシェル実行：出していません（%s）。" % failures)
    elif not failures:
        out.append("- この振り返りで失敗したシェル実行（kiroku のスクリプトの失敗と、権限の規則による拒否）：なし。")
    else:
        out.append("- この振り返りで失敗したシェル実行（kiroku のスクリプトの失敗と、権限の規則による拒否）：%d 件"
                   % len(failures))
        out += ["  - index %d `%s`：%s" % (row["index"], row["command"], row["result"]) for row in failures]
    errors = found["errors"]
    if errors["lines"]:
        kinds = "、".join("%s %d 件" % item for item in errors["types"].items()) or "例外の種類は読めない"
        out.append("- 記録フックのエラー（骨組みを作った時点まで）：%d 行（%s）。最後の行：`%s`"
                   % (errors["lines"], kinds, errors["last"] or ""))
    else:
        out.append("- 記録フックのエラー（骨組みを作った時点まで）：なし。")
    return out


def section(text, end_line):
    """節の行。節が無ければ None。end_line（検証値の前置きの行）か、次の `## ` の見出しで終わる。"""
    lines_ = text.splitlines()
    if HEADING not in lines_:
        return None
    body = []
    for line in lines_[lines_.index(HEADING) + 1:]:
        if line.startswith("## ") or line.strip() == end_line:
            break
        body.append(line)
    return body


def _tool_outputs(root, session):
    """振り返り自身のログのツールの出力。読めなければ、その理由（文字列）。"""
    if not session:
        return "骨組みに、振り返りのセッションの記録がありません"
    path = prompts.running_session_log(root, session)
    if not path:
        return "振り返りのセッションのログを読めません"
    try:
        rows = excerpt.load(path, types=None)
    except (OSError, UnicodeError):
        return "振り返りのセッションのログを読めません"
    if not any(payload.get("type") == "tool_call" and any(f in _command(payload) for f in reads.OWN_COMMANDS)
               for _, payload in rows):
        return "ログに、今回の骨組みの作成や検査の記録がまだありません"
    return [(position, payload.get("content") if isinstance(payload.get("content"), str)
             else excerpt.content_text(payload))
            for position, (_, payload) in enumerate(rows) if payload.get("type") == "tool_result"]


def _quote(value):
    start, end = value.find("「"), value.rfind("」")
    return value[start + 1:end] if 0 <= start < end else None


def validate(root, text, fixed, end_line):
    """(不一致, 知らせ) を返す。デバッグの節が無ければ、何も確かめない。

    fixed は、骨組みのスクリプトが書いた行（エージェントが書いた部分を見分けるのに使う）。
    """
    body = section(text, end_line)
    if body is None:
        return [], []
    written = [line for line in body if line.strip() and line.rstrip() not in fixed
               and not line.startswith(SCRIPT_LINES)]
    bugs, current = [], None
    for line in written:
        heading = BUG.match(line)
        if heading:
            current = {"number": heading.group(1), "fields": {}, "outputs": [], "files": 0}
            bugs.append(current)
        elif current is not None:
            field = FIELD.match(line)
            if field and field.group(1) not in current["fields"]:
                current["fields"][field.group(1)] = field.group(2).strip()
            cited = OUTPUT.search(line)
            if cited:
                current["outputs"].append(cited.group(1))
            if FILE_CITATION.search(line):
                current["files"] += 1
    if not bugs:
        if all(line.strip().startswith("なし") for line in written):
            return [], []
        return ["kiroku の不具合の候補は、`### B<番号>. ` の見出しで書いてください。無ければ「なし」と書きます。"], []
    errors, notices, outputs = [], [], None
    for number, count in sorted(collections.Counter(b["number"] for b in bugs).items()):
        if count > 1:
            errors.append("不具合の候補の番号 %s が %d 回使われています。B1 からの通し番号にしてください。" % (number, count))
    for bug in bugs:
        name, fields = bug["number"], bug["fields"]
        missing = [label for label in REQUIRED if not fields.get(label)]
        if missing:
            errors.append("%s：%s を書いてください。" % (name, "、".join(missing)))
        confidence = fields.get("確度") or ""
        if confidence and not confidence.startswith(CONFIDENCE):
            errors.append("%s：確度は「確認済み」か「推定」で書いてください。" % name)
        if confidence.startswith("確認済み") and not (bug["outputs"] or bug["files"]):
            errors.append("%s：確認済みとするなら、引用（出力）か引用（ファイル）を1つ以上付けてください。"
                          "確かめていなければ「推定」と書きます。" % name)
        for cited in bug["outputs"]:
            quote = _quote(cited)
            if quote is None or len(notes.fold(quote)) < notes.MIN_QUOTE_CHARS:
                errors.append("%s：引用（出力）は、%d 文字以上を「…」で囲んでください。" % (name, notes.MIN_QUOTE_CHARS))
                continue
            if outputs is None:
                outputs = _tool_outputs(root, reads.session_of(text))
            if isinstance(outputs, str):
                continue
            index = INDEX.search(cited[:cited.find("「")])
            hint = int(index.group(1)) if index else None
            ordered = sorted(outputs, key=lambda row: row[0] != hint)
            needle = notes.fold(quote)
            if not any(needle in notes.fold(content) or needle in notes.fold(mask(content)) for _, content in ordered):
                errors.append("%s：引用（出力）「%s」が、この振り返りのツールの出力にありません。出力を開き直して、"
                              "原文のまま写してください。" % (name, quote[:60]))
    if isinstance(outputs, str):
        notices.append("kiroku の不具合の候補の引用（出力）は照合できませんでした（%s）。" % outputs)
    return errors, notices


def main(argv=None):
    parser = argparse.ArgumentParser(description="kiroku のデバッグ欄の道具")
    parser.add_argument("--fingerprint", action="store_true", help="このスクリプトのフォルダの指紋を出す")
    args = parser.parse_args(argv)
    if args.fingerprint:
        print(fingerprint())
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
