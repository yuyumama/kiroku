"""ベストプラクティス集との照らし合わせ。項目ごとに、当てはまるか、満たしているかを決める。

集（references/best-practices.md）は、kiroku が配る、プロジェクトに依らないハーネスのベストプラクティス。
項目の ID、名前、確認日は集から読み、判定はここで ID ごとに持つ（集の ID と、この判定の ID が
一致することは、テストで確かめる）。判定はファイルと記録だけから決め、同じ入力からは同じ結果になる。
決まりの中身が正しいかは判定しない。振り返りのエージェントが読んで判断する。

状態：
  満たしている     満たした証拠がある
  満たしていない   当てはまり、証拠が無い。案の候補（practice:<ID>）
  当てはまらない   当てはまる条件を満たさない
  未確認           スクリプトでは決められない。エージェントが会話と文書から判断する

見送った項目（台帳で、対象の型が practice:<ID> の案を見送ったもの）は、候補から外す。
集のその項目の確認日が、見送った日より新しくなったら（外の知見が変わったら）、また候補にする。
経過した日数では、何も変えない。
"""
import glob
import json
import os
import re
import subprocess
from pathlib import Path

import harness
import ledger

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTION = os.path.normpath(os.path.join(HERE, "..", "references", "best-practices.md"))
SHOWN_PATH = ".kiro/skills/kiroku-retro/references/best-practices.md"   # 導入先での置き場所
MARK = re.compile(r"<!-- kiroku-best-practices:(\d+) -->")
ITEM = re.compile(r"^## (BP-[A-Z]\d+)\. (.+)$", re.M)
CHECKED = re.compile(r"^- 確認日：(\d{4}-\d{2}-\d{2})\s*$", re.M)
PREFIX = "practice:"

MET, UNMET, NOT_APPLICABLE, UNKNOWN = "満たしている", "満たしていない", "当てはまらない", "未確認"

OWN_HOOK_PREFIX = "kiroku-record-"
OWN_AGENT = "kiroku-retro.json"
OWN_SKILL = ".kiro/skills/kiroku-retro/"
# 作業の終わり・保存時・作成時・タスクの後のトリガー（小文字）。BP-S2。
END_TRIGGERS = {"stop", "agentstop", "postfilesave", "filesave", "postfilecreate", "filecreate", "posttaskexecution"}
START_TRIGGERS = {"sessionstart", "agentspawn"}
TRIGGER_LABELS = {"stop": "終了時", "agentstop": "終了時", "postfilesave": "保存時", "filesave": "保存時",
                  "postfilecreate": "作成時", "filecreate": "作成時", "posttaskexecution": "タスクの後",
                  "sessionstart": "セッション開始時", "agentspawn": "セッション開始時", "pretooluse": "実行前"}
# hook のコマンドに名前が出れば、lint・型検査・整形の道具とみなす。最初の値で、根拠は薄い。
LINT_TOOLS = ("ruff", "flake8", "pylint", "mypy", "pyright", "black", "isort", "eslint", "biome", "prettier", "tsc",
              "golangci-lint", "gofmt", "rubocop", "rustfmt", "clippy", "shellcheck", "stylelint")
# 取り返しのつかない操作の例。deny 規則がこれに当たるかを見る（BP-P1）。最初の値。
DANGEROUS_EXAMPLES = ("rm -rf build", "sudo apt-get install x", "git push --force origin main",
                      "git reset --hard HEAD~1")
# テストのファイル（BP-S1）。最初の値で、根拠は薄い。
TEST_FILES = (re.compile(r"(^|/)tests?/"), re.compile(r"(^|/)test_[^/]+\.py$"), re.compile(r"_test\.(py|go)$"),
              re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$"), re.compile(r"(^|/)__tests__/"),
              re.compile(r"(^|/)spec/.+_spec\.rb$"), re.compile(r"(^|/)src/test/"),
              re.compile(r"Tests?\.(java|kt|cs)$"))
TEST_SCRIPTS = ("run_tests.py", "run_tests.sh", "test.sh")
NO_TEST_SCRIPT = 'echo "Error: no test specified" && exit 1'   # npm init が書く、何もしない test
# プログラムのコード（BP-S3）。最初の値。
CODE_EXTENSIONS = {"py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "go", "rs", "java", "kt", "rb", "php", "cs",
                   "swift", "c", "cc", "cpp", "h", "hpp", "scala", "sh", "dart", "lua", "ex", "exs"}
# 秘密らしいファイル（BP-P3）。Kiro の Permissions の例から。最初の値。
SECRET_EXAMPLES = ("example", "sample", "template", "dist")
SECRET_DEPTH = 3   # ルートから何階層まで探すか。最初の値
SKIP_DIRS = harness.SKIP_DIRS | {".kiro"}
# 依存を定義するファイルと、ロックファイル（BP-E1）。
LOCKS = {"package.json": ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
                          "bun.lock"),
         "pyproject.toml": ("poetry.lock", "uv.lock", "pdm.lock"),
         "Pipfile": ("Pipfile.lock",), "go.mod": ("go.sum",), "Cargo.toml": ("Cargo.lock",),
         "Gemfile": ("Gemfile.lock",), "composer.json": ("composer.lock",)}
REQUIREMENTS = re.compile(r"^requirements[\w.-]*\.txt$")


def collection(path=COLLECTION):
    """集の版と、項目（ID、名前、確認日）。読めなければ None。"""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeError):
        return None
    mark = MARK.search(text)
    found = list(ITEM.finditer(text))
    items = []
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(text)
        body = text[match.end():end]
        cut = re.search(r"^## ", body, re.M)   # 項目でない節（参考など）の手前まで
        body = body[:cut.start()] if cut else body
        checked = CHECKED.search(body)
        items.append({"id": match.group(1), "title": match.group(2).strip(),
                      "checked": checked.group(1) if checked else None, "has_source": "- 出典：" in body})
    return {"version": int(mark.group(1)) if mark else None, "items": items}


def _read(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _shell_glob(pattern, command):
    """シェルの規則の照合（kiroku による近似）。`*` は任意の文字の並びで、コマンド全体に当てる。"""
    return re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), command) is not None


def _path_glob(pattern, path):
    """パスの規則の照合（kiroku による近似）。`**` は階層をまたぎ、`*` と `?` はまたがない。"""
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
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch("".join(out), path) is not None


def _denied_by(agents, capability, value, matcher):
    """value を止める deny 規則があるエージェント定義の名前。"""
    names = []
    for name, rules in agents:
        for rule in rules:
            if rule.get("capability") != capability or rule.get("effect") != "deny":
                continue
            match = [p for p in rule.get("match") or [] if isinstance(p, str)]
            exclude = [p for p in rule.get("exclude") or [] if isinstance(p, str)]
            if any(matcher(p, value) for p in match) and not any(matcher(p, value) for p in exclude):
                names.append(name)
                break
    return names


class Facts:
    """判定に使う、ファイルと記録から読んだもの。"""

    def __init__(self, root, data, config):
        self.root = root
        self.data = data
        self.files = [f for f in harness.repo_files(root) if not f.startswith(".kiro/")]
        self.tests = [t for t in config.get("tests") or [] if isinstance(t, dict) and isinstance(t.get("match"), str)]
        self.sensors = (data.get("diagnostics") or {}).get("sensors") or {}
        self.steering = data.get("steering") or {"files": [], "always_total": 0}
        self.hooks = self._hooks()
        self.agents = self._agents()

    def _hooks(self):
        """kiroku 以外の hook の (名前, トリガー（小文字）, コマンド)。プロジェクト内とユーザー共通。"""
        home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
        found = []
        for directory in (os.path.join(self.root, ".kiro", "hooks"), os.path.join(home, "hooks")):
            for path in sorted(glob.glob(os.path.join(glob.escape(directory), "*.json"))):
                try:
                    hooks = json.loads(_read(path) or "").get("hooks")
                except (ValueError, AttributeError):
                    continue
                for hook in hooks if isinstance(hooks, list) else []:
                    if not isinstance(hook, dict) or not isinstance(hook.get("name"), str) \
                            or hook["name"].startswith(OWN_HOOK_PREFIX):
                        continue
                    action = hook.get("action") if isinstance(hook.get("action"), dict) else {}
                    command = action.get("command") if isinstance(action.get("command"), str) else hook.get("command")
                    found.append((hook["name"], str(hook.get("trigger") or "").lower(),
                                  command if isinstance(command, str) else ""))
        return found

    def _agents(self):
        """リポジトリの中のエージェント定義（kiroku 自身のものを除く）の、(ファイル名, 権限の規則)。"""
        found = []
        for path in sorted(glob.glob(os.path.join(glob.escape(os.path.join(self.root, ".kiro", "agents")), "*.json"))):
            name = os.path.basename(path)
            if name == OWN_AGENT:
                continue
            try:
                profile = json.loads(_read(path) or "")
            except ValueError:
                continue
            permissions = profile.get("permissions") if isinstance(profile, dict) else None
            rules = permissions.get("rules") if isinstance(permissions, dict) else None
            found.append((name, [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []))
        return found

    def test_matches(self, text):
        for entry in self.tests:
            try:
                if re.search(entry["match"], text):
                    return True
            except re.error:
                continue
        return False

    def regressions(self):
        return sum(1 for row in self.data.get("test_failures") or [] if row.get("class") == "regression")

    def signals_for_checks(self):
        """検査の仕組みが要りそうな兆し（BP-S2、BP-S4）。"""
        out = []
        regressions = self.regressions()
        if regressions:
            out.append("回帰候補 %d 件" % regressions)
        written = self.sensors.get("after_writes")
        if written and written.get("turns") and written.get("tested", 0) < written["turns"]:
            out.append("書き込みのあったターン %d 件のうち、最後の書き込みの後にテストを実行したもの %d 件"
                       % (written["turns"], written["tested"]))
        return out


def _always(facts):
    return [row for row in facts.steering.get("files") or [] if row.get("inclusion") == "always"]


def _names(values, limit=3):
    values = list(values)
    shown = "、".join("`%s`" % v for v in values[:limit])
    return shown + ("ほか %d 件" % (len(values) - limit) if len(values) > limit else "")


def check_g1(facts, done):
    always = _always(facts)
    if always:
        return MET, "常時読み込み：%s" % _names(row["display"] for row in always), []
    return UNMET, "AGENTS.md も、常時読み込みの steering も無い", []


def _test_means(facts):
    """テストのファイルか、テストを実行する手段の例。無ければ None。"""
    for name in facts.files:
        if any(p.search(name) for p in TEST_FILES):
            return name
    for name in facts.files:
        base = name.rsplit("/", 1)[-1]
        if base in TEST_SCRIPTS:
            return name
        if base == "package.json":
            try:
                script = (json.loads(_read(os.path.join(facts.root, name)) or "").get("scripts") or {}).get("test")
            except (ValueError, AttributeError):
                script = None
            if isinstance(script, str) and script.strip() and script.strip() != NO_TEST_SCRIPT:
                return name + " の test"
        if base == "Makefile" and re.search(r"(?m)^test\s*:", _read(os.path.join(facts.root, name)) or ""):
            return name + " の test"
    return None


def check_s1(facts, done):
    means = _test_means(facts)
    if means is None:
        return NOT_APPLICABLE, ("テストのファイルも、テストを実行する手段（package.json の test、Makefile の test、"
                                "テスト用のスクリプト）も見つからない"), []
    if facts.tests:
        return MET, "テストの記録設定 %d 件（%s）" % (len(facts.tests), _names(t["match"] for t in facts.tests)), []
    gaps = len(facts.data.get("test_result_gaps") or [])
    return UNMET, "テストはある（`%s`）が、kiroku のテストの記録設定が無い" % means, \
        (["結果を取り込めなかったテストの実行 %d 件" % gaps] if gaps else [])


def check_g2(facts, done):
    if done["BP-S1"] != MET:
        return NOT_APPLICABLE, "BP-S1 を満たしていない（テストの記録設定が無いか、テストが無い）", []
    for row in _always(facts):
        for line in (_read(row["path"]) or "").splitlines():
            if facts.test_matches(line):
                return MET, "`%s` に、テストの記録設定に当たる行がある" % row["display"], []
    return UNMET, "常時読み込みの決まりに、テストの記録設定（%s）に当たる行が無い" % _names(
        t["match"] for t in facts.tests), []


def _is_check(facts, command):
    return facts.test_matches(command) or any(
        re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(tool), command) for tool in LINT_TOOLS)


def check_s2(facts, done):
    if done["BP-S1"] != MET:
        return NOT_APPLICABLE, "BP-S1 を満たしていない", []
    ends = [(name, trigger, command) for name, trigger, command in facts.hooks if trigger in END_TRIGGERS]
    checks = [(name, trigger) for name, trigger, command in ends if _is_check(facts, command)]
    if checks:
        return MET, "%s" % "、".join("`%s`（%s）" % (name, TRIGGER_LABELS.get(trigger, trigger))
                                     for name, trigger in checks), []
    signals = facts.signals_for_checks()
    if ends:
        return UNMET, "終了時・保存時の hook（%s）は、テストの記録設定にも、lint・型検査・整形の道具にも当たらない" % "、".join(
            "`%s`：`%s`" % (name, command or "コマンドなし") for name, trigger, command in ends[:3]), signals
    return UNMET, "作業の終わり・保存時・タスクの後に動く hook（kiroku 以外）が無い", signals


def check_s3(facts, done):
    if not any(name.rsplit(".", 1)[-1] in CODE_EXTENSIONS for name in facts.files if "." in name.rsplit("/", 1)[-1]):
        return NOT_APPLICABLE, "プログラムのコードが無い", []
    lint = facts.sensors.get("lint") or []
    if lint:
        return MET, "設定：%s" % _names(lint), []
    return UNMET, "lint・型検査の設定ファイルが無い", []


def check_s4(facts, done):
    if MET not in (done["BP-S1"], done["BP-S3"]):
        return NOT_APPLICABLE, "BP-S1 も BP-S3 も満たしていない", []
    precommit = facts.sensors.get("precommit") or []
    if precommit:
        return MET, "設定：%s" % _names(precommit), []
    return UNMET, "コミットの前の検査の設定（`.pre-commit-config.yaml`、husky、lefthook、`.git/hooks/pre-commit`）が無い", \
        facts.signals_for_checks()


def _remotes(root):
    try:
        done = subprocess.run(["git", "-C", str(root), "remote"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return [line for line in done.stdout.splitlines() if line.strip()] if done.returncode == 0 else None


def check_s5(facts, done):
    if done["BP-S1"] != MET:
        return NOT_APPLICABLE, "BP-S1 を満たしていない", []
    remotes = _remotes(facts.root)
    if remotes is None:
        return UNKNOWN, "git のリモートを確かめられない", []
    if not remotes:
        return NOT_APPLICABLE, "git のリモートが無い（手元だけで完結するプロジェクト）", []
    if facts.sensors.get("ci_runs_tests"):
        return MET, "CI の定義（%s）に、テストの記録設定に当たる行がある" % _names(facts.sensors.get("ci") or []), []
    ci = facts.sensors.get("ci") or []
    if ci:
        return UNMET, "CI の定義（%s）の、コメントを除いた行に、テストの記録設定に当たる行が無い" % _names(ci), []
    return UNMET, "CI の定義が無い", []


def _guards(facts):
    """実行前の hook（kiroku 以外）。何を止めるかは、エージェントが読んで確かめる。"""
    names = [name for name, trigger, command in facts.hooks if trigger == "pretooluse"]
    return "。実行前の hook %s がある（何を止めるかは、読んで確かめる）" % _names(names) if names else ""


def check_p1(facts, done):
    missing = [c for c in DANGEROUS_EXAMPLES if not _denied_by(facts.agents, "shell", c, _shell_glob)]
    signals = []
    blocked = sum(r.get("count", 0) for r in facts.data.get("unrecorded_calls") or [] if r.get("kind") == "blocked")
    if blocked:
        signals.append("実行前に止められた呼び出し %d 回" % blocked)
    if not missing:
        return MET, "エージェント定義の deny 規則が、例のコマンドすべてに当たる", signals
    return UNKNOWN, ("リポジトリの中のエージェント定義の deny 規則は、%s に当たらない。ワークスペースと"
                     "ユーザー共通の規則（リポジトリの外）は見ていない%s") % (_names(missing, 4), _guards(facts)), signals


def check_p2(facts, done):
    found = facts.data.get("approvals")
    if not found or not found.get("logs"):
        return UNKNOWN, "承認の記録を読めるセッションのログが無い", []
    candidates = found.get("candidates") or []
    if candidates:
        return UNMET, "毎回承認している操作の候補：%s" % _names(candidates), ["承認の候補 %d 件" % len(candidates)]
    return MET, "別々のセッションで繰り返し承認し、拒否の無い操作は無い（ログ %d 件）" % found["logs"], []


def _secret_files(root):
    found = []
    base = Path(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        depth = len(Path(directory).relative_to(base).parts)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS) if depth < SECRET_DEPTH else []
        for name in sorted(files):
            lower = name.lower()
            env = lower == ".env" or (lower.startswith(".env.") and lower[5:] not in SECRET_EXAMPLES)
            if env or lower.endswith((".pem", ".key")):
                found.append((Path(directory) / name).relative_to(base).as_posix())
    return found


def check_p3(facts, done):
    secrets = _secret_files(facts.root)
    if not secrets:
        return NOT_APPLICABLE, "秘密らしいファイル（`.env`、`*.pem`、`*.key` など）が見つからない", []
    missing = [s for s in secrets if not _denied_by(facts.agents, "fs_read", s, _path_glob)]
    if not missing:
        return MET, "エージェント定義の読み取りの deny 規則が、%s に当たる" % _names(secrets), []
    return UNKNOWN, ("%s の読み取りを止める規則が、リポジトリの中のエージェント定義に無い。ワークスペースと"
                     "ユーザー共通の規則（リポジトリの外）は見ていない") % _names(missing), []


def check_g3(facts, done):
    total = facts.steering.get("always_total") or 0
    if total <= harness.STEERING_GUIDE_CHARS:
        return NOT_APPLICABLE, "常時読み込みの合計 %d 文字（目安 %d 文字以下）" % (total, harness.STEERING_GUIDE_CHARS), []
    conditional = [row["display"] for row in facts.steering.get("files") or []
                   if row.get("inclusion") in ("fileMatch", "auto", "manual")]
    signals = ["常時読み込みの合計 %d 文字（目安 %d 文字）" % (total, harness.STEERING_GUIDE_CHARS)]
    if conditional:
        return MET, "条件付きの steering：%s" % _names(conditional), signals
    return UNMET, "条件付き（fileMatch、auto、manual）の steering が無い", signals


def _skills(facts):
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    names = []
    for entry in facts.data.get("harness") or []:
        if not entry.get("path", "").replace("\\", "/").endswith("/skills"):
            continue
        base, prefix = (Path(facts.root), "") if entry.get("scope") == "project" else (Path(home), "~/.kiro/")
        for path in entry.get("files") or []:
            try:
                name = prefix + Path(path).relative_to(base).as_posix()
            except ValueError:
                continue
            if name.endswith("/SKILL.md") and not name.startswith(OWN_SKILL):
                names.append(name)
    return names


def check_g4(facts, done):
    candidates = [t for t in facts.data.get("topics") or [] if t.get("candidate")]
    if not candidates:
        return NOT_APPLICABLE, "会話からの型の候補が無い", []
    signals = ["会話からの型の候補 %d 件" % len(candidates)]
    skills = _skills(facts)
    if skills:
        return MET, "kiroku 以外の skill：%s（型に関わるかは、読んで決める）" % _names(skills), signals
    return UNMET, "kiroku 以外の skill が無い", signals


def check_r1(facts, done):
    specs = Path(facts.root) / ".kiro" / "specs"
    count = sum(len(files) for _, _, files in os.walk(specs)) if specs.is_dir() else 0
    if count:
        return MET, "`.kiro/specs/` に %d 個のファイルがある" % count, []
    return UNKNOWN, "`.kiro/specs/` が無い。大きな変更があったかは、会話から判断する", []


def check_r2(facts, done):
    starts = [name for name, trigger, command in facts.hooks if trigger in START_TRIGGERS]
    if starts:
        return MET, "セッション開始時の hook：%s" % _names(starts), []
    return UNKNOWN, "セッション開始時の hook（kiroku 以外）が無い。始めに毎回、状態を説明し直しているかは、会話から判断する", []


def _has_dependencies(base, text):
    if text is None:
        return False
    if base in ("package.json", "composer.json"):
        try:
            value = json.loads(text)
        except ValueError:
            return False
        keys = ("require", "require-dev") if base == "composer.json" else (
            "dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
        return isinstance(value, dict) and any(isinstance(value.get(k), dict) and value[k] for k in keys)
    if base == "pyproject.toml":
        return bool(re.search(r"(?m)^\s*dependencies\s*=\s*\[\s*[\"']", text)
                    or re.search(r"(?m)^\[tool\.poetry\.(dev-)?dependencies\]", text))
    if REQUIREMENTS.match(base):
        return bool(_requirements(text))
    if base == "go.mod":
        return bool(re.search(r"(?m)^require\b", text))
    if base in ("Cargo.toml", "Pipfile"):
        section = r"(dev-)?dependencies" if base == "Cargo.toml" else r"(dev-)?packages"
        return bool(re.search(r"(?m)^\[%s\]\s*\n\s*[A-Za-z0-9_.-]+\s*=" % section, text))
    if base == "Gemfile":
        return bool(re.search(r"(?m)^\s*gem\s+['\"]", text))
    return False


def _requirements(text):
    lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("-")]


def check_e1(facts, done):
    present = set(facts.files)
    missing, manifests = [], 0
    for name in facts.files:
        base = name.rsplit("/", 1)[-1]
        if base not in LOCKS and not REQUIREMENTS.match(base):
            continue
        text = _read(os.path.join(facts.root, name))
        if not _has_dependencies(base, text):
            continue
        manifests += 1
        if REQUIREMENTS.match(base):
            if any("==" not in line for line in _requirements(text)):
                missing.append(name)
            continue
        folder = name.rsplit("/", 1)[0] if "/" in name else ""
        # ワークスペース（モノレポ）では、ロックファイルは上の階層にある。同じフォルダと、その上を見る。
        parents = [folder]
        while folder:
            folder = folder.rsplit("/", 1)[0] if "/" in folder else ""
            parents.append(folder)
        if not any((p + "/" if p else "") + lock in present for p in parents for lock in LOCKS[base]):
            missing.append(name)
    if not manifests:
        return NOT_APPLICABLE, "依存を定義するファイルが無い（または、依存が書かれていない）", []
    if missing:
        return UNMET, "%s に対応するロックファイルが無い（requirements は、`==` で固定していない行がある）" % _names(missing), []
    return MET, "依存を定義するファイル %d 個に、ロックファイルか、固定した版がある" % manifests, []


CHECKS = {"BP-G1": check_g1, "BP-S1": check_s1, "BP-G2": check_g2, "BP-S2": check_s2, "BP-S3": check_s3,
          "BP-S4": check_s4, "BP-S5": check_s5, "BP-P1": check_p1, "BP-P2": check_p2, "BP-P3": check_p3,
          "BP-G3": check_g3, "BP-G4": check_g4, "BP-R1": check_r1, "BP-R2": check_r2, "BP-E1": check_e1}


def _decided(root):
    """台帳で、対象の型が practice:<ID> の案への最新の判断。{ID: (判断, 日付)}。"""
    found = {}
    for entry in sorted(ledger.decisions(root).values(), key=lambda e: str(e.get("ts"))):
        target = entry.get("target") or ""
        if target.startswith(PREFIX):
            found[target[len(PREFIX):]] = (entry.get("decision"), str(entry.get("ts") or "")[:10])
    return found


def evaluate(root, data, config, path=COLLECTION):
    """すべての項目の状態を決める。集を読めなければ、項目は空で、problems に理由を書く。"""
    found = collection(path)
    if found is None:
        return {"version": None, "oldest_checked": None, "items": [], "candidates": [],
                "problems": ["ベストプラクティス集（%s）を読めない" % SHOWN_PATH]}
    facts = Facts(root, data, config)
    decided = _decided(root)
    done, items, problems = {}, [], []
    for item in found["items"]:
        check = CHECKS.get(item["id"])
        if check is None:   # 集に足した項目に、この版の kiroku の判定が無い
            status, reason, signals = UNKNOWN, "この版の kiroku には、この項目の判定が無い", []
        else:
            status, reason, signals = check(facts, done)
        done[item["id"]] = status
        row = dict(item, status=status, reason=reason, signals=signals, decision=None, candidate=False)
        decision = decided.get(item["id"])
        if decision:
            row["decision"] = {"decision": decision[0], "date": decision[1]}
        if status == UNMET:
            # 見送った項目は、集のその項目が見送りの後に更新されるまで、候補にしない。
            row["candidate"] = not (decision and decision[0] == "rejected"
                                    and not (item["checked"] and item["checked"] > decision[1]))
        items.append(row)
    missing = sorted(set(CHECKS) - {item["id"] for item in found["items"]})
    if missing:
        problems.append("集に無い項目の判定があります（%s）。集と kiroku の版がそろっていない可能性があります"
                        % "、".join(missing))
    order = sorted((row for row in items if row["candidate"]), key=lambda row: not row["signals"])
    dates = [item["checked"] for item in found["items"] if item["checked"]]
    return {"version": found["version"], "oldest_checked": min(dates) if dates else None, "items": items,
            "candidates": [PREFIX + row["id"] for row in order], "problems": problems}


def _line(row):
    text = "- `%s%s` %s：%s。%s" % (PREFIX, row["id"], row["title"], row["status"], row["reason"])
    if row["signals"]:
        text += "。兆し：%s" % "、".join(row["signals"])
    decision = row.get("decision")
    if decision and row["status"] == UNMET:
        if decision["decision"] == "rejected" and not row["candidate"]:
            text += "。%s に見送った（集のこの項目が更新されるまで、候補にしない）" % decision["date"]
        elif decision["decision"] == "rejected":
            text += "。%s に見送ったが、その後に集のこの項目が更新された" % decision["date"]
        else:
            text += "。%s に採用した（反映を待っている可能性がある）" % decision["date"]
    return text


def lines(result):
    """照らし合わせの行。集約とレポートの骨組みで同じ文面を使う。候補（兆しのあるものが先）、未確認、ほかの順。"""
    if result["version"] is None:
        return ["- %s" % problem for problem in result["problems"]]
    by_id = {row["id"]: row for row in result["items"]}
    candidates = [by_id[name[len(PREFIX):]] for name in result["candidates"]]
    groups = [("候補（満たしていない）", candidates),
              ("未確認（スクリプトでは決められない）", [r for r in result["items"] if r["status"] == UNKNOWN]),
              ("見送り中（満たしていない）", [r for r in result["items"] if r["status"] == UNMET and not r["candidate"]]),
              ("満たしている", [r for r in result["items"] if r["status"] == MET]),
              ("当てはまらない", [r for r in result["items"] if r["status"] == NOT_APPLICABLE])]
    out = []
    for label, rows in groups:
        out.append("%s：%d 件" % (label, len(rows)))
        out += [_line(row) for row in rows]
    return out + ["- %s" % problem for problem in result["problems"]]


def snapshot(result):
    """レポートの骨組みを作った時点の控え（state の report_practices）。検査は、この控えから行を作り直す。

    集を --update で更新しても、以前のレポートの照合が崩れないようにするため。
    targets は、対象の型と一歩先の案の手がかりに書ける項目：候補と、未確認の項目。未確認の項目
    （リポジトリの外の規則、会話から判断すること）は、利用者の答えで当てはまると分かったときに案にできる。
    満たしている項目、当てはまらない項目、見送り中の項目は書けない。
    """
    unknown = [PREFIX + row["id"] for row in result["items"] if row["status"] == UNKNOWN]
    return {"version": result["version"], "candidates": list(result["candidates"]),
            "targets": list(result["candidates"]) + unknown,
            "ids": [PREFIX + row["id"] for row in result["items"]], "lines": lines(result)}
