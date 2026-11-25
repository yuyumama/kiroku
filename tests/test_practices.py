"""ベストプラクティス集との照らし合わせ（骨組みの版7）。

集の項目と判定がそろっていること、項目ごとの判定、見送った項目の扱い、レポートの控えと検査を確かめる。
判定はファイルと記録だけから決まる。Kiro の規則の当たり方（glob）は kiroku による近似で、実機では確かめていない。
"""
import datetime
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import test_review   # テストのクラスは名前で取り込まない（取り込むと、そのテストがここでも走る）
import test_skeleton
from test_kiroku import aggregate
from test_skeleton import LEDGER, PROPOSAL, skeleton

practices = aggregate.practices
FIELDS = ("何のためか", "当てはまる条件", "満たした証拠", "案の形", "聞き方", "出典", "確認日")


def status(data, name):
    return next(row for row in data["practices"]["items"] if row["id"] == name)


class CollectionTest(test_review.ReviewSandbox):
    """集（references/best-practices.md）と、判定（practices.py）がそろっていること。"""

    def test_every_item_has_a_check_and_the_check_has_an_item(self):
        found = practices.collection()
        self.assertIsInstance(found["version"], int)
        ids = [item["id"] for item in found["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(practices.CHECKS))

    def test_every_item_has_its_fields_a_source_and_a_date(self):
        text = Path(practices.COLLECTION).read_text(encoding="utf-8")
        blocks = re.split(r"^## (?=BP-)", text, flags=re.M)[1:]
        self.assertEqual(len(blocks), len(practices.CHECKS))
        for block in blocks:
            block = re.split(r"^## ", block, flags=re.M)[0]
            name = block.split(".", 1)[0]
            for field in FIELDS:
                self.assertRegex(block, r"(?m)^- %s：" % field, name + " " + field)
            self.assertRegex(block, r"https://", name)
            checked = re.search(r"(?m)^- 確認日：(\d{4}-\d{2}-\d{2})\s*$", block).group(1)
            datetime.date.fromisoformat(checked)
        dates = [item["checked"] for item in practices.collection()["items"]]
        self.assertNotIn(None, dates)

    def test_an_unreadable_collection_is_reported_not_hidden(self):
        found = practices.evaluate(self.root, aggregate.aggregate(self.root), aggregate.load_config(self.root),
                                   path=os.path.join(self.root, "missing.md"))
        self.assertEqual((found["items"], found["candidates"]), ([], []))
        self.assertIn("ベストプラクティス集", found["problems"][0])
        self.assertIn("を読めない", "\n".join(practices.lines(found)))


class EvaluateTest(test_review.ReviewSandbox):
    """項目ごとの判定。当てはまる条件と、満たした証拠。"""

    def evaluate(self):
        return aggregate.aggregate(self.root)

    def test_an_empty_project(self):
        data = self.evaluate()
        self.assertEqual(status(data, "BP-G1")["status"], practices.UNMET)
        self.assertEqual(status(data, "BP-S1")["status"], practices.NOT_APPLICABLE)
        self.assertEqual(status(data, "BP-S3")["status"], practices.NOT_APPLICABLE)
        self.assertEqual(status(data, "BP-E1")["status"], practices.NOT_APPLICABLE)
        self.assertEqual(status(data, "BP-P1")["status"], practices.UNKNOWN)
        self.assertEqual(status(data, "BP-P2")["status"], practices.UNKNOWN)
        self.assertEqual(status(data, "BP-R1")["status"], practices.UNKNOWN)
        self.assertEqual(data["practices"]["candidates"], ["practice:BP-G1"])
        self.assertEqual(data["practices"]["problems"], [])

    def test_tests_and_the_rule_that_names_the_command(self):
        self.write("tests/test_x.py", "def test_x():\n    pass\n")
        data = self.evaluate()
        self.assertEqual(status(data, "BP-S1")["status"], practices.MET)   # 記録設定は \bpytest\b
        self.assertEqual(status(data, "BP-G2")["status"], practices.UNMET)   # 常時読み込みの決まりが無い
        self.write(".kiro/steering/testing.md", "テストは make test で実行する。\n")
        self.assertEqual(status(self.evaluate(), "BP-G2")["status"], practices.UNMET)
        self.write(".kiro/steering/testing.md", "テストは pytest -q で実行する。\n")
        self.assertEqual(status(self.evaluate(), "BP-G2")["status"], practices.MET)
        self.write(".kiro/kiroku/config.json", json.dumps({"tests": []}))
        data = self.evaluate()
        self.assertEqual(status(data, "BP-S1")["status"], practices.UNMET)
        self.assertIn("`tests/test_x.py`", status(data, "BP-S1")["reason"])
        self.assertEqual(status(data, "BP-G2")["status"], practices.NOT_APPLICABLE)

    def test_a_package_script_or_make_target_also_means_tests(self):
        self.write(".kiro/kiroku/config.json", json.dumps({"tests": []}))
        self.write("package.json", json.dumps({"scripts": {"test": practices.NO_TEST_SCRIPT}}))
        self.assertEqual(status(self.evaluate(), "BP-S1")["status"], practices.NOT_APPLICABLE)
        self.write("Makefile", "test:\n\tpython3 -m unittest\n")
        self.assertEqual(status(self.evaluate(), "BP-S1")["status"], practices.UNMET)

    def hooks(self, *hooks):
        self.write(".kiro/hooks/project.json", json.dumps({"hooks": [
            {"name": name, "trigger": trigger, "action": {"type": "command", "command": command}}
            for name, trigger, command in hooks]}))

    def test_a_hook_that_runs_a_check_at_the_end_or_on_save(self):
        self.write("tests/test_x.py", "def test_x():\n    pass\n")
        self.hooks(("syntax", "PostFileSave", "python3 scripts/check_syntax.py"), ("guard", "PreToolUse", "pytest"))
        row = status(self.evaluate(), "BP-S2")
        self.assertEqual(row["status"], practices.UNMET)
        self.assertIn("`syntax`：`python3 scripts/check_syntax.py`", row["reason"])   # 実行前の hook は数えない
        self.hooks(("tests", "Stop", "pytest -q"))
        self.assertEqual(status(self.evaluate(), "BP-S2")["status"], practices.MET)
        self.hooks(("lint", "PostFileSave", "ruff check ."))
        self.assertEqual(status(self.evaluate(), "BP-S2")["status"], practices.MET)
        self.hooks(("kiroku-record-turn-end", "Stop", "pytest"))   # kiroku 自身の hook は数えない
        self.assertEqual(status(self.evaluate(), "BP-S2")["status"], practices.UNMET)

    def test_ci_runs_tests_only_when_there_is_a_remote(self):
        self.write("tests/test_x.py", "def test_x():\n    pass\n")
        self.write(".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - run: echo hi  # pytest は後で\n")
        row = status(self.evaluate(), "BP-S5")
        self.assertEqual(row["status"], practices.NOT_APPLICABLE)
        self.assertIn("リモートが無い", row["reason"])
        self.git("remote", "add", "origin", "https://example.invalid/repo.git")
        row = status(self.evaluate(), "BP-S5")
        self.assertEqual(row["status"], practices.UNMET)
        self.assertIn("コメントを除いた行に", row["reason"])
        self.write(".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - run: pytest -q\n")
        self.assertEqual(status(self.evaluate(), "BP-S5")["status"], practices.MET)

    def test_lint_and_precommit(self):
        self.write("app.py", "print(1)\n")
        data = self.evaluate()
        self.assertEqual(status(data, "BP-S3")["status"], practices.UNMET)
        self.assertEqual(status(data, "BP-S4")["status"], practices.NOT_APPLICABLE)   # S1 も S3 も満たさない
        self.write("ruff.toml", "line-length = 100\n")
        data = self.evaluate()
        self.assertEqual(status(data, "BP-S3")["status"], practices.MET)
        self.assertEqual(status(data, "BP-S4")["status"], practices.UNMET)
        self.write(".pre-commit-config.yaml", "repos: []\n")
        self.assertEqual(status(self.evaluate(), "BP-S4")["status"], practices.MET)

    def agent(self, *rules):
        self.write(".kiro/agents/dev.json", json.dumps({"name": "dev", "permissions": {"rules": list(rules)}}))

    def test_deny_rules_for_irreversible_operations(self):
        self.agent({"capability": "shell", "match": ["rm -rf *", "sudo *"], "effect": "deny"})
        row = status(self.evaluate(), "BP-P1")
        self.assertEqual(row["status"], practices.UNKNOWN)   # リポジトリの外の規則は見ていない
        self.assertIn("`git push --force origin main`、`git reset --hard HEAD~1` に当たらない", row["reason"])
        self.agent({"capability": "shell", "match": ["rm -rf *", "sudo *", "git push --force*", "git reset --hard*"],
                    "effect": "deny"})
        self.assertEqual(status(self.evaluate(), "BP-P1")["status"], practices.MET)
        # 「すべてを止め、許可したものだけを除く」規則も、例のコマンドを止める。
        self.agent({"capability": "shell", "match": ["*"], "exclude": ["pytest *"], "effect": "deny"})
        self.assertEqual(status(self.evaluate(), "BP-P1")["status"], practices.MET)
        # kiroku 自身のエージェント定義は数えない。
        os.remove(os.path.join(self.root, ".kiro/agents/dev.json"))
        self.write(".kiro/agents/kiroku-retro.json", json.dumps({"permissions": {"rules": [
            {"capability": "shell", "match": ["*"], "effect": "deny"}]}}))
        self.assertEqual(status(self.evaluate(), "BP-P1")["status"], practices.UNKNOWN)

    def test_reading_secret_files(self):
        self.write(".env.example", "KEY=\n")
        self.assertEqual(status(self.evaluate(), "BP-P3")["status"], practices.NOT_APPLICABLE)
        self.write(".env", "KEY=x\n")
        self.write("config/server.pem", "x\n")
        row = status(self.evaluate(), "BP-P3")
        self.assertEqual(row["status"], practices.UNKNOWN)
        self.assertIn("`.env`", row["reason"])
        self.agent({"capability": "fs_read", "match": ["**/.env", "**/*.pem"], "effect": "deny"})
        self.assertEqual(status(self.evaluate(), "BP-P3")["status"], practices.MET)
        self.agent({"capability": "fs_read", "match": ["*.pem"], "effect": "deny"})   # 階層をまたがない
        self.assertEqual(status(self.evaluate(), "BP-P3")["status"], practices.UNKNOWN)

    def test_long_always_loaded_rules(self):
        self.write(".kiro/steering/product.md", "あ" * 9000)
        row = status(self.evaluate(), "BP-G3")
        self.assertEqual(row["status"], practices.UNMET)
        self.assertEqual(row["signals"], ["常時読み込みの合計 9000 文字（目安 8000 文字）"])
        self.write(".kiro/steering/style.md", "---\ninclusion: fileMatch\nfileMatchPattern: \"*.py\"\n---\n短い\n")
        self.assertEqual(status(self.evaluate(), "BP-G3")["status"], practices.MET)

    def test_skills_are_looked_for_only_when_there_are_patterns(self):
        self.assertEqual(status(self.evaluate(), "BP-G4")["status"], practices.NOT_APPLICABLE)
        self.log("sess_a", ["テストは make test で回して"])
        self.log("sess_b", ["make test を使って。前にも言った"])
        self.note("sess_a", [test_review.finding("テストは make test で回して")])
        self.note("sess_b", [test_review.finding("make test を使って。前にも言った")])
        self.write(".kiro/skills/kiroku-retro/SKILL.md", "kiroku 自身")
        row = status(self.evaluate(), "BP-G4")
        self.assertEqual((row["status"], row["signals"]), (practices.UNMET, ["会話からの型の候補 1 件"]))
        self.write(".kiro/skills/release/SKILL.md", "---\nname: release\n---\n")
        self.assertEqual(status(self.evaluate(), "BP-G4")["status"], practices.MET)

    def test_locked_dependencies(self):
        self.write("package.json", json.dumps({"dependencies": {"left-pad": "^1.0.0"}}))
        self.assertEqual(status(self.evaluate(), "BP-E1")["status"], practices.UNMET)
        self.write("package-lock.json", "{}")
        self.assertEqual(status(self.evaluate(), "BP-E1")["status"], practices.MET)
        self.write("packages/web/package.json", json.dumps({"devDependencies": {"vite": "5"}}))
        self.assertEqual(status(self.evaluate(), "BP-E1")["status"], practices.MET)   # ロックは上の階層
        self.write("tools/requirements.txt", "requests>=2\n")
        row = status(self.evaluate(), "BP-E1")
        self.assertEqual(row["status"], practices.UNMET)
        self.assertIn("`tools/requirements.txt`", row["reason"])
        self.write("tools/requirements.txt", "# 固定\nrequests==2.32.3\n")
        self.assertEqual(status(self.evaluate(), "BP-E1")["status"], practices.MET)

    def test_specs_and_session_start_hooks(self):
        self.write(".kiro/specs/export/requirements.md", "要件\n")
        self.hooks(("state", "SessionStart", "cat PROGRESS.md"))
        data = self.evaluate()
        self.assertEqual(status(data, "BP-R1")["status"], practices.MET)
        self.assertEqual(status(data, "BP-R2")["status"], practices.MET)

    def test_candidates_with_signals_come_first(self):
        self.write("tests/test_x.py", "def test_x():\n    pass\n")
        self.write(".kiro/steering/testing.md", "テストは pytest -q で実行する。\n")
        self.write("app.py", "print(1)\n")
        data = self.evaluate()
        self.assertEqual(data["practices"]["candidates"][:3], ["practice:BP-S2", "practice:BP-S3", "practice:BP-S4"])
        # 回帰候補（兆し）のある BP-S2 と BP-S4 が、兆しの無い BP-S3 より先になる。
        self.run_tests([("t", "test_a", "passed")])
        self.run_tests([("t", "test_a", "failed")])
        data = self.evaluate()
        self.assertEqual(data["practices"]["candidates"][:3], ["practice:BP-S2", "practice:BP-S4", "practice:BP-S3"])
        self.assertEqual(status(data, "BP-S2")["signals"][0], "回帰候補 1 件")
        text = aggregate.render(data, section="practices")
        self.assertIn("## ベストプラクティス集との照らし合わせ（集の版 1、いちばん古い確認日 2026-11-20", text)
        self.assertLess(text.index("`practice:BP-S2`"), text.index("`practice:BP-S3`"))
        self.assertIn("兆し：回帰候補 1 件", text)

    def collection_with(self, name, checked):
        """確認日だけを変えた集を、一時ファイルに作る（集の更新の代わり）。"""
        text = Path(practices.COLLECTION).read_text(encoding="utf-8")
        start = text.index("## %s." % name)
        end = text.index("- 確認日：", start)
        line_end = text.index("\n", end)
        path = os.path.join(self.root, "collection.md")
        Path(path).write_text(text[:end] + "- 確認日：%s" % checked + text[line_end:], encoding="utf-8")
        return path

    def test_a_rejected_item_is_not_a_candidate_until_the_item_is_updated(self):
        self.write("app.py", "print(1)\n")
        self.assertIn("practice:BP-S3", self.evaluate()["practices"]["candidates"])
        self.decide("2026-11-25-0900#P1", "rejected", 0, target="practice:BP-S3")
        data = self.evaluate()
        self.assertNotIn("practice:BP-S3", data["practices"]["candidates"])
        row = status(data, "BP-S3")
        self.assertEqual(row["status"], practices.UNMET)   # 状態は変えない。候補から外すだけ
        self.assertIn("に見送った（集のこの項目が更新されるまで、候補にしない）", "\n".join(practices.lines(data["practices"])))
        # 経過した日数では戻らない。集のその項目の確認日が、見送りより新しくなったら、また候補にする。
        config = aggregate.load_config(self.root)
        later = (test_review.NOW + datetime.timedelta(days=30)).date().isoformat()
        found = practices.evaluate(self.root, data, config, path=self.collection_with("BP-S3", later))
        self.assertIn("practice:BP-S3", found["candidates"])
        self.assertIn("に見送ったが、その後に集のこの項目が更新された", "\n".join(practices.lines(found)))
        found = practices.evaluate(self.root, data, config, path=self.collection_with("BP-G1", later))
        self.assertNotIn("practice:BP-S3", found["candidates"])   # ほかの項目の更新では戻らない

    def test_same_files_give_the_same_result(self):
        self.write("app.py", "print(1)\n")
        first, second = self.evaluate()["practices"], self.evaluate()["practices"]
        self.assertEqual(practices.lines(first), practices.lines(second))
        self.assertEqual(list(Path(self.root, ".kiro").rglob("__pycache__")), [])


class PracticeReportTest(test_skeleton.SkeletonBase):
    """レポート（版7）：控えから節を書き、控えの候補を、対象の型と手がかりに認める。"""

    def setUp(self):
        super().setUp()
        self.write("app.py", "print(1)\n")   # lint の設定が無い → BP-S3 が候補

    def target(self, text, target, one_step=None):
        """欄を埋める。改善案 P1（一歩先の案があれば P2）の対象の型を target にする。"""
        proposal = PROPOSAL.replace("`tool:make test`", "`%s`" % target)
        if one_step:
            marker = "## 一歩先の改善案\n\n"
            start = text.index(marker) + len(marker)
            text = text[:start] + one_step + text[text.index("\n", start):]
            proposal = proposal.replace("### P1.", "### P2.")
        return skeleton.SLOT.sub("なし", self.propose(text, proposal).replace(skeleton.slot("確認済み／未確認"), "確認済み"))

    def test_the_section_is_written_from_the_kept_snapshot(self):
        path, text = self.new_report()
        self.assertTrue(text.startswith(skeleton.MARK))
        self.assertGreaterEqual(skeleton.VERSION, 7)
        self.assertIn(skeleton.PRACTICES_HEADING, text)
        self.assertLess(text.index("## ハーネスの診断"), text.index(skeleton.PRACTICES_HEADING))
        self.assertLess(text.index(skeleton.PRACTICES_HEADING), text.index("## 記録の不足、確認事項"))
        self.assertIn("- `practice:BP-S3` lint や型検査の設定がある：満たしていない。", text)
        state = json.loads((self.data / "state.json").read_text(encoding="utf-8"))
        kept = state["report_practices"][path.stem]
        self.assertIn("practice:BP-S3", kept["candidates"])
        self.assertEqual(kept["version"], 1)

    def test_a_candidate_can_be_the_target_and_the_clue(self):
        path, text = self.new_report()
        done = self.check(path, self.target(text, "practice:BP-S3"))
        self.assertEqual(done.returncode, 0, done.stdout)
        done = self.check(path, self.target(text, "practice:BP-S1"))   # 満たしている項目
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("ベストプラクティス集の候補にない対象の型：practice:BP-S1", done.stdout)
        done = self.check(path, self.target(text, "practice:BP-P1"))   # 未確認の項目（答えで当てはまると分かったとき）
        self.assertEqual(done.returncode, 0, done.stdout)
        done = self.check(path, self.target(text, "practice:BP-E1"))   # 当てはまらない項目
        self.assertEqual(done.returncode, 1, done.stdout)
        one_step = test_skeleton.ONE_STEP.replace("`D2:.kiro/steering/build.md:make build`、`tool:make test`",
                                                  "`practice:BP-S3`\n- 対象の型：`practice:BP-S3`")
        body = self.target(text, "tool:make test", one_step)
        done = self.check(path, body)
        self.assertEqual(done.returncode, 0, done.stdout)
        done = self.check(path, body.replace("`practice:BP-S3`", "`practice:BP-S1`", 1))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("集約に無い手がかり：practice:BP-S1", done.stdout)

    def test_a_later_change_does_not_break_the_report(self):
        """骨組みの後に照らし合わせが変わっても（集の更新、別の振り返りの見送りなど）、控えで照合する。"""
        path, text = self.new_report()
        filled = self.target(text, "practice:BP-S3")
        self.decide("2026-11-01-0900#P9", "rejected", 0, target="practice:BP-S3")   # 今は候補ではない
        self.assertNotIn("practice:BP-S3", aggregate.aggregate(self.root)["practices"]["candidates"])
        done = self.check(path, filled)
        self.assertEqual(done.returncode, 0, done.stdout)
        # 控えの行を書き換えると、不一致になる。
        done = self.check(path, filled.replace("lint や型検査の設定がある：満たしていない", "lint や型検査の設定がある：満たしている"))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("スクリプトが書いた行が、消えているか、書き換えられています", done.stdout)

    def test_without_the_snapshot_the_current_comparison_is_used_and_said_so(self):
        path, text = self.new_report()
        state_path = self.data / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("report_practices")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        done = self.check(path, self.target(text, "practice:BP-S3"))
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("控えが無いので、スクリプトが書いた行を照合していません", done.stdout)

    def test_the_collection_is_not_quoted(self):
        source = Path(practices.COLLECTION).read_text(encoding="utf-8")
        self.write(practices.SHOWN_PATH, source)
        path, text = self.new_report()
        quoted = PROPOSAL.replace("`.kiro/steering/testing.md`「テストは make test で実行する。」",
                                  "`%s`「毎回読ませる決まりがある」" % practices.SHOWN_PATH)
        body = skeleton.SLOT.sub("なし", self.propose(text, quoted).replace(skeleton.slot("確認済み／未確認"), "確認済み"))
        done = self.check(path, body)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("ベストプラクティス集の文面は引用しません", done.stdout)

    def test_a_practice_target_can_be_adopted_and_followed_by_its_state(self):
        path, text = self.new_report()
        done = self.check(path, self.target(text, "practice:BP-S3"))
        self.assertEqual(done.returncode, 0, done.stdout)
        adopted = self.cli("adopt", "P1", script=LEDGER)
        self.assertEqual(adopted.returncode, 0, adopted.stdout)
        self.assertIn("対象の型：practice:BP-S3", adopted.stdout)
        review = aggregate.render(aggregate.aggregate(self.root), section="review")
        self.assertIn("ベストプラクティス集との照らし合わせの今の状態：満たしていない", review)
        self.write("ruff.toml", "line-length = 100\n")
        review = aggregate.render(aggregate.aggregate(self.root), section="review")
        self.assertIn("ベストプラクティス集との照らし合わせの今の状態：満たしている", review)

    def test_sixth_format_report_from_the_previous_version_still_passes(self):
        """版6（ベストプラクティス集を足す前の main）で作り、検査を通ったレポートが、更新しただけで不一致にならない。"""
        work = Path(tempfile.mkdtemp(prefix="kiroku-old-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        scripts = test_skeleton.old_scripts(work, SIXTH_FORMAT_VERSION)
        if scripts is None:
            self.skipTest("以前の版（%s）が git の履歴にありません" % SIXTH_FORMAT_VERSION)
        old = self.cli("--since", "7d", "--new-report", script=str(scripts / "aggregate.py"))
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        path = sorted((self.data / "reports").glob("*.md"))[-1]
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- kiroku-report:6 -->"))
        self.assertNotIn(skeleton.PRACTICES_HEADING, text)
        filled = skeleton.SLOT.sub("なし", self.propose(text, PROPOSAL))
        path.write_text(filled, encoding="utf-8")
        before = self.cli("--check-report", str(path), script=str(scripts / "aggregate.py"))
        self.assertEqual(before.returncode, 0, before.stdout)
        after = self.cli("--check-report", str(path))
        self.assertEqual(after.returncode, 0, after.stdout)
        self.assertNotIn("控えが無い", after.stdout)   # 版6には節が無いので、控えも要らない
        self.assertFalse(skeleton.is_draft(filled))


SIXTH_FORMAT_VERSION = "e727611"   # 版6の骨組みを作っていた main（PR #26 のマージ。ベストプラクティス集を足す前）
