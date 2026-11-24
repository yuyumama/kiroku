"""承認の記録：開発のセッションで、人が承認画面に答えた呼び出しを数え、許可の規則の候補を出す。

ログの形（Kiro CLI 3.3.0 と 3.5.0 で実測。docs/retro/verification.md）：
  承認画面に答えると、interaction_resolved が残る。
    承認（Allow）            outcome=selected、selectedOption=accept
    規則にして承認（Always allow）  outcome=selected、selectedOption=always-accept（3.5.0。
                             _meta.kiro.consentOutcome に scope と applied）
    拒否（Deny）             outcome=selected、selectedOption=reject
    取り消し（Escape など）   outcome=cancelled（selectedOption のキーが無い）
  3.3.0 では呼び出し（tool_call）と toolCallId が同じだったが、3.5.0 のシェルの呼び出しでは違った。
  結び方は excerpt.pair_interactions に任せ、結べなかった答えは数えて表に出す。
  知らない値は「不明」として別に数え、承認には数えない。

回数は、利用者に聞くきっかけであって、許可にする理由ではない。わざと毎回確かめている操作もある。
危険の区分は、コマンドの文面から決める（利用者の発話ではないので、発話を語で分類しない決まりには当たらない）。
規則の当たり方は、Kiro の資料の書き方（`*` は任意の文字の並び、つないだコマンドは分けて判定）を
kiroku が近似したもので、実機では確かめていない。
"""
import collections
import datetime
import os
import re
import shlex
from urllib.parse import urlparse

import commands
import excerpt
import prompts
from record import mask

APPROVED, ALWAYS, REJECTED, CANCELLED, UNKNOWN = "approved", "always", "rejected", "cancelled", "unknown"
PREFIX = "approval:"
CLASSES = {"irreversible": "取り返しがつかない", "code": "任意のコードの実行", "external": "外に出る",
           "other": "区分を決められない", "write": "手元の書き換え", "check": "手元の検査", "read": "読み取り"}
ORDER = ("irreversible", "code", "external", "other", "write", "check", "read")   # 危ないものが先
EXCLUDED = ("irreversible", "code", "external")   # 許可の候補にしない区分

# 1語の見出しにするプログラム（読み取りの道具と、テスト・lint の道具）。ほかは最初の2語で見出しにする。
ONE_WORD = {"ls", "cat", "head", "tail", "grep", "rg", "wc", "pwd", "which", "tree", "stat", "file", "du", "df",
            "echo", "printf", "date", "sort", "uniq", "diff", "cmp", "jq", "less", "more", "basename", "dirname",
            "realpath", "pytest", "jest", "vitest", "mocha", "rspec", "phpunit", "tox", "nox", "ruff", "mypy",
            "pyright", "flake8", "pylint", "black", "isort", "eslint", "prettier", "biome", "tsc", "shellcheck"}
NEUTRAL = {"cd", "pushd", "popd", "true", ":", "export", "set"}   # 作業場所や環境を変えるだけのもの
READ = ONE_WORD - {"pytest", "jest", "vitest", "mocha", "rspec", "phpunit", "tox", "nox", "ruff", "mypy", "pyright",
                   "flake8", "pylint", "black", "isort", "eslint", "prettier", "biome", "tsc", "shellcheck"}
CHECK = (ONE_WORD - READ) | {"make", "gradle", "gradlew", "mvn", "ctest"}
WRITE = {"mkdir", "mv", "cp", "touch", "ln", "rm", "rmdir", "chmod", "patch", "tee"}
EXTERNAL = {"curl", "wget", "gh", "ssh", "scp", "sftp", "ftp", "rsync", "nc", "telnet", "aws", "gcloud", "az",
            "kubectl", "helm", "terraform", "glab"}
CODE = {"eval", "exec", "xargs", "source", ".", "npx", "bunx", "pnpx", "uvx"}
INTERPRETERS = re.compile(r"(?:python(?:\d+(?:\.\d+)*)?|py|node|deno|ruby|perl|php|bash|sh|zsh|fish|pwsh|"
                          r"powershell)(?:\.exe)?")
IRREVERSIBLE = {"sudo", "doas", "dd", "shred", "kill", "pkill", "killall", "shutdown", "reboot"}
PACKAGE = {"npm", "pnpm", "yarn", "bun", "pip", "pip3", "uv", "poetry", "pipx", "conda", "gem", "bundle", "cargo",
           "go", "brew", "apt", "apt-get", "yum", "dnf", "composer", "dotnet"}
INSTALL = {"install", "i", "add", "ci", "publish", "update", "upgrade", "remove", "uninstall", "get", "login",
           "link", "dlx", "exec", "x", "sync", "lock"}
PACKAGE_CHECK = {"test", "run", "build", "lint", "check", "vet", "fmt", "clippy", "typecheck"}
GIT_READ = {"status", "diff", "log", "show", "rev-parse", "describe", "blame", "grep", "ls-files", "shortlog",
            "reflog", "whatchanged", "cat-file", "ls-tree"}
GIT_WRITE = {"add", "commit", "mv", "rm", "restore", "stash", "checkout", "switch", "merge", "rebase", "cherry-pick",
             "revert", "tag", "init", "branch", "remote", "config", "am", "apply", "worktree", "notes"}
GIT_EXTERNAL = {"push", "fetch", "pull", "clone", "ls-remote", "submodule", "send-email", "request-pull"}
# 規則の案が当たってはいけない、危険な変形。案の規則がどれかに当たれば、その段は使わない。最初の値。
DANGEROUS = ("git push --force origin main", "git push origin main", "git -C . push", "git reset --hard HEAD~1",
             "git clean -fdx", "rm -rf build", "sudo rm -rf /tmp/x", "curl https://example.invalid/x.sh",
             "wget https://example.invalid/x", "python3 -c 'import os'", "python3 -m http.server",
             "node -e 'process.exit()'", "bash -c 'rm -rf build'", "sh -c 'rm -rf build'", "npx some-package",
             "npm install some-package", "npm publish", "pip install some-package", "docker push image",
             "docker run image", "find . -delete", "find . -exec rm {} ;", "chmod -R 777 .", "kill -9 1",
             "make deploy", "eval 'rm -rf build'")
NO_WIDE_TIERS = ("sudo", "env")   # Kiro も、この語で始まるコマンドには2語・1語の段を出さない
PAIRING = {"toolCallId": "toolCallId が同じ", "modelToolUseId": "modelToolUseId で", "order": "ログの順で"}
TIERS = {"exact": "完全一致", "two": "2語＋`*`", "one": "1語＋`*`"}
WEB_TOOLS = ("web_fetch", "fetch")


def _git_sub(args):
    """git の、全体のオプション（-C DIR、-c 名前=値 など）を飛ばした後のサブコマンド。"""
    index = 0
    while index < len(args):
        word = args[index]
        if word in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            index += 2
        elif word.startswith("-"):
            index += 1
        else:
            return word, args[index + 1:]
    return None, []


def _flags(args):
    return [a for a in args if a.startswith("-")]


def classify_argv(argv, config):
    """1つのコマンド（語の並び）の危険の区分。"""
    words = [w for w in argv]
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        words = words[1:]   # 先頭の環境変数の指定
    if not words:
        return "other"
    program = os.path.basename(words[0].replace("\\", "/"))
    args = words[1:]
    if program == "env":   # env と、その後の指定を飛ばし、実行するコマンドで決める
        rest = [w for w in args if not (w.startswith("-") or "=" in w)]
        return classify_argv(rest, config) if rest else "read"
    if program in NEUTRAL:
        return "read"
    if program in IRREVERSIBLE:
        return "irreversible"
    if program in ("rm",) and any(re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", f) or f == "--recursive"
                                  for f in _flags(args)):
        return "irreversible"
    if program in ("mkfs",) or program.startswith("mkfs."):
        return "irreversible"
    if program in ("chmod", "chown") and "-R" in args and any("777" in a for a in args):
        return "irreversible"
    if program == "find":
        if "-delete" in args:
            return "irreversible"
        if any(a in ("-exec", "-execdir", "-ok", "-okdir") for a in args):
            return "code"
        return "read"
    if program == "git":
        sub, rest = _git_sub(args)
        if sub == "reset" and "--hard" in rest:
            return "irreversible"
        if sub == "push" and (any(f in ("--force", "-f", "--force-with-lease", "--mirror", "--delete", "-d")
                                  or f.startswith("--force") for f in _flags(rest))
                              or any(a.startswith("+") for a in rest)):
            return "irreversible"
        if sub == "clean" and any("f" in f.lstrip("-") for f in _flags(rest) if not f.startswith("--")):
            return "irreversible"
        if sub == "branch" and any(f in ("-D", "--delete") for f in rest):
            return "irreversible"
        if sub in GIT_EXTERNAL:
            return "external"
        if sub in GIT_READ:
            return "read"
        if sub in GIT_WRITE:
            return "write"
        return "other"
    if program in ("docker", "podman"):
        sub = args[0] if args else None
        if sub in ("push", "pull", "login"):
            return "external"
        if sub in ("exec", "run"):
            return "code"
        if sub in ("ps", "images", "logs", "inspect", "version", "info"):
            return "read"
        return "other"
    if program in EXTERNAL:
        return "external"
    if program in CODE:
        return "code"
    if INTERPRETERS.fullmatch(program):
        if any(a in ("-c", "-e", "--eval", "-m", "-r", "--command") or a.startswith("-c") for a in args[:2]):
            entry, _ = commands.test_match(shlex.join(words), config)
            return "check" if entry else "code"
        entry, _ = commands.test_match(shlex.join(words), config)
        return "check" if entry else "other"
    if program in PACKAGE:
        sub = next((a for a in args if not a.startswith("-")), None)
        if sub in INSTALL:
            return "external"
        if sub in PACKAGE_CHECK:
            return "check"
        return "other"
    entry, _ = commands.test_match(shlex.join(words), config)
    if entry or program in CHECK:
        return "check"
    if program in READ:
        return "read"
    if program == "sed":
        return "write" if any(f.startswith("-i") or f == "--in-place" for f in _flags(args)) else "read"
    if program in WRITE:
        return "write"
    return "other"


def _parts(command):
    """つないだコマンドを、1つずつのコマンドの語の並びに分ける（Kiro も、分けて1つずつ判定する）。"""
    parts, current, position, text = [], [], 0, command.strip()
    while position < len(text):
        match = commands.TOKEN.match(text, position)
        if not match:
            return [text.split()]   # 解釈できない入力は、全体を1つとして見る
        position = match.end()
        if match.group("op"):
            if match.group("op") in ("&&", "||", ";", "|", "&") and current:
                parts.append(current)
                current = []
            continue
        try:
            current += shlex.split(match.group("word"))
        except ValueError:
            current.append(match.group("word"))
    if current:
        parts.append(current)
    return parts


def classify(command, config):
    """コマンド全体の危険の区分。つないだコマンドは、いちばん危ないもの。"""
    found = [classify_argv(part, config) for part in _parts(command)] or ["other"]
    return min(found, key=ORDER.index)


def heading(command):
    """見出し。シェルは、作業場所の指定・リダイレクト・パイプ以降を除いた実コマンドの、最初の2語。

    読み取りの道具と、テスト・lint の道具は、最初の1語（引数の違いで見出しが分かれないように）。
    """
    key = commands.key(mask(command))
    try:
        words = shlex.split(key)
    except ValueError:
        words = key.split()
    if not words:
        return key
    program = os.path.basename(words[0].replace("\\", "/"))
    return shlex.join(words[:1 if program in ONE_WORD else 2])


def _glob(pattern, command):
    """Kiro のシェルの規則の照合（kiroku による近似）。`*` は任意の文字の並びで、コマンド全体に当てる。"""
    return re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), command) is not None


def rule_for(keys):
    """観測したコマンドの違いを覆う、いちばん狭い段の規則。決められなければ (None, 理由)。

    keys は、観測したコマンド（作業場所の指定・リダイレクト・パイプ以降を除いたもの）。
    規則は、Kiro の承認画面の段（完全一致、2語＋`*`、1語＋`*`）に合わせる。`git diff *` は、
    引数の無い `git diff` には当たらない（`*` の前に空白がある）ので、引数の無い形を観測していれば、
    その完全一致も規則に入れる（patterns）。
    """
    keys = sorted(set(keys))
    words = []
    for key in keys:
        try:
            words.append(shlex.split(key))
        except ValueError:
            words.append(key.split())
    if not keys or not all(words):
        return None, "コマンドを解釈できない"
    if len(keys) == 1:
        tier, prefix = "exact", None
    elif all(w[:2] == words[0][:2] for w in words) and len(words[0]) >= 2:
        tier, prefix = "two", shlex.join(words[0][:2])
    elif all(w[:1] == words[0][:1] for w in words):
        tier, prefix = "one", shlex.join(words[0][:1])
    else:
        return None, "観測したコマンドの先頭の語がそろわない"
    if tier != "exact" and words[0][0] in NO_WIDE_TIERS:
        return None, "`%s` で始まるコマンドは、完全一致でしか規則にしない" % words[0][0]
    patterns = keys[:1] if tier == "exact" else ([prefix] if prefix in keys else []) + [prefix + " *"]
    if any(p.strip() == "*" for p in patterns):
        return None, "`*` だけの規則は出さない"
    unmatched = [k for k in keys if not any(_glob(p, k) for p in patterns)]
    if unmatched:
        return None, "規則が観測したコマンドに当たらない（%s）" % unmatched[0]
    dangerous = [d for d in DANGEROUS if any(_glob(p, d) for p in patterns)]
    if dangerous:
        return None, "規則 %s が、危険な変形（`%s`）にも当たる" % ("、".join("`%s`" % p for p in patterns),
                                                       dangerous[0])
    return {"tier": tier, "patterns": patterns, "match": keys[:3], "not_match": _examples(words[0][0])}, None


def _examples(program):
    """当たらない例。同じ先頭の語の危険な変形を先に、無ければ一般的なものを2つ。"""
    same = [d for d in DANGEROUS if d.split()[0] == program]
    return (same + [d for d in DANGEROUS if d not in same])[:2]


def _decision(resolved):
    outcome, option = resolved.get("outcome"), resolved.get("selectedOption")
    if outcome == "cancelled":
        return CANCELLED, None
    if option == "accept":
        return APPROVED, None
    if option == "always-accept":
        consent = ((resolved.get("_meta") or {}).get("kiro") or {}).get("consentOutcome") \
            if isinstance(resolved.get("_meta"), dict) else None
        scope = consent.get("scope") if isinstance(consent, dict) else None
        return ALWAYS, scope if isinstance(scope, str) else None
    if option == "reject":
        return REJECTED, None
    return UNKNOWN, "outcome=%s、selectedOption=%s" % (outcome, option if "selectedOption" in resolved else "キーなし")


def _domain(args):
    url = args.get("url") if isinstance(args, dict) else None
    try:
        return urlparse(url).hostname if isinstance(url, str) else None
    except ValueError:
        return None


def collect(root, session_ids, config):
    """承認画面に答えた呼び出し。開発のセッションだけ（振り返りの後と、kiroku 自身のコマンドを除く）。

    戻り値は (答え, 読めたログの数, ワークスペースのハッシュの一覧, 結び方)。答えは
    {session_id, ts, decision, raw, tool, heading, command, key, class}。結び方は
    {"methods": {結び方: 件数}, "unpaired": [結べなかった答えの時刻]}。
    """
    directory, marks = prompts.sessions_dir(root), prompts.retro_marks(root)
    found, read, hashes = [], 0, collections.Counter()
    pairing = {"methods": collections.Counter(), "unpaired": []}
    for session in session_ids:
        path = excerpt.find_log(session, directory)
        if not path:
            continue
        try:
            rows = excerpt.load(path, types=None)
        except (OSError, UnicodeError):
            continue
        read += 1
        hashes[os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(path))))] += 1
        cutoff = prompts.retro_start([r for r in rows if r[1].get("type") in excerpt.SHOWN_TYPES], marks.get(session))
        calls = {p.get("toolCallId"): p for _, p in rows
                 if p.get("type") == "tool_call" and isinstance(p.get("toolCallId"), str)}
        paired, unpaired, ways = excerpt.pair_interactions(rows)
        owner = {id(answer): call_id for call_id, answers in paired.items() for answer in answers}
        unpaired_ids = {id(answer) for answer in unpaired}
        for stamp, payload in rows:
            if payload.get("type") != "interaction_resolved":
                continue
            moment = excerpt.parse_time(stamp)
            if moment is None or (cutoff and moment >= cutoff):
                continue
            if id(payload) in unpaired_ids:
                pairing["unpaired"].append(moment)   # 黙って捨てず、集約に件数を出す
                continue
            call = calls.get(owner.get(id(payload)))
            if call is None:
                continue
            pairing["methods"][ways[id(payload)]] += 1
            decision, raw = _decision(payload)
            tool = call.get("toolName") if isinstance(call.get("toolName"), str) else "不明"
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            row = {"session_id": session, "ts": moment, "decision": decision, "raw": raw, "tool": tool,
                   "command": None, "key": None}
            if tool in prompts.SHELL_TOOLS:
                command = args.get("command")
                if not isinstance(command, str) or commands.own_script(command):
                    continue
                row.update(command=mask(command), key=commands.key(mask(command)), heading=heading(command),
                           **{"class": classify(command, config)})
            elif tool in WEB_TOOLS:
                row.update(heading="%s %s" % (tool, _domain(args) or "不明"), **{"class": "other"})
            else:
                row.update(heading=tool, **{"class": "read" if tool in ("fs_read", "read") else
                                            "write" if tool in ("fs_write", "write") else "other"})
            found.append(row)
    return found, read, [h for h, _ in hashes.most_common()], pairing


def rules_file(hashes):
    """ワークスペースの規則のファイル。セッションのログの置き場のフォルダ名が <hash>（CLI 3.2.1 で実測）。"""
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    for name in hashes:
        if name in ("", ".", "..", "sessions"):
            continue
        path = os.path.join(home, "workspace-roots", name, "permissions.yaml")
        shown = "~/.kiro/workspace-roots/%s/permissions.yaml" % name
        try:
            changed = os.path.getmtime(path)
        except OSError:
            return {"path": shown, "exists": False, "changed": None}
        return {"path": shown, "exists": True,
                "changed": datetime.datetime.fromtimestamp(changed, datetime.timezone.utc)}
    return None


def summarize(answers, read, hashes, since, until, pairing, min_sessions):
    """見出しごとにまとめ、候補を決める。回数とセッション数は期間内、拒否は全期間で見る。

    Always allow で規則にした見出しは、もう規則があるので候補にしない。
    """
    def inside(row):
        return (since is None or row["ts"] >= since) and (until is None or row["ts"] < until)

    rules = rules_file(hashes)
    groups = collections.OrderedDict()
    for row in sorted(answers, key=lambda r: r["ts"]):
        groups.setdefault(row["heading"], []).append(row)
    out = []
    for name, rows in groups.items():
        period = [r for r in rows if inside(r)]
        if not period:
            continue
        approved = [r for r in period if r["decision"] == APPROVED]
        sessions = {r["session_id"] for r in approved}
        ever_rejected = any(r["decision"] == REJECTED for r in rows)
        always = [r for r in rows if r["decision"] == ALWAYS]
        kind = min((r["class"] for r in rows), key=ORDER.index)
        entry = {"heading": name, "target": PREFIX + name, "tool": period[-1]["tool"], "class": kind,
                 "approved": len(approved), "sessions": len(sessions),
                 "always": sum(1 for r in period if r["decision"] == ALWAYS),
                 "rejected": sum(1 for r in period if r["decision"] == REJECTED),
                 "cancelled": sum(1 for r in period if r["decision"] == CANCELLED),
                 "unknown": sum(1 for r in period if r["decision"] == UNKNOWN),
                 "last_approved": max((r["ts"] for r in approved), default=None),
                 "candidate": False, "deny": False, "why": None, "rule": None, "rule_note": None,
                 "rules_changed": False}
        if kind == "irreversible":
            entry["deny"] = bool(approved)
            entry["why"] = "取り返しがつかない操作。許可の候補にせず、deny の案の候補にする"
        elif kind in EXCLUDED:
            entry["why"] = "危険の区分のため、許可の候補にしない"
        elif always:
            entry["why"] = "Always allow で規則にした（%s、範囲：%s）" % (
                _when(always[-1]["ts"]), always[-1]["raw"] or "不明")
        elif ever_rejected:
            entry["why"] = "拒否したことがある（中身を見て判断している）"
        elif len(sessions) < min_sessions:
            entry["why"] = "承認したセッションが %d 件（候補は %d 件から）" % (len(sessions), min_sessions)
        else:
            entry["candidate"] = True
            if period[-1]["tool"] in prompts.SHELL_TOOLS:
                entry["rule"], entry["rule_note"] = rule_for([r["key"] for r in approved])
            else:
                entry["rule_note"] = "シェル以外の規則の書き方は、kiroku では確かめていない。承認画面の Always allow を使う"
            if rules and rules["changed"] and entry["last_approved"] and rules["changed"] > entry["last_approved"]:
                entry["rules_changed"] = True
        out.append(entry)
    out.sort(key=lambda e: (not e["candidate"], not e["deny"], -e["sessions"], -e["approved"], e["heading"]))
    unknown = collections.Counter(r["raw"] for r in answers if r["decision"] == UNKNOWN and inside(r))
    return {"groups": out, "logs": read, "rules_file": rules, "min_sessions": min_sessions,
            "candidates": [e["target"] for e in out if e["candidate"]],
            "unknown_values": sorted(unknown.items()),
            "pairing": dict(sorted((pairing or {}).get("methods", {}).items())),
            "unpaired": sum(1 for t in (pairing or {}).get("unpaired", [])
                            if (since is None or t >= since) and (until is None or t < until))}


def occurrences(answers, target):
    """台帳の対象の型（approval:<見出し>）の、承認の時刻。採用した後に承認が減ったかを数える。"""
    name = target[len(PREFIX):]
    return [r["ts"] for r in answers if r["heading"] == name and r["decision"] in (APPROVED, ALWAYS)]


def _when(moment):
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d") if moment else "なし"


def lines(found, cap=None):
    """集約に出す行。"""
    out = []
    rules = found["rules_file"]
    if rules:
        out.append("- ワークスペースの規則のファイル：`%s`（%s）" % (
            rules["path"], "あり。更新 %s" % _when(rules["changed"]) if rules["exists"] else "まだ無い"))
    else:
        out.append("- ワークスペースの規則のファイル：見つからない（ログが Kiro の既定の置き場に無い）")
    if found.get("pairing"):
        out.append("- 答えと呼び出しの結び方（ログ全体）：%s" % "、".join(
            "%s %d 件" % (PAIRING.get(k, k), v) for k, v in found["pairing"].items()))
    if found.get("unpaired"):
        out.append("- 呼び出しに結べなかった答え：%d 件（数えていない。ログの形が変わった可能性がある）" % found["unpaired"])
    groups = found["groups"]
    candidates = [g for g in groups if g["candidate"]]
    denies = [g for g in groups if g["deny"]]
    others = [g for g in groups if not g["candidate"] and not g["deny"]]
    out += ["", "### 許可の候補（%d 件）" % len(candidates), ""]
    if not candidates:
        out.append("なし")
    for g in candidates:
        text = "- `%s`：%s、区分：%s。" % (g["target"], _counts(g), CLASSES[g["class"]])
        if g["rule"]:
            text += "規則の案：%s（%s）。当たる例：%s。当たらない例：%s" % (
                "、".join("`%s`" % p for p in g["rule"]["patterns"]), TIERS[g["rule"]["tier"]],
                "、".join("`%s`" % m for m in g["rule"]["match"]), "、".join("`%s`" % m for m in g["rule"]["not_match"]))
        else:
            text += "規則の案なし（%s）" % g["rule_note"]
        if g["rules_changed"]:
            text += "。最後の承認の後に、ワークスペースの規則のファイルが変わった（もう規則にしてあるかもしれない）"
        out.append(text)
    out += ["", "### 取り返しがつかない操作の承認（deny の案の候補。%d 件）" % len(denies), ""]
    out += ["- `%s`：%s。%s" % (g["target"], _counts(g), g["why"]) for g in denies] or ["なし"]
    out += ["", "### 候補にしない承認（%d 件）" % len(others), ""]
    shown = others if cap is None else others[:cap * 2]
    out += ["- `%s`：%s、区分：%s。%s" % (g["heading"], _counts(g), CLASSES[g["class"]], g["why"]) for g in shown] \
        or ["なし"]
    if len(shown) < len(others):
        out.append("- ほか %d 件。全件は `--section approvals` で出します。" % (len(others) - len(shown)))
    if found["unknown_values"]:
        out += ["", "### 知らない答えの値（承認には数えていない）", ""]
        out += ["- %s：%d 回（kiroku が知らない値。承認に数えない）" % (raw, count)
                for raw, count in found["unknown_values"]]
    return out


def _counts(g):
    text = "承認 %d 回（%d セッション）、拒否 %d 回、取り消し %d 回" % (g["approved"], g["sessions"], g["rejected"],
                                                          g["cancelled"])
    if g.get("always"):
        text += "、Always allow %d 回" % g["always"]
    if g["unknown"]:
        text += "、不明 %d 回" % g["unknown"]
    return text + "、最後の承認 %s" % _when(g["last_approved"])
