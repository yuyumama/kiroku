"""ハーネスの診断値。ファイルだけから決まる（同じファイルなら、いつも同じ結果になる）。

一歩先の改善案の手がかりにする。事例が無くても、ハーネス自体の欠けや古びを示せるようにするため。
意味（決まりが正しいか、矛盾していないか）は判定しない。それは振り返りのエージェントが読む。

  D1  読み込みの条件（fileMatchPattern）に当たるファイルが無い steering
  D2  決まりに書かれたパス、make の target、npm script、hook やエージェントが呼ぶスクリプトの参照先が無い
  D3  同じ文が、複数のファイルにある
  D5  事後の検査（テスト、hook、pre-commit、CI、lint）の一覧
  D6  型ごとの負担（回数、セッション数、割合）

判定できない参照は数えない。「確かめられない」ものを「壊れている」とは書かない。
"""
import itertools
import json
import os
import re
import subprocess
from pathlib import Path

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
    if not token or any(c in NOT_PATH_CHARS for c in token) or "://" in token:
        return False
    if token.startswith(("-", "~", "#", "@", "/", "\\")):
        return False
    if "/" in token:
        return bool(token.strip("/"))
    found = re.fullmatch(r"[\w.-]+\.([A-Za-z0-9]+)", token)
    return bool(found) and found.group(1).lower() in PATH_EXTENSIONS


def _make_targets(root):
    for name in ("Makefile", "makefile", "GNUmakefile"):
        text = _read(Path(root) / name)
        if text is not None:
            targets = set()
            for line in text.splitlines():
                found = re.match(r"^([A-Za-z0-9_.%/ -]+?)\s*::?(?!=)", line)
                if found and not line.startswith("\t"):
                    targets.update(found.group(1).split())
            return targets
    return None


def _npm_scripts(root):
    text = _read(Path(root) / "package.json")
    if text is None:
        return None
    try:
        scripts = json.loads(text).get("scripts")
    except (ValueError, AttributeError):
        return None
    return set(scripts) if isinstance(scripts, dict) else set()


def broken_references(root, command, bases, cache):
    """1つのコマンドや参照の中の、参照先が無いもの。[(参照, 理由)]。判定できないものは返さない。"""
    tokens = command.split()
    if not tokens:
        return []
    problems = []
    if tokens[0] in ("make", "gmake"):
        target = next((t for t in tokens[1:] if not t.startswith("-") and "=" not in t), None)
        if "make" not in cache:
            cache["make"] = _make_targets(root)
        targets = cache["make"]
        if targets is None:
            problems.append((" ".join(tokens[:2] if target else tokens[:1]), "Makefile が無い"))
        elif target and target not in targets:
            problems.append(("make %s" % target, "Makefile に target `%s` が無い" % target))
        return problems
    script = None
    if tokens[0] in ("npm", "pnpm", "yarn") and len(tokens) >= 3 and tokens[1] == "run":
        script, written = tokens[2], " ".join(tokens[:3])
    elif tokens[0] == "npm" and len(tokens) >= 2 and tokens[1] == "test":
        script, written = "test", "npm test"
    if script:
        if "npm" not in cache:
            cache["npm"] = _npm_scripts(root)
        scripts = cache["npm"]
        if scripts is None:
            problems.append((written, "package.json が無い"))
        elif script not in scripts:
            problems.append((written, "package.json に script `%s` が無い" % script))
        return problems
    for token in tokens:
        token = token.strip("()[],;:。、")
        if not _looks_like_path(token):
            continue
        if any((Path(base) / token).exists() for base in [root] + list(bases)):
            continue
        if _ignored(root, token):
            continue   # 実行時にできるファイル（記録など）。git が無視するものは、無くても誤りではない
        problems.append((token, "参照先が無い"))
    return problems


def _code_spans(text):
    """文書の中の、コードとして書かれた部分（インラインのコードと、コードブロックの各行）。"""
    spans, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            spans.append(line.strip())
        else:
            spans += [m.group(1).strip() for m in INLINE_CODE.finditer(line)]
    return [s for s in spans if s]


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


def broken_refs(root, texts):
    """D2：決まりに書かれた参照のうち、参照先が無いもの。プロジェクト内のファイルだけを見る。"""
    found, cache, seen = [], {}, set()
    for shown, path, kind, scope in texts:
        if scope != "project":
            continue
        if kind in ("hooks", "agent"):
            commands = _json_commands(path, kind)
        else:
            commands = _code_spans(_read(path) or "")
        bases = [path.parent] if kind == "skill" else []
        for command in commands:
            for ref, reason in broken_references(root, command, bases, cache):
                key = "D2:%s:%s" % (shown, ref)
                if key not in seen:
                    seen.add(key)
                    found.append({"key": key, "path": shown, "ref": mask(ref), "reason": reason})
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
            for line in (_read(Path(root) / name) or "").splitlines():
                for entry in tests:
                    try:
                        if re.search(entry["match"], line):
                            ci_runs_tests = True
                    except re.error:
                        continue
    lint = [n for n in files if n in LINT_FILES]
    for name, sections in LINT_SECTIONS.items():
        if name in files and any(s in (_read(Path(root) / name) or "") for s in sections):
            lint.append(name)
    return {"test_configs": len(tests), "hooks": hooks, "precommit": precommit, "ci": ci,
            "ci_runs_tests": ci_runs_tests, "lint": sorted(set(lint))}


def diagnose(root, config, hooks_by_name):
    """D1〜D3 と D5。ファイルだけから決まる。"""
    files = harness.repo_files(root)
    texts = harness_texts(root, files)
    return {"unloaded": unloaded_steering(texts, files), "broken": broken_refs(root, texts),
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


def lines(found, rows):
    """診断値を、人が読む行にする。骨組みと集約の表示で同じ文面を使う。"""
    out = []
    for row in found["unloaded"]:
        out.append("- `%s`：読み込みの条件 %s に当たるファイルが、リポジトリに無い。この決まりは読み込まれていない"
                   "（照合は kiroku による。Kiro の照合の細部は未確認）。" % (
                       row["key"], "、".join("`%s`" % p for p in row["patterns"])))
    for row in found["broken"]:
        out.append("- `%s`：%s。" % (row["key"], row["reason"]))
    for row in found["duplicates"]:
        out.append("- `%s`：同じ文が %d 行ある（例：「%s」）。" % (
            row["key"], row["lines"], row["sample"][:60] + ("…" if len(row["sample"]) > 60 else "")))
    if not (found["unloaded"] or found["broken"] or found["duplicates"]):
        out.append("- 読み込まれない決まり、参照先の無い記述、複数のファイルにある同じ文は、見つからなかった。")
    sense = found["sensors"]
    hooks = "、".join("%s %s" % (label, "・".join("`%s`" % n for n in names))
                     for label, names in sorted(sense["hooks"].items())) or "無し"
    ci = "、".join("`%s`" % n for n in sense["ci"]) or "無し"
    if sense["ci_runs_tests"] is not None:
        ci += "（テストの記録設定の match に一致する行が%s）" % ("ある" if sense["ci_runs_tests"] else "無い")
    out += ["- `D5`：事後の検査。テストの記録設定 %d 件。hook（kiroku 自身のものを除く）：%s。pre-commit：%s。"
            "CI：%s。lint・型検査の設定：%s。" % (
                sense["test_configs"], hooks, "、".join("`%s`" % n for n in sense["precommit"]) or "無し", ci,
                "、".join("`%s`" % n for n in sense["lint"]) or "無し")]
    if rows:
        out += ["", "型ごとの負担（`D6`。回数、セッション数、分母に対する割合）："]
        for row in rows:
            share = "%d%%" % row["share"] if row["share"] is not None else "分母なし"
            out.append("- `%s`：%d 回、%d セッション（%s %d 件のうち %s）" % (
                row["target"], row["count"], row["sessions"], row["base_label"], row["base"], share))
    return out
