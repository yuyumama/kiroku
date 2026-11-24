"""承認の記録と、許可の規則の候補（骨組みの版8）。

ログの形は Kiro CLI 3.3.0 で実測したもの（docs/retro/verification.md）を模している：
承認画面に答えると、tool_call と同じ toolCallId の interaction_resolved が残り、承認は selectedOption=accept、
拒否は selectedOption=reject、取り消しは outcome=cancelled（selectedOption のキーが無い）。
Always allow を選んだときの値は確かめていないので、知らない値として扱う（承認に数えない）。
規則の当たり方は Kiro の資料からの近似で、実機では確かめていない。
"""
import datetime
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

import test_review   # テストのクラスは名前で取り込まない（取り込むと、そのテストがここでも走る）
import test_skeleton
from test_kiroku import aggregate
from test_skeleton import LEDGER, PROPOSAL, skeleton

approvals = aggregate.approvals
practices = aggregate.practices
NOW = test_review.NOW
SEVENTH_FORMAT_VERSION = "cdf4c0b"   # 版7の骨組みを作っていた main（PR #27 のマージ。承認の記録を足す前）
ANSWERS = {"accept": {"outcome": "selected", "selectedOption": "accept"},
           "reject": {"outcome": "selected", "selectedOption": "reject"},
           "cancel": {"outcome": "cancelled"},
           "always": {"outcome": "selected", "selectedOption": "accept_always"}}


def log(home, root, session, items, days_ago=0, hash_name="0123456789abcdef", prompt="作業して"):
    """承認画面に答えたログを作る。items は (コマンド, 答え) の一覧。答えは ANSWERS の名前か、発話（"user:…"）。"""
    directory = Path(home) / "sessions" / hash_name / session
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "session.json").write_text(json.dumps({"id": session, "workspacePaths": [root]}), encoding="utf-8")
    start = NOW - datetime.timedelta(days=days_ago, minutes=30)
    rows = [{"type": "user", "content": prompt}]
    for number, (command, answer) in enumerate(items):
        if command.startswith("user:"):
            rows.append({"type": "user", "content": command[5:]})
            continue
        call = "c%d" % number
        rows.append(dict({"type": "interaction_resolved", "toolCallId": call, "executionId": "e",
                          "consentRound": 1}, **ANSWERS[answer]))
        rows.append({"type": "tool_call", "toolCallId": call, "toolName": "execute_bash",
                     "status": "completed" if answer in ("accept", "always") else "denied",
                     "args": {"command": command}, "executionId": "e", "turnId": "t1",
                     "_meta": {"kiro": {"agentMode": "dev"}}})
        rows.append({"type": "tool_result", "toolCallId": call, "content": "Output:\nok\n\nExit Code: 0",
                     "success": answer in ("accept", "always"), "executionId": "e", "turnId": "t1"})
    with open(directory / "messages.jsonl", "w", encoding="utf-8") as handle:
        for index, payload in enumerate(rows):
            stamp = (start + datetime.timedelta(seconds=index)).isoformat()
            handle.write(json.dumps({"id": str(index), "timestamp": stamp, "payload": payload},
                                    ensure_ascii=False) + "\n")


def group(data, heading):
    return next((g for g in data["approvals"]["groups"] if g["heading"] == heading), None)


class ApprovalTest(test_review.ReviewSandbox):
    def approve(self, session, items, **kwargs):
        log(self.home, self.root, session, items, **kwargs)

    def test_answers_are_paired_by_call_id_and_repeated_ones_become_candidates(self):
        self.approve("sess_a", [("git diff", "accept"), ("git status", "accept")])
        self.approve("sess_b", [("git diff HEAD~1", "accept"), ("cd sub && git diff | tail -3", "accept")])
        data = aggregate.aggregate(self.root)
        found = group(data, "git diff")
        self.assertEqual((found["approved"], found["sessions"], found["candidate"], found["class"]),
                         (3, 2, True, "read"))
        self.assertEqual(found["rule"]["patterns"], ["git diff", "git diff *"])
        self.assertEqual(found["rule"]["tier"], "two")
        self.assertIn("approval:git diff", data["approvals"]["candidates"])
        self.assertFalse(group(data, "git status")["candidate"])   # 1 セッションだけ
        self.assertEqual(data["report_facts"]["targets"]["approval:git diff"], {"count": 3, "sessions": 2})
        text = aggregate.render(data, section="approvals")
        self.assertIn("### 許可の候補（1 件）", text)
        self.assertIn("- `approval:git diff`：承認 3 回（2 セッション）、拒否 0 回、取り消し 0 回", text)
        self.assertIn("規則の案：`git diff`、`git diff *`（2語＋`*`）", text)
        self.assertIn("当たらない例：`git push --force origin main`", text)

    def test_many_approvals_in_one_session_are_not_a_candidate(self):
        """D34：1つのセッションで同じコマンドを10回承認しても、その場限りの作業として候補にしない。"""
        self.approve("sess_a", [("pytest -q", "accept")] * 10)
        found = group(aggregate.aggregate(self.root), "pytest")
        self.assertEqual((found["approved"], found["sessions"], found["candidate"]), (10, 1, False))
        self.assertIn("承認したセッションが 1 件（候補は 2 件から）", found["why"])

    def test_pushes_and_mixed_answers_are_not_candidates(self):
        """D35：git push は毎回承認していても外に出るので候補にしない。git commit は拒否したことがあるので外す。"""
        self.approve("sess_a", [("git push origin main", "accept"), ("git commit -m a", "accept")])
        self.approve("sess_b", [("git push origin main", "accept"), ("git commit -m b", "reject")])
        self.approve("sess_c", [("git commit -m c", "accept")])
        data = aggregate.aggregate(self.root)
        push, commit = group(data, "git push"), group(data, "git commit")
        self.assertEqual((push["candidate"], push["class"]), (False, "external"))
        self.assertIn("危険の区分のため", push["why"])
        self.assertEqual((commit["candidate"], commit["rejected"]), (False, 1))
        self.assertIn("拒否したことがある", commit["why"])
        self.assertEqual(data["approvals"]["candidates"], [])

    def test_irreversible_operations_become_deny_candidates(self):
        self.approve("sess_a", [("rm -rf build", "accept"), ("git reset --hard HEAD~1", "accept")])
        data = aggregate.aggregate(self.root)
        self.assertTrue(group(data, "rm -rf")["deny"])
        self.assertTrue(group(data, "git reset")["deny"])
        self.assertIn("### 取り返しがつかない操作の承認（deny の案の候補。2 件）", aggregate.render(data, section="approvals"))

    def test_unknown_answers_are_counted_apart_and_not_as_approvals(self):
        self.approve("sess_a", [("git log", "always"), ("git log", "cancel")])
        self.approve("sess_b", [("git log", "always")])
        data = aggregate.aggregate(self.root)
        found = group(data, "git log")
        self.assertEqual((found["approved"], found["unknown"], found["cancelled"], found["candidate"]), (0, 2, 1, False))
        self.assertEqual(data["approvals"]["unknown_values"], [("outcome=selected、selectedOption=accept_always", 2)])
        self.assertIn("### 知らない答えの値（承認には数えていない）", aggregate.render(data, section="approvals"))

    def test_the_retro_and_kiroku_commands_are_not_counted(self):
        self.approve("sess_a", [("git diff", "accept"), ("user:/kiroku-retro", ""), ("git log", "accept")])
        self.approve("sess_b", [("python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since last", "accept")])
        data = aggregate.aggregate(self.root)
        self.assertIsNotNone(group(data, "git diff"))
        self.assertIsNone(group(data, "git log"))   # 振り返りを始めた後
        self.assertEqual([g["heading"] for g in data["approvals"]["groups"]], ["git diff"])

    def test_counts_are_for_the_period_but_a_rejection_counts_whenever_it_was(self):
        self.approve("sess_old", [("git diff", "accept")], days_ago=20)
        self.approve("sess_new", [("git diff", "accept")], days_ago=1)
        since = NOW - datetime.timedelta(days=7)
        found = group(aggregate.aggregate(self.root, since), "git diff")
        self.assertEqual((found["sessions"], found["candidate"]), (1, False))
        self.assertTrue(group(aggregate.aggregate(self.root), "git diff")["candidate"])
        self.approve("sess_older", [("git diff", "reject")], days_ago=30)
        self.assertFalse(group(aggregate.aggregate(self.root), "git diff")["candidate"])

    def test_the_minimum_sessions_can_be_set(self):
        self.approve("sess_a", [("git diff", "accept")])
        self.approve("sess_b", [("git diff", "accept")])
        self.write(".kiro/kiroku/config.json", json.dumps({"tests": [], "approval_min_sessions": 3}))
        self.assertFalse(group(aggregate.aggregate(self.root), "git diff")["candidate"])
        self.write(".kiro/kiroku/config.json", json.dumps({"tests": [], "approval_min_sessions": 0}))   # 不正 → 既定
        self.assertTrue(group(aggregate.aggregate(self.root), "git diff")["candidate"])

    def test_the_workspace_rules_file_is_found_from_the_log_folder(self):
        self.approve("sess_a", [("git diff", "accept")], hash_name="abc123")
        self.approve("sess_b", [("git diff", "accept")], hash_name="abc123")
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["approvals"]["rules_file"]["path"], "~/.kiro/workspace-roots/abc123/permissions.yaml")
        self.assertFalse(data["approvals"]["rules_file"]["exists"])
        self.assertFalse(group(data, "git diff")["rules_changed"])
        rules = Path(self.home) / "workspace-roots" / "abc123" / "permissions.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text("rules: []\n", encoding="utf-8")
        data = aggregate.aggregate(self.root)
        self.assertTrue(group(data, "git diff")["rules_changed"])
        self.assertIn("もう規則にしてあるかもしれない", aggregate.render(data, section="approvals"))

    def test_best_practice_p2_follows_the_candidates(self):
        self.assertEqual(test_practices_status(aggregate.aggregate(self.root), "BP-P2"), practices.UNKNOWN)
        self.approve("sess_a", [("git diff", "accept")])
        self.assertEqual(test_practices_status(aggregate.aggregate(self.root), "BP-P2"), practices.MET)
        self.approve("sess_b", [("git diff", "accept")])
        data = aggregate.aggregate(self.root)
        self.assertEqual(test_practices_status(data, "BP-P2"), practices.UNMET)
        self.assertIn("practice:BP-P2", data["practices"]["candidates"])

    def test_installer_says_when_the_setting_is_wrong(self):
        spec = importlib.util.spec_from_file_location(
            "kiroku_installer", os.path.join(os.path.dirname(__file__), "..", "install.py"))
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        self.write(".kiro/kiroku/config.json", json.dumps({"approval_min_sessions": 0}))
        notes = "\n".join(installer.config_notes(self.root))
        self.assertIn("approval_min_sessions = 0 は使えません（1 以上の整数。", notes)
        self.assertIn("既定値 2 で動きます", notes)


def test_practices_status(data, name):
    return next(row for row in data["practices"]["items"] if row["id"] == name)["status"]


class RuleTest(test_review.ReviewSandbox):
    """規則の段と、危険の区分。コマンドの文面から決める。"""

    def test_the_narrowest_tier_that_covers_what_was_seen(self):
        self.assertEqual(approvals.rule_for(["git status"])[0]["patterns"], ["git status"])
        self.assertEqual(approvals.rule_for(["cat a.md", "cat b.md"])[0]["patterns"], ["cat *"])
        self.assertEqual(approvals.rule_for(["pytest -q", "pytest -k x"])[0]["patterns"], ["pytest *"])
        rule, why = approvals.rule_for(["python3 -m pytest -q", "python3 -m pytest -k x"])
        self.assertIsNone(rule)
        self.assertIn("危険な変形（`python3 -m http.server`）", why)
        rule, why = approvals.rule_for(["sudo ls", "sudo cat x"])
        self.assertIsNone(rule)
        self.assertIn("完全一致でしか", why)
        self.assertIsNone(approvals.rule_for(["git status", "ls"])[0])   # 先頭の語がそろわない
        self.assertIsNone(approvals.rule_for(["git diff", "git status"])[0])   # `git *` は危険な変形に当たる

    def test_rules_never_match_the_dangerous_variants(self):
        for keys in (["git diff", "git diff HEAD"], ["cat a"], ["pytest -q", "pytest"], ["npm run build", "npm run lint"]):
            rule, why = approvals.rule_for(keys)
            self.assertIsNotNone(rule, why)
            for sample in approvals.DANGEROUS:
                self.assertFalse(any(approvals._glob(p, sample) for p in rule["patterns"]), (keys, sample))

    def test_classes_come_from_the_command_text(self):
        config = {"tests": [{"match": r"run_tests\.py"}]}
        cases = {"git status": "read", "cd sub && pytest -q | tail -3": "check", "python3 run_tests.py": "check",
                 "git commit -m x": "write", "git -C . push": "external", "npm install x": "external",
                 "curl https://example.invalid": "external", "python3 -c 'x'": "code", "npx tool": "code",
                 "docker exec c ls": "code", "find . -exec rm {} ;": "code", "rm -rf build": "irreversible",
                 "git push --force": "irreversible", "git reset --hard": "irreversible", "sudo ls": "irreversible",
                 "git clean -fd": "irreversible", "find . -delete": "irreversible", "kill -9 1": "irreversible",
                 "rm a.txt": "write", "python3 scripts/x.py": "other", "env A=1 git status": "read",
                 "ls && rm -rf build": "irreversible"}
        for command, kind in cases.items():
            self.assertEqual(approvals.classify(command, config), kind, command)

    def test_headings_keep_one_word_tools_together(self):
        self.assertEqual(approvals.heading("cat README.md"), "cat")
        self.assertEqual(approvals.heading("cd sub && pytest tests/test_a.py | tail"), "pytest")
        self.assertEqual(approvals.heading("git diff HEAD~1"), "git diff")
        self.assertEqual(approvals.heading("python3 run_tests.py -v"), "python3 run_tests.py")


class ApprovalReportTest(test_skeleton.SkeletonBase):
    """レポート（版8）：承認の候補は対象の型になり、採用した後の承認を数えて追う。"""

    def setUp(self):
        super().setUp()
        log(self.home, self.root, "sess_a", [("git diff", "accept")], days_ago=1)
        log(self.home, self.root, "sess_b", [("git diff HEAD~1", "accept")], days_ago=1)

    def test_an_approval_candidate_is_a_target_and_is_followed(self):
        path, text = self.new_report()
        self.assertTrue(text.startswith("<!-- kiroku-report:8 -->"))
        self.assertIn('"approval:git diff": {"count": 2, "sessions": 2}', text)
        self.assertIn("`approval:git diff`", text.split("## ハーネスの診断", 1)[1])   # 型ごとの負担（D6）
        proposal = PROPOSAL.replace("`tool:make test`", "`approval:git diff`")
        body = skeleton.SLOT.sub("なし", self.propose(text, proposal).replace(skeleton.slot("確認済み／未確認"), "確認済み"))
        done = self.check(path, body)
        self.assertEqual(done.returncode, 0, done.stdout)
        done = self.check(path, body.replace("`approval:git diff`", "`approval:git log`"))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("集約にない対象の型：approval:git log", done.stdout)
        self.assertEqual(self.check(path, body).returncode, 0)   # 書き終えた形に戻す
        adopted = self.cli("adopt", "P1", script=LEDGER)
        self.assertEqual(adopted.returncode, 0, adopted.stdout)
        tracked = aggregate.aggregate(self.root)["adopted"][0]
        self.assertEqual((tracked["target"], tracked["before"]), ("approval:git diff", 2))
        self.assertEqual(tracked["observed"]["unit"], "ログを読めたセッション（発話あり）")

    def test_seventh_format_report_from_the_previous_version_still_passes(self):
        """版7（承認の記録を足す前の main）で作り、検査を通ったレポートが、承認のログがあっても通る。"""
        work = Path(tempfile.mkdtemp(prefix="kiroku-old-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        scripts = test_skeleton.old_scripts(work, SEVENTH_FORMAT_VERSION)
        if scripts is None:
            self.skipTest("以前の版（%s）が git の履歴にありません" % SEVENTH_FORMAT_VERSION)
        old = self.cli("--since", "7d", "--new-report", script=str(scripts / "aggregate.py"))
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        path = sorted((self.data / "reports").glob("*.md"))[-1]
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- kiroku-report:7 -->"))
        self.assertNotIn("approval:", text)
        filled = skeleton.SLOT.sub("なし", self.propose(text, PROPOSAL))
        path.write_text(filled, encoding="utf-8")
        before = self.cli("--check-report", str(path), script=str(scripts / "aggregate.py"))
        self.assertEqual(before.returncode, 0, before.stdout)
        after = self.cli("--check-report", str(path))
        self.assertEqual(after.returncode, 0, after.stdout)
