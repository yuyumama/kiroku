"""ハーネスの診断値。ファイルだけから決まる（同じファイルなら、いつも同じ結果になる）。

一歩先の改善案の手がかりにする。事例が無くても、ハーネス自体の欠けや古びを示せるようにするため。
意味（決まりが正しいか、矛盾していないか）は判定しない。それは振り返りのエージェントが読む。

  D1  読み込みの条件（fileMatchPattern）に当たるファイルが無い steering
  D2  決まりに書かれた参照の参照先が無い。2つに分ける。
        断定：Kiro やツールが機械的に読む書き方（Kiro のファイル参照 #[[file:…]]、Markdown のリンク、
              hook のコマンド、エージェント定義）。
        候補：文章の中でコードとして書かれた語（パス、make の target、npm script）。参照なのか、例・
              使わない手順・これから作る場所なのかは文脈で決まるので、行の文面を添えて出し、
              振り返りのエージェントが読んで決める。
  D3  同じ文が、複数のファイルにある
  D5  事後の検査（テスト、hook、pre-commit、CI、lint）の一覧。記録があれば、書き込みの後のテスト実行の数も
  D6  型ごとの負担（回数、セッション数、割合）

判定できない参照は数えない。「確かめられない」ものを「壊れている」とは書かない。
意味（参照か例か、決まりが正しいか）はコードで決めない。コードは、構文と実在だけで決まることを確かめ、
候補を絞って根拠（行の文面）を添える。
モノレポでは、参照先がルート以外にあることが多い。ルート、書いたファイルのディレクトリ、リポジトリ内の
同じ名前（末尾が一致する）パスのどこにも無いものだけを「見つからない」とする。npm script と make の target も、
リポジトリ内のすべての package.json と Makefile を見る。
"""
import itertools
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import harness
from record import mask

# kiroku 自身のファイル。診断の対象にしない（skeleton.OWN_FILES と同じ）
OWN_FILES = (".kiro/skills/kiroku-retro/", ".kiro/hooks/kiroku.json", ".kiro/agents/kiroku-retro.json",
             ".kiro/kiroku/")
OWN_HOOK_PREFIX = "kiroku-record-"
DUPLICATE_MIN_CHARS = 12   # これより短い行は、同じでも重複に数えない。最初の値で、根拠は薄い
PATH_EXTENSIONS = {"py", "md", "json", "js", "mjs", "cjs", "ts", "tsx", "jsx", "sh", "ps1", "yml", "yaml",
                   "toml", "txt", "cfg", "ini", "xml", "mdc", "rb", "go", "rs", "java", "kt", "sql", "html",
                   "css", "lock"}
NOT_PATH_CHARS = set("<>{}$*?[]=|&;\"'`")
INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
TRIGGER_LABELS = {"pretooluse": "実行前", "posttooluse": "実行後", "postfilesave": "保存時", "filesave": "保存時",
                  "postfilecreate": "作成時", "stop": "終了時", "agentstop": "終了時",
                  "userpromptsubmit": "発話時", "promptsubmit": "発話時", "sessionstart": "セッション開始時"}
PRECOMMIT_FILES = (".pre-commit-config.yaml", ".husky/", "lefthook.yml", "lefthook.yaml", ".lefthook.yml")
CI_PATTERNS = (re.compile(r"^\.github/workflows/[^/]+\.ya?ml$"), re.compile(r"^\.gitlab-ci\.yml$"),
               re.compile(r"^\.circleci/config\.ya?ml$"), re.compile(r"^azure-pipelines\.ya?ml$"),
               re.compile(r"^Jenkinsfile$"), re.compile(r"^\.buildkite/.+\.ya?ml$"))
LINT_FILES = ("ruff.toml", ".ruff.toml", ".flake8", ".pylintrc", "mypy.ini", ".mypy.ini", "tsconfig.json",
              ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml",
              "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts", "biome.json",
              ".golangci.yml", ".golangci.yaml", ".rubocop.yml")
LINT_SECTIONS = {"pyproject.toml": ("[tool.ruff", "[tool.mypy", "[tool.pylint", "[tool.flake8"),
                 "setup.cfg": ("[flake8]", "[mypy]")}


def _own(relative):
    return relative.startswith(OWN_FILES)


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _home():
    return Path(os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro"))


def harness_texts(root, files):
    """診断する決まりのファイル。[(表示名, 絶対パス, 種類, 範囲)]。kiroku 自身のものは除く。"""
    found = []
    for name in files:
        if _own(name):
            continue
        if name.startswith(".kiro/steering/") and name.endswith(".md"):
            found.append((name, Path(root) / name, "steering", "project"))
        elif name.split("/")[-1] == "AGENTS.md":
            found.append((name, Path(root) / name, "agents_md", "project"))
        elif name.startswith(".kiro/skills/") and name.endswith("/SKILL.md"):
            found.append((name, Path(root) / name, "skill", "project"))
        elif re.fullmatch(r"\.kiro/hooks/[^/]+\.json", name):
            found.append((name, Path(root) / name, "hooks", "project"))
        elif re.fullmatch(r"\.kiro/agents/[^/]+\.json", name):
            found.append((name, Path(root) / name, "agent", "project"))
    steering = _home() / "steering"
    for directory, dirs, names in os.walk(steering, followlinks=False):
        dirs.sort()
        for name in sorted(names):
            if name.lower().endswith(".md"):
                path = Path(directory) / name
                found.append(("~/.kiro/" + path.relative_to(_home()).as_posix(), path, "steering", "user"))
    return found


def front_matter(text):
    """先頭の front matter を、簡単な key: value の辞書にする（値はそのままの文字列か、文字列の一覧）。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    values, key = {}, None
    for line in text[3:end].splitlines():
        item = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if item and key:
            values.setdefault(key, [])
            if isinstance(values[key], list):
                values[key].append(item.group(1).strip("\"'"))
            continue
        pair = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
        if not pair:
            continue
        key, value = pair.group(1), pair.group(2)
        if value.startswith("[") and value.endswith("]"):
            values[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        elif value:
            values[key] = value.strip("\"'")
        else:
            values[key] = []
    return values


def _braces(pattern):
    found = re.search(r"\{([^{}]*)\}", pattern)
    if not found:
        return [pattern]
    return [expanded for option in found.group(1).split(",")
            for expanded in _braces(pattern[:found.start()] + option + pattern[found.end():])]


def glob_regex(pattern):
    """`**` を階層またぎとして扱う glob を、正規表現にする。"""
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        elif pattern[index] == "[" and "]" in pattern[index + 1:]:
            end = pattern.index("]", index + 1)
            out.append("[" + pattern[index + 1:end].replace("\\", "\\\\") + "]")
            index = end + 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def matches(pattern, files):
    """パターンに当たるファイルがあるか。Kiro の照合の細部は未確認なので、広めに当てる。

    `/` を含まないパターンは、ファイル名だけでも照合する（どの階層でも当たるとみなす）。
    """
    for option in _braces(pattern.strip()):
        option = option[2:] if option.startswith("./") else option
        regex = glob_regex(option)
        if any(regex.match(name) or ("/" not in option and regex.match(name.split("/")[-1])) for name in files):
            return True
    return False


def unloaded_steering(texts, files):
    """D1：読み込みの条件が fileMatch なのに、条件に当たるファイルが無い steering。"""
    found = []
    for shown, path, kind, scope in texts:
        if kind != "steering" or scope != "project":
            continue
        meta = front_matter(_read(path) or "")
        if str(meta.get("inclusion", "")).lower() != "filematch":
            continue
        patterns = meta.get("fileMatchPattern") or []
        patterns = [patterns] if isinstance(patterns, str) else list(patterns)
        if patterns and not any(matches(p, files) for p in patterns):
            found.append({"key": "D1:%s" % shown, "path": shown, "patterns": patterns})
    return found


def _ignored(root, relative):
    try:
        return subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", relative],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _looks_like_path(token):
    """パスらしさ。False（パスではない）、"path"（確かめる）、"maybe"（最初の区切りがディレクトリとして
    実在するときだけ確かめる）。

    拡張子の無い a/b は、`owner/repo`、`Given/When/Then`、「登録/削除」のような、/ で区切った語のことが多い。
    最初の区切りがリポジトリに無ければ、パスとはみなさない。
    """
    if not token or any(c in NOT_PATH_CHARS for c in token) or "://" in token:
        return False
    if token.startswith(("-", "~", "#", "@", "/", "\\")):
        return False
    found = re.fullmatch(r"[\w.-]+\.([A-Za-z0-9]+)", token.rsplit("/", 1)[-1])
    if found and found.group(1).lower() in PATH_EXTENSIONS:
        return "path"
    if "/" not in token:
        return False
    if token.endswith("/"):
        return "path" if token.strip("/") else False
    return "maybe"


def _repo_index(files):
    """リポジトリのパス（ファイルとディレクトリ）を、最後の名前で引く表と、ディレクトリの名前の集合。"""
    index, directories = {}, set()
    for name in files:
        parts = name.split("/")
        directories.update(parts[:-1])
        for depth in range(1, len(parts) + 1):
            index.setdefault(parts[depth - 1], set()).add("/".join(parts[:depth]))
    return index, directories


def _elsewhere(token, index):
    """リポジトリのどこかに、末尾が token と一致するパスがあるか。"""
    token = token.strip("/")
    while token.startswith("./"):
        token = token[2:]
    if not token or ".." in token.split("/"):
        return False
    return any(p == token or p.endswith("/" + token) for p in index.get(token.split("/")[-1], ()))


def _make_targets(text):
    targets = set()
    for line in text.splitlines():
        found = re.match(r"^([A-Za-z0-9_.%/ -]+?)\s*::?(?!=)", line)
        if found and not line.startswith("\t"):
            targets.update(found.group(1).split())
    return targets


def _package_scripts(text):
    try:
        scripts = json.loads(text).get("scripts")
    except (ValueError, AttributeError):
        return None
    return set(scripts) if isinstance(scripts, dict) else set()


MAKEFILES = ("GNUmakefile", "makefile", "Makefile")


def _definitions(root, files, names, parse):
    """リポジトリ内の package.json や Makefile ごとの定義。([(相対パス, 名前の集合)], 読めなかった数)。

    同じディレクトリに複数の Makefile があるときは、make が読むもの（GNUmakefile、makefile、Makefile の順）だけ。
    """
    chosen = {}
    for name in set(files) | {n for n in names if (Path(root) / n).is_file()}:
        directory, _, base = name.rpartition("/")
        if base in names and (directory not in chosen or names.index(base) < names.index(chosen[directory])):
            chosen[directory] = base
    found, unreadable = [], 0
    for directory, base in sorted(chosen.items()):
        relative = directory + "/" + base if directory else base
        text = _read(Path(root) / relative)
        value = parse(text) if text is not None else None
        if value is None:
            unreadable += 1
        else:
            found.append((relative, value))
    return found, unreadable


def _missing_definition(defined, unreadable, label, what, name, written):
    """npm script や make の target が、どの定義にも無いか。判定できなければ None。"""
    if any(name in names for _, names in defined):
        return None
    if unreadable:
        return None   # 読めない定義に書かれているかもしれない
    if not defined:
        return (written, "%s が無い" % label)
    if len(defined) == 1:
        return (written, "%s に %s `%s` が無い" % (defined[0][0], what, name))
    return (written, "どの %s（%d 個）にも %s `%s` が無い" % (label, len(defined), what, name))


def _context(root, cache):
    if "files" not in cache:
        cache["files"] = harness.repo_files(root)
    if "index" not in cache:
        cache["index"] = _repo_index(cache["files"])
    return cache["files"], cache["index"]


SEPARATORS = ("|", "||", "&&", ";", "&")
REDIRECTS = (">", ">>", "1>", "2>", "&>", "1>>", "2>>", "&>>")
CREATING = ("mkdir", "touch", "tee")   # 引数のパスを作るコマンド。引数は、今あるものへの参照ではない


def _outputs(tokens):
    """コマンドが作る・書き出すパスの位置（リダイレクトの先、mkdir・touch・tee の引数）。構文だけで決まる。"""
    found, start, creating, redirected = set(), True, False, False
    for position, token in enumerate(tokens):
        if token in SEPARATORS:
            start, creating, redirected = True, False, False
            continue
        if redirected:
            found.add(position)
            redirected = False
        elif token in REDIRECTS:
            redirected = True
        elif start and token in CREATING:
            creating = True
        elif creating and not token.startswith("-"):
            found.add(position)
        start = False
    return found


def broken_references(root, command, bases, cache, missing="見つからない", strict=False):
    """1つのコマンドや参照の中の、参照先が無いもの。[(参照, 理由)]。判定できないものは返さない。

    cache には、リポジトリのファイル一覧（"files"）を入れておける。無ければ、ここで求める。
    missing は、パスが見つからないときの理由の書き出し。参照と決まっているもの（hook のコマンドなど）は
    「参照先が無い」、文章の中のコードとして書かれた語は「見つからない」（参照かどうかは未確認）にする。
    strict は、実行・読み込みされるもの（hook のコマンド、エージェント定義）。ルートからだけ解決し、
    別の場所の同じ名前のファイルでは「有る」としない（実行すると見つからずに失敗するため）。
    """
    tokens = command.split()
    if not tokens:
        return []
    files, (index, directories) = _context(root, cache)
    if tokens[0] in ("make", "gmake"):
        target = next((t for t in tokens[1:] if not t.startswith("-") and "=" not in t), None)
        if "make" not in cache:
            cache["make"] = _definitions(root, files, MAKEFILES, _make_targets)
        defined, unreadable = cache["make"]
        if not defined and not unreadable:
            return [(" ".join(tokens[:2] if target else tokens[:1]), "Makefile が無い")]
        if target:
            missing = _missing_definition(defined, unreadable, "Makefile", "target", target, "make %s" % target)
            return [missing] if missing else []
        return []
    script = None
    if tokens[0] in ("npm", "pnpm", "yarn") and len(tokens) >= 3 and tokens[1] == "run":
        script, written = tokens[2], " ".join(tokens[:3])
    elif tokens[0] == "npm" and len(tokens) >= 2 and tokens[1] == "test":
        script, written = "test", "npm test"
    if script:
        if "npm" not in cache:
            cache["npm"] = _definitions(root, files, ("package.json",), _package_scripts)
        missing = _missing_definition(*cache["npm"], "package.json", "script", script, written)
        return [missing] if missing else []
    problems = []
    roots = [Path(root)] + ([] if strict else [Path(b) for b in bases if Path(b) != Path(root)])
    outputs = _outputs(tokens)
    for position, token in enumerate(tokens):
        if position in outputs:
            continue   # これから作る・書き出すパス（`> out.json`、`mkdir -p dist/`）
        token = token.strip("()[],;:。、")
        kind = _looks_like_path(token)
        if not kind:
            continue
        if any((base / token).exists() for base in roots):
            continue
        first = token.split("/")[0]
        if kind == "maybe" and first not in directories and not any((b / first).is_dir() for b in roots):
            continue   # / で区切った語（owner/repo など）。パスとして読む根拠が無いので、候補にも出さない
        if not strict and _elsewhere(token, index):
            continue   # 別の場所にある同じ名前のもの。どれを指すかは分からないが、無いとは言えない
        if not strict and token.startswith(".") and "/" not in token and token.count(".") >= 2 \
                and any(name.endswith(token) for name in index):
            continue   # `.test.ts` のような、名前の末尾の説明。末尾が一致するファイルがある
        if _ignored(root, token):
            continue   # 実行時にできるファイル（記録など）。git が無視するものは、無くても誤りではない
        problems.append((token, "%s（基準：ルート）" % missing if strict
                         else "%s（探した場所：%s）" % (missing, _places(root, roots, token))))
    return problems


def _places(root, roots, token):
    places = ["ルート"] + ["`%s/`" % os.path.relpath(b, root).replace(os.sep, "/") for b in roots[1:]]
    return "、".join(places + ["リポジトリ内の同じ名前のファイル" if "/" not in token.strip("/")
                               else "リポジトリ内で末尾が一致するパス"])


FILE_REFERENCE = re.compile(r"#\[\[(file|folder):([^\]\n]+?)\]\]")
MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
LINE_TEXT_CHARS = 100   # 候補に添える行の文面の長さ（文字数）。最初の値で、根拠は薄い


def _scan(text):
    """文書を行ごとに読み、(コードとして書かれた部分, 書き方から参照と決まるもの) を返す。

    コード：[(行番号, 中身, 行の文面)]。インラインのコードと、コードブロックの各行。
    参照：[(行番号, 種類, 参照先, 行の文面)]。種類は file・folder（Kiro のファイル参照 #[[file:…]]）か
    link（Markdown のリンク）。コードの中に書かれたものは、記法の説明や例なので、参照として数えない。
    """
    spans, refs, fenced = [], [], False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            if line.strip():
                spans.append((number, line.strip(), line.strip()))
            continue
        spans += [(number, m.group(1).strip(), line.strip()) for m in INLINE_CODE.finditer(line)
                  if m.group(1).strip()]
        prose = INLINE_CODE.sub(" ", line)
        refs += [(number, m.group(1), m.group(2).strip(), line.strip()) for m in FILE_REFERENCE.finditer(prose)]
        refs += [(number, "link", m.group(1), line.strip()) for m in MARKDOWN_LINK.finditer(prose)]
    return spans, refs


def _link_target(target):
    """Markdown のリンク先のうち、リポジトリの中のパス。URL、見出し、絶対パス、型紙は None。"""
    if not target or target.startswith(("#", "/", "~", "\\")) or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    path = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not path or any(c in NOT_PATH_CHARS for c in path):
        return None
    return path


def _reference_bases(root, path, kind, scope):
    """Kiro のファイル参照の基準（kiro.dev/docs/steering の File references）。(基準の一覧, 表示)。

    ワークスペースの steering はルート、ユーザー共通の steering は ~/.kiro/steering/、AGENTS.md は
    そのファイルのフォルダ。skill などは公式資料に記述が無いので、ルートと、そのファイルのフォルダの
    どちらかにあれば有るとする。
    """
    if kind == "steering":
        return ([_home() / "steering"], "`~/.kiro/steering/`") if scope == "user" else ([Path(root)], "ルート")
    folder = os.path.relpath(path.parent, root).replace(os.sep, "/")
    if kind == "agents_md":
        return [path.parent], "ルート" if folder == "." else "`%s/`" % folder
    return [Path(root), path.parent], "ルート、`%s/`" % folder


def _explicit(root, path, kind, scope, how, target):
    """書き方から参照と決まるものが、参照先を持つか。無ければ (参照, 理由)、有れば None。"""
    if how == "link":
        relative = _link_target(target)
        if relative is None:
            return None
        bases = [path.parent, Path(root)]
        where = "このファイルのフォルダ、ルート"
        label = "Markdown のリンク先が無い"
    else:
        relative = re.sub(r":\d+(?:-\d+)?$", "", target) if how == "file" else target
        bases, where = _reference_bases(root, path, kind, scope)
        label = "Kiro のファイル参照 `#[[%s:…]]` の参照先が無い" % how
    if not relative or any((base / relative).exists() for base in bases):
        return None
    if scope == "project":
        inside = os.path.relpath(bases[0] / relative, root).replace(os.sep, "/")
        if not inside.startswith("..") and _ignored(root, inside):
            return None   # 実行時にできるファイル。git が無視するものは、無くても誤りではない
    return relative, "%s（基準：%s）" % (label, where)


def _around(line, ref):
    """候補に添える行の文面。長い行は、参照の前後だけを残す。"""
    if len(line) <= LINE_TEXT_CHARS:
        return line
    at = max(line.find(ref.split()[0] if ref.split() else ref), 0)
    start = max(0, min(at - LINE_TEXT_CHARS // 3, len(line) - LINE_TEXT_CHARS))
    return ("…" if start else "") + line[start:start + LINE_TEXT_CHARS] + \
        ("…" if start + LINE_TEXT_CHARS < len(line) else "")


def _json_commands(path, kind):
    """hook とエージェントの定義が実行・参照するもの。"""
    try:
        data = json.loads(_read(path) or "")
    except ValueError:
        return []
    found = []
    if kind == "hooks":
        for hook in data.get("hooks", []) if isinstance(data, dict) else []:
            action = hook.get("action") if isinstance(hook, dict) else None
            if isinstance(action, dict) and isinstance(action.get("command"), str):
                found.append(action["command"])
    elif kind == "agent" and isinstance(data, dict):
        for resource in data.get("resources") or []:
            if isinstance(resource, str) and "://" in resource:
                found.append(resource.split("://", 1)[1])
        rules = (data.get("permissions") or {}).get("rules") if isinstance(data.get("permissions"), dict) else None
        for rule in rules or []:
            if isinstance(rule, dict) and rule.get("capability") == "shell" and rule.get("effect") == "allow":
                found += [m for m in rule.get("match") or [] if isinstance(m, str)]
    return found


def broken_refs(root, texts, files=None):
    """D2：決まりに書かれた参照のうち、参照先が見つからないもの。

    confirmed が真（断定）：Kiro やツールが機械的に読む書き方。Kiro のファイル参照（#[[file:…]]、
    #[[folder:…]]）、Markdown のリンク、hook のコマンド、エージェント定義の resources と許可するコマンド。
    confirmed が偽（候補）：文章の中で、コードとして書かれた語。パスや npm script として探して見つからなくても、
    参照なのか、例・使わない手順・これから作る場所なのかは、文脈を読まないと決まらない。
    行の文面を添えて出し、振り返りのエージェントが読んで決める。
    ユーザー共通の steering は、Kiro のファイル参照だけを見る（基準が ~/.kiro/steering/ と決まっているため）。
    """
    found, cache, seen = [], {} if files is None else {"files": files}, set()

    def add(shown, ref, reason, confirmed, number=None, line=None):
        key = "D2:%s:%s" % (shown, ref)
        if key in seen:
            return
        seen.add(key)
        row = {"key": key, "path": shown, "ref": mask(ref), "reason": reason, "confirmed": confirmed}
        if number is not None:
            row.update(line=number, text=mask(_around(line, ref)))
        found.append(row)

    for shown, path, kind, scope in texts:
        if kind in ("hooks", "agent"):
            if scope == "project":
                for command in _json_commands(path, kind):
                    for ref, reason in broken_references(root, command, [], cache, "参照先が無い", strict=True):
                        add(shown, ref, reason, True)
            continue
        spans, refs = _scan(_read(path) or "")
        for number, how, target, line in refs:
            if scope == "project" or how != "link":
                problem = _explicit(root, path, kind, scope, how, target)
                if problem:
                    add(shown, problem[0], problem[1], True, number, line)
        if scope != "project":
            continue   # ユーザー共通の文書の中のコードは、どのリポジトリを指すか決まらない
        for number, span, line in spans:
            for ref, reason in broken_references(root, span, [path.parent], cache):
                add(shown, ref, reason, False, number, line)
    return found


def _normalize(line):
    line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)
    return " ".join(line.split())


def duplicates(texts):
    """D3：同じ文が、複数のファイルにある。見出し、表、コードブロック、front matter、短い行は見ない。"""
    owners = {}
    for shown, path, kind, scope in texts:
        if kind in ("hooks", "agent"):
            continue
        text = _read(path) or ""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            text = text[end + 4:] if end != -1 else text
        fenced = False
        for line in text.splitlines():
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            stripped = line.strip()
            if fenced or not stripped or stripped.startswith(("#", "|", "---", ">")):
                continue
            normal = _normalize(stripped)
            if len(normal) >= DUPLICATE_MIN_CHARS:
                owners.setdefault(normal, set()).add(shown)
    pairs = {}
    for line, files in owners.items():
        for first, second in itertools.combinations(sorted(files), 2):
            pairs.setdefault((first, second), []).append(line)
    return [{"key": "D3:%s|%s" % pair, "files": list(pair), "lines": len(lines),
             "sample": mask(sorted(lines)[0])} for pair, lines in sorted(pairs.items())]


def _uncommented(line, marker):
    """行から、引用符の外のコメントを除く。marker は "#"（YAML）か "//"（Jenkinsfile）。

    YAML の # は、行頭か空白の後に来たときだけコメントになる（`http://x/#y` の # は残す）。
    """
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif line.startswith(marker, index) and (marker != "#" or index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def ci_lines(name, text):
    """CI の定義のうち、コメントを除いた行。Issue #24 のレビューで、コメントにテスト名があるだけで
    「CI がテストを動かす」と判定していたため。文面の一致が実行か言及かは、これまでどおり区別しない。"""
    marker = "//" if name.rsplit("/", 1)[-1] == "Jenkinsfile" else "#"
    return [kept for kept in (_uncommented(line, marker) for line in text.splitlines()) if kept.strip()]


def sensors(root, files, config, hooks_by_name):
    """D5：事後の検査の一覧。有無だけを見る。動いているか（発火するか）は見ない。"""
    hooks = {}
    for name, trigger in sorted(hooks_by_name.items()):
        if name.startswith(OWN_HOOK_PREFIX):
            continue
        hooks.setdefault(TRIGGER_LABELS.get(trigger.lower(), trigger), []).append(name)
    precommit = [n for n in files if any(n == p or (p.endswith("/") and n.startswith(p)) for p in PRECOMMIT_FILES)]
    git_hook = Path(root) / ".git" / "hooks" / "pre-commit"
    if git_hook.is_file() and os.access(git_hook, os.X_OK):
        precommit.append(".git/hooks/pre-commit")
    precommit = sorted({p.split("/")[0] + "/" if p.startswith(".husky/") else p for p in precommit})
    ci = [n for n in files if any(p.match(n) for p in CI_PATTERNS)]
    tests = [t for t in config.get("tests") or [] if isinstance(t, dict) and isinstance(t.get("match"), str)]
    ci_runs_tests = None
    if tests and ci:
        ci_runs_tests = False
        for name in ci:
            for line in ci_lines(name, _read(Path(root) / name) or ""):
                for entry in tests:
                    try:
                        if re.search(entry["match"], line):
                            ci_runs_tests = True
                    except re.error:
                        continue
    # モノレポでは、設定がサブディレクトリごとにある。どの階層でも、ファイル名で見る。
    lint = [n for n in files if n.rsplit("/", 1)[-1] in LINT_FILES]
    for name in files:
        sections = LINT_SECTIONS.get(name.rsplit("/", 1)[-1])
        if sections and any(s in (_read(Path(root) / name) or "") for s in sections):
            lint.append(name)
    return {"test_configs": len(tests), "hooks": hooks, "precommit": precommit, "ci": ci,
            "ci_runs_tests": ci_runs_tests, "lint": sorted(set(lint))}


def diagnose(root, config, hooks_by_name):
    """D1〜D3 と D5。ファイルだけから決まる。"""
    files = harness.repo_files(root)
    texts = harness_texts(root, files)
    return {"unloaded": unloaded_steering(texts, files), "broken": broken_refs(root, texts, files),
            "duplicates": duplicates(texts), "sensors": sensors(root, files, config, hooks_by_name)}


def burden(data):
    """D6：型ごとの負担。回数、セッション数と、分母に対する割合。検証値の対象の型だけを出す。

    分母は、記録からの型（tool、denied、cancelled、timeout、test）なら期間内の記録があるセッション、
    会話からの型（topic）なら期間内に発話があるセッション。
    """
    recorded = data["coverage"]["recorded"]
    talked = sum(1 for s in data["sessions"] if s["prompts"])
    rows = []
    for target, value in data["report_facts"]["targets"].items():
        base = talked if target.startswith("topic:") else recorded
        rows.append({"target": target, "count": value["count"], "sessions": value["sessions"], "base": base,
                     "share": round(100 * value["sessions"] / base) if base else None,
                     "base_label": "発話があるセッション" if target.startswith("topic:") else "記録のあるセッション"})
    return sorted(rows, key=lambda r: (-r["sessions"], -r["count"], r["target"]))


def keys(found):
    """レポートの手がかりに書ける、診断値の名前。"""
    return sorted([row["key"] for row in found["unloaded"]] + [row["key"] for row in found["broken"]]
                  + [row["key"] for row in found["duplicates"]] + ["D5", "D6"])


REPORT_CAP = 12   # レポートの骨組みに載せる、診断値の一覧1つあたりの件数。最初の値で、根拠は薄い


def _capped(rows, cap):
    if cap is None or len(rows) <= cap:
        return rows, []
    return rows[:cap], ["  - ほか %d 件。全件は `aggregate.py --section harness` で出します。" % (len(rows) - cap)]


def lines(found, rows, cap=None):
    """診断値を、人が読む行にする。骨組みと集約の表示で同じ文面を使う。cap は一覧1つあたりの件数。"""
    out = []
    for row in found["unloaded"]:
        out.append("- `%s`：読み込みの条件 %s に当たるファイルが、リポジトリに無い。この決まりは読み込まれていない"
                   "（照合は kiroku による。Kiro の照合の細部は未確認）。" % (
                       row["key"], "、".join("`%s`" % p for p in row["patterns"])))
    confirmed = [r for r in found["broken"] if r.get("confirmed", True)]
    candidates = [r for r in found["broken"] if not r.get("confirmed", True)]
    shown, more = _capped(confirmed, cap)
    for row in shown:
        out.append("- `%s`：%s。%s" % (row["key"], row["reason"],
                                      "%d 行目。" % row["line"] if row.get("line") else ""))
    out += [line[2:] for line in more]
    if candidates:
        out.append("- D2 の候補 %d 件：文章の中でコードとして書かれた語のうち、リポジトリに見つからないもの。"
                   "参照なのか、例・使わない手順・これから作る場所なのかは未確認。"
                   "手がかりに使う前に、その行を読んで確かめる。" % len(candidates))
        shown, more = _capped(candidates, cap)
        for row in shown:
            out.append("  - `%s`：%s。%d 行目「%s」" % (row["key"], row["reason"], row["line"], row["text"]))
        out += more
    shown, more = _capped(found["duplicates"], cap)
    for row in shown:
        out.append("- `%s`：同じ文が %d 行ある（例：「%s」）。" % (
            row["key"], row["lines"], row["sample"][:60] + ("…" if len(row["sample"]) > 60 else "")))
    out += [line[2:] for line in more]
    if not (found["unloaded"] or found["broken"] or found["duplicates"]):
        out.append("- 読み込まれない決まり、参照先の無い記述、複数のファイルにある同じ文は、見つからなかった。")
    sense = found["sensors"]
    hooks = "、".join("%s %s" % (label, "・".join("`%s`" % n for n in names))
                     for label, names in sorted(sense["hooks"].items())) or "無し"
    ci = "、".join("`%s`" % n for n in sense["ci"]) or "無し"
    if sense["ci_runs_tests"] is not None:
        ci += "（テストの記録設定の match に一致する行が%s）" % ("ある" if sense["ci_runs_tests"] else "無い")
    lint, more = _capped(sense["lint"], cap)
    after = sense.get("after_writes")
    out += ["- `D5`：事後の検査。テストの記録設定 %d 件。hook（kiroku 自身のものを除く）：%s。pre-commit：%s。"
            "CI：%s。lint・型検査の設定：%s。%s" % (
                sense["test_configs"], hooks, "、".join("`%s`" % n for n in sense["precommit"]) or "無し", ci,
                "、".join("`%s`" % n for n in lint) + ("、ほか %d 個" % (len(sense["lint"]) - len(lint)) if more else "")
                or "無し",
                "" if after is None else
                "記録から：書き込みのあったターン %d 件のうち、最後の書き込みの後に同じターンでテスト実行の記録が"
                "あるもの %d 件（テストで確かめる必要の無い書き込みも含む）。" % (after["turns"], after["tested"]))]
    if rows:
        out += ["", "型ごとの負担（`D6`。回数、セッション数、分母に対する割合）："]
        for row in rows:
            share = "%d%%" % row["share"] if row["share"] is not None else "分母なし"
            out.append("- `%s`：%d 回、%d セッション（%s %d 件のうち %s）" % (
                row["target"], row["count"], row["sessions"], row["base_label"], row["base"], share))
    return out
