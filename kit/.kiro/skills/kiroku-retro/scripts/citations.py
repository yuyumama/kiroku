"""レポートの主張に付けた引用が、元の会話・ファイルに実在するかを確かめる。

振り返りのエージェントは、型と改善案の中で事実を述べるとき、出どころを示す引用を書く。
  - 引用（利用者）：session `<id>` index 12「完了報告は結論から短く」
  - 引用（応答）：session `<id>` index 13「テストは12件すべて成功しました」
  - 引用（ファイル）：`.kiro/steering/release.md`「4. 変更履歴を更新する」
エージェントの申告をそのまま信じず、引用の実在はここで機械的に確かめる。
確かめるのは実在だけで、引用の解釈が正しいかどうかは判定しない。
"""
import os
import re

import excerpt
import notes
import prompts
from ledger import HEADING, TARGET
from record import mask

KINDS = {"利用者": "user", "応答": "assistant", "ファイル": "file"}
CITATION = re.compile(r"引用（(利用者|応答|ファイル)）[：:]\s*(.*)$")
SESSION = re.compile(r"session\s*`([^`]+)`(?:\s*index\s*[=＝]?\s*(\d+))?")
PATH = re.compile(r"`([^`]+)`")
SECTION = re.compile(r"^###\s+(.*)$")
EMPTY_CAUSES = ("未確認", "該当なし", "なし")
NEW_FILE = "新規"


def _quote(rest):
    """行の「…」の中身。閉じ括弧が本文にあっても、最後の 」 までを引用とする。"""
    start, end = rest.find("「"), rest.rfind("」")
    return rest[start + 1:end] if 0 <= start < end else None


def _in_session(root, session, index, quote, role, cache):
    if session not in cache:
        path = excerpt.find_log(session, prompts.sessions_dir(root))
        try:
            cache[session] = excerpt.load(path) if path else None
        except (OSError, UnicodeError):
            cache[session] = None
    rows = cache[session]
    if rows is None:
        return "セッション `%s` のログを読めない" % session
    needle = notes.fold(quote)
    if len(needle) < notes.MIN_QUOTE_CHARS:
        return "引用が短すぎる（空白を除いて %d 文字以上）" % notes.MIN_QUOTE_CHARS
    order = ([index] if index is not None and 0 <= index < len(rows) else []) + list(range(len(rows)))
    for position in order:
        payload = rows[position][1]
        if payload.get("type") != role:
            continue
        text = excerpt.content_text(payload)
        if needle in notes.fold(text) or needle in notes.fold(mask(text)):
            return None
    return "セッション `%s` の%sの発話に、この引用が無い" % (session, "利用者" if role == "user" else "応答")


def resolve(root, name):
    """引用されたパスを実際のファイルにする。リポジトリと Kiro のユーザー設定の外は読まない。"""
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    name = name.strip()
    if name.startswith("~/.kiro/"):
        base, path = home, os.path.join(home, name[len("~/.kiro/"):])
    else:
        base, path = root, name if os.path.isabs(name) else os.path.join(root, name)
    real, base = os.path.realpath(path), os.path.realpath(base)
    if real != base and not real.startswith(base + os.sep):
        return None
    return real if os.path.isfile(real) else None


def _in_file(root, name, quote):
    path = resolve(root, name)
    if path is None:
        return "ファイル `%s` が無いか、リポジトリと ~/.kiro の外にある" % name
    needle = notes.fold(quote)
    if len(needle) < notes.MIN_QUOTE_CHARS:
        return "引用が短すぎる（空白を除いて %d 文字以上）" % notes.MIN_QUOTE_CHARS
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return "ファイル `%s` を読めない" % name
    return None if needle in notes.fold(text) else "ファイル `%s` に、この引用が無い" % name


def sections(text):
    """`### ` の見出しごとに、(見出し, 行の一覧) を返す。上位の見出しで区切る。"""
    found, current = [], None
    for line in text.splitlines():
        heading = SECTION.match(line)
        if heading:
            current = (line, [])
            found.append(current)
        elif line.startswith("#"):
            current = None
        elif current is not None:
            current[1].append(line)
    return found


def _value(lines, label):
    for line in lines:
        text = line.lstrip("-・ 　")
        if text.startswith(label):
            return text[len(label):].lstrip("：: ").strip()
    return None


def validate(root, text):
    """引用を照合し、裏付けの足りない主張を挙げる。問題が無ければ空の一覧。"""
    errors, cache = [], {}
    for heading, lines in sections(text):
        cited = {"user": 0, "assistant": 0, "file": 0}
        cited_files = set()
        for line in lines:
            found = CITATION.search(line)
            if not found:
                continue
            role, rest = KINDS[found.group(1)], found.group(2)
            quote = _quote(rest)
            if quote is None:
                errors.append("引用の形を読めません（「…」で囲んでください）：%s" % line.strip())
                continue
            if role == "file":
                name = PATH.search(rest[:rest.find("「")])
                problem = "パスを `…` で書いてください" if not name else _in_file(root, name.group(1), quote)
                if not problem:
                    cited_files.add(resolve(root, name.group(1)))
            else:
                where = SESSION.search(rest[:rest.find("「")])
                problem = "session を `…` で書いてください" if not where else _in_session(
                    root, where.group(1), int(where.group(2)) if where.group(2) else None, quote, role, cache)
            if problem:
                errors.append("照合できない引用（%s）：%s。「%s」" % (heading.strip("# ").strip(), problem, quote[:60]))
            else:
                cited[role] += 1
        name = heading.strip("# ").strip()
        target = next((TARGET.search(l).group(1).strip() for l in lines if TARGET.search(l)), None)
        # 会話からの型は、利用者の発話が根拠。その引用が要る。
        if target and target.startswith("topic:") and not HEADING.match(heading) and not cited["user"]:
            errors.append("「%s」：会話からの型には、利用者の発話の引用（引用（利用者）：…）が要ります。" % name)
        # 原因を述べるなら、利用者の指摘だけでなく、応答かファイルの裏付けが要る。
        cause = _value(lines, "原因の見立て")
        if cause and not cause.startswith(EMPTY_CAUSES) and not (cited["assistant"] or cited["file"]):
            errors.append("「%s」：原因の見立てに、応答またはファイルの引用がありません。"
                          "裏付けが無ければ「未確認」と書きます。" % name)
        # 既存のファイルについて述べるなら、そのファイルの引用が要る。
        for label in ("対応する既存の決まり", "対象のファイル"):
            value = _value(lines, label)
            if not value or value.startswith(("なし", NEW_FILE)) or NEW_FILE in value.split("`")[-1]:
                continue
            for path in PATH.findall(value):
                real = resolve(root, path)
                if real and real not in cited_files:
                    errors.append("「%s」：%s に `%s` を挙げていますが、そのファイルからの引用"
                                  "（引用（ファイル）：…）がありません。" % (name, label, path))
    return errors
