"""ハーネスの診断値（ファイルだけから決まる値）。一歩先の改善案の手がかりにする。"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from test_kiroku import aggregate
from test_review import ReviewSandbox

diagnose = aggregate.diagnose
REPO = Path(__file__).resolve().parents[1]


class DiagnoseTest(ReviewSandbox):
    def found(self):
        return diagnose.diagnose(self.root, aggregate.load_config(self.root),
                                 aggregate.prompts.hook_triggers(self.root))

    def keys(self):
        return diagnose.keys(self.found())

    def test_steering_whose_file_match_hits_nothing_is_never_loaded(self):
        self.write(".kiro/steering/style.md", '---\ninclusion: fileMatch\nfileMatchPattern: "src/**/*.py"\n---\n書き方\n')
        self.write("pkg/app.py", "x = 1\n")
        self.assertIn("D1:.kiro/steering/style.md", self.keys())
        self.write("src/deep/app.py", "x = 1\n")
        self.assertNotIn("D1:.kiro/steering/style.md", self.keys())
        # 一覧の形、波括弧、ファイル名だけのパターン（どの階層でも当たるとみなす）
        for pattern in ('["docs/*.md", "pkg/*.py"]', '"pkg/{a,app}.py"', '"*.py"'):
            self.write(".kiro/steering/style.md", "---\ninclusion: fileMatch\nfileMatchPattern: %s\n---\n" % pattern)
            self.assertNotIn("D1:.kiro/steering/style.md", self.keys(), pattern)
        self.write(".kiro/steering/always.md", "---\ninclusion: always\n---\n常に読む\n")
        self.assertFalse([k for k in self.keys() if k.startswith("D1:.kiro/steering/always")])

    def test_references_whose_target_is_missing(self):
        self.write(".gitignore", ".kiro/kiroku/data/\nreports/\ndata/\n")
        self.write("run_tests.py", "")
        self.write(".kiro/steering/testing.md", "\n".join([
            "- テストは `make test` で実行します。", "- 実行は `python3 run_tests.py` です。",
            "- 記録は `data/app.json` です。", "- 新しいテストは `tests/test_<名前>.py` に置きます。",
            "- 手順は `scripts/release.sh` を使います。", "- 版は `v1.2.0` です。",
            "```bash", "npm run build", "```"]) + "\n")
        found = self.keys()
        self.assertIn("D2:.kiro/steering/testing.md:make test", found)
        self.assertIn("D2:.kiro/steering/testing.md:scripts/release.sh", found)
        self.assertIn("D2:.kiro/steering/testing.md:npm run build", found)
        for fine in ("run_tests.py", "data/app.json", "test_<", "v1.2.0"):   # ある、無視される、型紙、版の名前
            self.assertFalse([k for k in found if fine in k], fine)
        self.write("Makefile", "lint:\n\truff check .\n")
        self.assertIn("D2:.kiro/steering/testing.md:make test", self.keys())   # target が無い
        self.write("Makefile", "test lint:\n\tpython3 run_tests.py\n")
        self.write("package.json", json.dumps({"scripts": {"build": "tsc"}}))
        found = self.keys()
        self.assertFalse([k for k in found if "make test" in k or "npm" in k])
        reasons = {r["key"]: r["reason"] for r in self.found()["broken"]}
        self.assertEqual(reasons["D2:.kiro/steering/testing.md:scripts/release.sh"], "参照先が無い")

    def test_hooks_agents_and_skills_are_checked_but_kiroku_itself_is_not(self):
        self.write(".kiro/hooks/project.json", json.dumps({"hooks": [
            {"name": "fmt", "trigger": "PostFileSave", "action": {"type": "command", "command": "python3 scripts/fmt.py"}}]}))
        self.write(".kiro/agents/reviewer.json", json.dumps({
            "resources": ["file://.kiro/steering/missing.md"],
            "permissions": {"rules": [{"capability": "shell", "match": ["python3 check.py"], "effect": "allow"}]}}))
        self.write(".kiro/skills/release/SKILL.md", "1. `references/steps.md` を読む。\n2. `scripts/tag.sh` を実行する。\n")
        self.write(".kiro/skills/release/references/steps.md", "手順\n")
        self.write(".kiro/skills/kiroku-retro/SKILL.md", "`.kiro/kiroku/data/reports/<名前>.md` と `missing/own.py`\n")
        found = self.keys()
        self.assertIn("D2:.kiro/hooks/project.json:scripts/fmt.py", found)
        self.assertIn("D2:.kiro/agents/reviewer.json:.kiro/steering/missing.md", found)
        self.assertIn("D2:.kiro/agents/reviewer.json:check.py", found)
        self.assertIn("D2:.kiro/skills/release/SKILL.md:scripts/tag.sh", found)
        self.assertNotIn("D2:.kiro/skills/release/SKILL.md:references/steps.md", found)   # skill からの相対パス
        self.assertFalse([k for k in found if "kiroku-retro" in k])
        # ユーザー共通の steering は、どのリポジトリを指すか決まらないので、参照先を確かめない。
        (self.home / "steering").mkdir(parents=True)
        (self.home / "steering" / "common.md").write_text("`scripts/none.sh` を使う。\n", encoding="utf-8")
        self.assertFalse([k for k in self.keys() if "common.md" in k])

    def test_same_sentence_in_two_files(self):
        rule = "- 金額は整数の円で持ちます。小数は使いません。"
        self.write(".kiro/steering/product.md", "# 前提\n\n%s\n- 短い\n" % rule)
        self.write("AGENTS.md", "# 決まり\n\n%s\n- 短い\n" % rule.replace("- ", "* "))
        (self.home / "steering").mkdir(parents=True)
        (self.home / "steering" / "common.md").write_text(rule + "\n", encoding="utf-8")
        rows = {r["key"]: r for r in self.found()["duplicates"]}
        self.assertEqual(set(rows), {"D3:.kiro/steering/product.md|AGENTS.md",
                                     "D3:.kiro/steering/product.md|~/.kiro/steering/common.md",
                                     "D3:AGENTS.md|~/.kiro/steering/common.md"})
        self.assertEqual(rows["D3:.kiro/steering/product.md|AGENTS.md"]["lines"], 1)   # 短い行と見出しは数えない

    def test_sensors_list_hooks_precommit_ci_and_lint(self):
        self.write(".kiro/hooks/project.json", json.dumps({"hooks": [
            {"name": "guard", "trigger": "PreToolUse", "action": {"type": "command", "command": "true"}},
            {"name": "fmt", "trigger": "PostFileSave", "action": {"type": "command", "command": "true"}}]}))
        self.write(".kiro/hooks/kiroku.json", json.dumps({"hooks": [
            {"name": "kiroku-record-tool", "trigger": "PostToolUse", "action": {"type": "command", "command": "true"}}]}))
        sense = self.found()["sensors"]
        self.assertEqual(sense["hooks"], {"実行前": ["guard"], "保存時": ["fmt"]})
        self.assertEqual((sense["precommit"], sense["ci"], sense["lint"], sense["ci_runs_tests"]), ([], [], [], None))
        self.write(".pre-commit-config.yaml", "repos: []\n")
        self.write(".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - run: pytest -q\n")
        self.write("pyproject.toml", "[tool.ruff]\nline-length = 100\n")
        sense = self.found()["sensors"]
        self.assertEqual(sense["precommit"], [".pre-commit-config.yaml"])
        self.assertEqual(sense["ci"], [".github/workflows/ci.yml"])
        self.assertTrue(sense["ci_runs_tests"])   # テストの記録設定の match（pytest）に一致する行がある
        self.assertEqual(sense["lint"], ["pyproject.toml"])

    def test_aggregate_shows_diagnostics_burden_and_puts_keys_in_the_facts(self):
        self.write(".kiro/steering/testing.md", "- `make test` で実行する。\n")
        for _ in range(2):
            self.shell("make test", 2)
        data = aggregate.aggregate(self.root)
        self.assertIn("D2:.kiro/steering/testing.md:make test", data["report_facts"]["diagnostics"])
        self.assertIn("D5", data["report_facts"]["diagnostics"])
        self.assertEqual([(r["target"], r["count"], r["sessions"], r["share"]) for r in data["burden"]],
                         [("tool:make test", 2, 1, 100)])
        text = aggregate.render(data, section="harness")
        self.assertIn("`D2:.kiro/steering/testing.md:make test`：Makefile が無い。", text)
        self.assertIn("`tool:make test`：2 回、1 セッション（記録のあるセッション 1 件のうち 100%）", text)

    def test_demo_harness_problems_are_found_without_any_incident(self):
        """デモの python-style.md の条件違い（D08）と、testing.md の古いコマンド（D13）を、記録なしで見つける。"""
        work = Path(tempfile.mkdtemp(prefix="kiroku-demo-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        demo = work / "demo"
        shutil.copytree(REPO / "examples" / "demo", demo)
        subprocess.run(["git", "init", "-q", str(demo)], check=True)
        found = diagnose.keys(diagnose.diagnose(str(demo), aggregate.load_config(str(demo)), {}))
        self.assertIn("D1:.kiro/steering/python-style.md", found)
        self.assertIn("D2:.kiro/steering/testing.md:make test", found)
        self.assertFalse([k for k in found if "kakeibo.json" in k or "run_tests.py" in k])
