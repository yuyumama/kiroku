"""表で「確認済み」としたファイルを、振り返り自身のセッションログの読み取りと突き合わせる。

「読んだ」という申告をそのまま信じず、そのセッションで実際に本文を取得した記録があるかを確かめる。
ログの形は Kiro CLI 3.3.0 で実測したもの（docs/retro/verification.md の 2026-11-17）。
  - read_file      args.path（絶対パス）。成功は、対応する tool_result の success が真
  - load_resource  skill の本文の読み込み。_meta.kiro.disclosedContext.uri が file URI
  - steering_inclusion  自動で文脈に入った資料（AGENTS.md、常時読み込みの steering）。documents が file URI
一覧（list_directory）や検索（file_search、grep_search）は、本文を読んだ記録としては数えない。
確かめるのは本文を取得した記録があることだけで、内容を正しく理解したかどうかは判定しない。
"""
import os
import re
from urllib.parse import unquote, urlparse

import citations
import excerpt
import prompts
import skeleton

SESSION = re.compile(r"<!-- kiroku-session:([^\s>]+) -->")
OWN_COMMANDS = ("--new-report", "--check-report")


def session_line(session):
    return "<!-- kiroku-session:%s -->" % session


def session_of(text):
    found = SESSION.search(text)
    return found.group(1) if found else None


def _path(root, value):
    """ログに残った絶対パス、相対パス、file URI を、実際のパスにする。"""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("file:"):
        value = unquote(urlparse(value).path)
    if not os.path.isabs(value):
        value = os.path.join(root, value)
    return os.path.realpath(value)


def opened(root, session):
    """このセッションで本文を取得したファイル。ログを読めなければ None。

    戻り値は (開いたファイルの集合, 記録が今の実行まで届いているか)。
    """
    path = excerpt.find_log(session, prompts.sessions_dir(root))
    if not path:
        return None
    try:
        rows = excerpt.load(path, types=None)
    except (OSError, UnicodeError):
        return None
    succeeded = {p.get("toolCallId") for _, p in rows
                 if p.get("type") == "tool_result" and p.get("success") is True}
    files, fresh = set(), False
    for _, payload in rows:
        kind = payload.get("type")
        if kind == "steering_inclusion":
            documents = payload.get("documents")
            files.update(filter(None, (_path(root, d) for d in documents if isinstance(documents, list))))
            continue
        if kind != "tool_call":
            continue
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        name = payload.get("toolName")
        if name in ("execute_bash", "execute_pwsh"):
            command = args.get("command")
            # 骨組みを作った、または検査した呼び出しが残っていれば、記録は今のターンまで届いている。
            fresh = fresh or (isinstance(command, str) and any(flag in command for flag in OWN_COMMANDS))
            continue
        if payload.get("toolCallId") not in succeeded:
            continue
        if name == "read_file":
            files.add(_path(root, args.get("path")))
        elif name == "load_resource":
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            kiro = meta.get("kiro") if isinstance(meta.get("kiro"), dict) else {}
            disclosed = kiro.get("disclosedContext") if isinstance(kiro.get("disclosedContext"), dict) else {}
            files.add(_path(root, disclosed.get("uri")))
    files.discard(None)
    return files, fresh


def validate(root, text):
    """(不一致, 知らせ) を返す。照合できないときは、不一致にせず、知らせに理由を書く。"""
    claimed = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) >= 5 and "確認済み" in cells[3] and skeleton.UNREAD_STATE not in cells[3]:
            name = citations.PATH.search(cells[1])
            if name:
                claimed.append(name.group(1))
    if not claimed:
        return [], []
    session = session_of(text)
    if not session:
        return [], ["「確認済み」の照合はしていません（骨組みに、振り返りのセッションの記録がありません）。"]
    found = opened(root, session)
    if found is None:
        return [], ["「確認済み」の照合はできませんでした（振り返りのセッションのログを読めません）。"]
    files, fresh = found
    if not fresh:
        return [], ["「確認済み」の照合はできませんでした（ログに、今回の骨組みの作成や検査の記録がまだありません）。"]
    errors = []
    for name in claimed:
        real = citations.resolve(root, name)
        if real is None or real not in files:
            errors.append("表で `%s` を「確認済み」としていますが、この振り返りで本文を開いた記録がありません。"
                          "開いて確かめるか、「%s」にしてください。" % (name, skeleton.UNREAD_STATE))
    return errors, []
