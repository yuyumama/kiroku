"""レポートの「利用者に確かめたこと」を読み、答えの出どころを確かめる。

振り返りのエージェントは、案を作る前に1回だけ利用者に質問し（SKILL.md 手順5）、答えをこの節に書く。
  ### Q1. 作業の終わりに、テストを自動で実行する仕組みを使うか
  - 手がかり：`D5`
  - 見立て：a（使ってみたい）。…
  - 回答：a（見立てのとおり）
  - 引用（回答）：「はい」
  - 反映：P1 を出した
エージェントの申告をそのまま信じず、答えの引用が、振り返り自身のセッションの利用者の発話に
実在することを、ここで機械的に確かめる。エージェントが代わりに書いた答えは、ここで通らない。
答えの解釈が正しいか、見立てが正しいかは判定しない。答えを語で分類することもしない。
"""
import collections
import os
import re

import commands
import excerpt
import notes
import prompts
import reads
import skeleton
from ledger import HEADING as PROPOSAL_HEADING
from record import mask

HEADING = skeleton.QUESTION_HEADING
QUESTION = re.compile(r"^###\s+(Q\d+)[.．:：]?\s*(.*)$")
FIELD = re.compile(r"^\s*[-*]\s*(手がかり|見立て|回答|引用（回答）|反映)[：:]\s*(.*)$")
REQUIRED = ("手がかり", "見立て", "回答", "反映")
UNANSWERED = "未回答"
INDEX = re.compile(r"index\s*[=＝]?\s*(\d+)")
PROPOSAL = re.compile(r"(?<![#\w])P\d+")   # 反映に書いた、今回のレポートの案の番号（`レポート名#P1` は除く）


def section(text):
    """「利用者に確かめたこと」の節の行。節が無ければ None。"""
    lines = text.splitlines()
    if HEADING not in lines:
        return None
    body = []
    for line in lines[lines.index(HEADING) + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def parse(lines):
    """節の行から、質問の一覧を読む。[{number, title, fields}]。"""
    questions, current = [], None
    for line in lines:
        heading = QUESTION.match(line)
        if heading:
            current = {"number": heading.group(1), "title": heading.group(2).strip(), "fields": {}}
            questions.append(current)
        elif current is not None:
            field = FIELD.match(line)
            if field and field.group(1) not in current["fields"]:
                current["fields"][field.group(1)] = field.group(2).strip()
    return questions


def _quote(value):
    """「…」の中身。閉じ括弧が本文にあっても、最後の 」 までを引用とする。"""
    start, end = value.find("「"), value.rfind("」")
    return value[start + 1:end] if 0 <= start < end else None


def retro_log(root, session):
    """振り返り自身のログ。(行, 振り返りの集約を始めた位置) か、照合できない理由（文字列）。

    答えは、手順1の集約より後に利用者が書いた発話にある。ログがまだ今の実行（骨組みの作成か検査）
    まで届いていないときは、答えがまだ書かれていないことがあるので、照合しない。
    """
    if not session:
        return "骨組みに、振り返りのセッションの記録がありません"
    path = prompts.running_session_log(root, session)
    if not path:
        return "振り返りのセッションのログを読めません"
    try:
        rows = excerpt.load(path, types=None)
    except (OSError, UnicodeError):
        return "振り返りのセッションのログを読めません"
    start, fresh = None, False
    for position, (_, payload) in enumerate(rows):
        if payload.get("type") != "tool_call":
            continue
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        command = args.get("command")
        if not isinstance(command, str):
            continue
        if start is None and commands.own_script(command) == "aggregate.py":
            start = position
        fresh = fresh or any(flag in command for flag in reads.OWN_COMMANDS)
    if not fresh:
        return "ログに、今回の骨組みの作成や検査の記録がまだありません"
    return rows, -1 if start is None else start


def found(log, quote, index=None):
    """引用が、振り返りを始めた後の利用者の発話にあるか。

    4 文字未満の引用（「はい」「2」など）は、発話の全体と一致するときだけ認める（ノートの引用と同じ）。
    ただし、質問の番号つきの答え（「Q1 a」「Q2=c」）は、短くても、発話の一部として認める。
    1 通の発話でまとめて答えたとき、質問ごとに引用できるようにするため。
    /kiroku-retro の発話そのものは、答えに数えない。
    """
    rows, start = log
    needle = notes.fold(quote)
    whole = len(needle) < notes.MIN_QUOTE_CHARS
    numbered = re.fullmatch(r"[QqＱｑ]\d+[=＝:：]?[A-Za-zａ-ｚＡ-Ｚ]", needle) is not None
    order = ([index] if isinstance(index, int) and start < index < len(rows) else []) \
        + list(range(start + 1, len(rows)))
    for position in order:
        payload = rows[position][1]
        if payload.get("type") != "user":
            continue
        text = excerpt.content_text(payload)
        if not text.strip() or text.lstrip().startswith("/kiroku-retro"):
            continue
        candidates = (notes.fold(text), notes.fold(mask(text)))
        if numbered and any(re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(needle), c)
                            for c in candidates):
            return True
        if (needle in candidates) if whole else any(needle in c for c in candidates):
            return True
    return False


def validate(root, text, known, limit):
    """(不一致, 知らせ) を返す。照合できないときは、不一致にせず、知らせに理由を書く。

    known は、手がかりに書ける名前（集約に出た型、診断値、コマンド、パスなど）。
    limit は、質問の上限（設定の question_max）。None は上限なし。
    """
    body = section(text)
    if body is None:
        return ["「%s」の節がありません。骨組みのとおりに残してください。" % HEADING[3:]], []
    questions = parse(body)
    if not questions:
        content = [line.strip() for line in body if line.strip()]
        if all(line.startswith("なし") for line in content):
            return [], []
        return ["利用者に確かめたことは、質問ごとに `### Q<番号>. ` の見出しで書いてください。"
                "聞かなかったときは「なし」と書きます。"], []
    errors = []
    if limit is not None and len(questions) > limit:
        errors.append("利用者への質問が %d 件あります。上限は %d 件です（設定の question_max）。"
                      "答えで変わる案が大きく、見立てが不確かなものに絞ってください。" % (len(questions), limit))
    for number, count in sorted(collections.Counter(q["number"] for q in questions).items()):
        if count > 1:
            errors.append("質問の番号 %s が %d 回使われています。Q1 からの通し番号にしてください。" % (number, count))
    proposals = {m.group(1) for m in (PROPOSAL_HEADING.match(line) for line in text.splitlines()) if m}
    log, quoted = None, False
    for question in questions:
        name, fields = question["number"], question["fields"]
        missing = [label for label in REQUIRED if not fields.get(label)]
        if missing:
            errors.append("%s：%s を書いてください。" % (name, "、".join(missing)))
        clue = fields.get("手がかり")
        if clue and not any(c in known for c in re.findall(r"`([^`]+)`", clue)):
            errors.append("%s：手がかりに、集約に出た型、診断値、コマンド、パスなどを1つ以上、バッククォートで囲んで"
                          "書いてください。手がかりの無い一般的な質問はしません。" % name)
        for number in PROPOSAL.findall(fields.get("反映") or ""):
            if number not in proposals:
                errors.append("%s：反映に書いた %s が、このレポートの案にありません。" % (name, number))
        answer = fields.get("回答") or ""
        if not answer or answer.startswith(UNANSWERED):
            continue
        cited = fields.get("引用（回答）")
        if not cited:
            errors.append("%s：答えの出どころとして「引用（回答）：「…」」を書いてください。"
                          "答えが無いときは、回答を「%s」にします。" % (name, UNANSWERED))
            continue
        quote = _quote(cited)
        if quote is None or not notes.fold(quote):
            errors.append("%s：引用（回答）は「…」で囲んでください。" % name)
            continue
        quoted = True
        if log is None:
            log = retro_log(root, reads.session_of(text))
        if isinstance(log, str):
            continue
        index = INDEX.search(cited[:cited.find("「")])
        if not found(log, quote, int(index.group(1)) if index else None):
            errors.append("%s：引用（回答）「%s」が、この振り返りでの利用者の発話にありません。利用者が書いたとおりに"
                          "写してください。答えを補ってはいけません。%d 文字未満の答え（「はい」など）は、"
                          "発話の全体を写します。" % (name, quote[:60], notes.MIN_QUOTE_CHARS))
    notices = ["利用者に確かめたことの引用は照合できませんでした（%s）。" % log] if quoted and isinstance(log, str) else []
    return errors, notices


def previous(root, before=None):
    """書き終えたレポートの「利用者に確かめたこと」。新しいレポートが先。

    before を渡すと、その名前より前のレポートだけを読む（検査するレポート自身と、その後のものを除く）。
    """
    directory = os.path.join(root, ".kiro", "kiroku", "data", "reports")
    try:
        names = sorted((n for n in os.listdir(directory) if n.endswith(".md")), reverse=True)
    except OSError:
        return []
    rows = []
    for name in names:
        if before is not None and name[:-3] >= before:
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeError):
            continue
        if skeleton.is_draft(text):
            continue   # 書きかけの骨組み。欄の説明に出てくる見出しを、質問として読まない
        body = section(text)
        for question in parse(body) if body is not None else []:
            fields = question["fields"]
            rows.append({"id": "%s#%s" % (name[:-3], question["number"]), "report": name[:-3],
                         "number": question["number"], "title": question["title"],
                         "answer": fields.get("回答") or "記載なし", "reflected": fields.get("反映") or "記載なし"})
    return rows
