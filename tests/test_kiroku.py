"""kiroku のテスト。一時的な git リポジトリを作り、フックの入力を record.py に流して確かめる。

実行: python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(HERE, "..", "kit", ".kiro", "kiroku")
RECORD = os.path.join(KIT, "record.py")
sys.path.insert(0, KIT)
import aggregate  # noqa: E402
import excerpt  # noqa: E402
import record  # noqa: E402

REPORT = "reports/junit.xml"


def junit(cases):
    """cases は (classname, name, status) の一覧。"""
    body = []
    for classname, name, status in cases:
        inner = {"passed": "", "failed": "<failure message='boom'/>",
                 "error": "<error message='err'/>", "skipped": "<skipped/>"}[status]
        body.append('<testcase classname="%s" name="%s">%s</testcase>' % (classname, name, inner))
    return '<?xml version="1.0"?><testsuites><testsuite name="s">%s</testsuite></testsuites>' % "".join(body)


class Sandbox(unittest.TestCase):
    """一時リポジトリと、フック入力を流すための道具。"""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="kiroku-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        self.write(".gitignore", ".kiro/retro/\nreports/\n")
        self.write(".kiro/kiroku/config.json", json.dumps(
            {"tests": [{"match": r"\bpytest\b", "report": REPORT, "format": "junit"}]}))
        self.commit("init")
        self.session = "sess_test"

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root] + list(args), check=True,
                              capture_output=True, text=True).stdout

    def write(self, path, text):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def hook(self, payload):
        """record.py をフックと同じように起動する。標準出力と終了コードを返す。"""
        payload = dict({"cwd": self.root, "session_id": self.session}, **payload)
        done = subprocess.run([sys.executable, RECORD], input=json.dumps(payload),
                              capture_output=True, text=True, cwd=self.root)
        return done

    def shell(self, command, exit_code, output="out"):
        return self.hook({
            "hook_event_name": "PostToolUse", "tool_name": "execute_bash",
            "tool_input": {"command": command},
            "tool_response": "Output:\n%s\n\n\nExit Code: %d" % (output, exit_code)})

    def run_tests(self, cases):
        """テストを実行したことにする。レポートを書き、シェル実行のフックを流す。"""
        time.sleep(0.02)  # レポートの更新時刻が前回と変わるようにする
        self.write(REPORT, junit(cases))
        failed = any(status in ("failed", "error") for _, _, status in cases)
        self.shell("pytest -q --junitxml=%s" % REPORT, 1 if failed else 0)

    def saved(self, path):
        self.hook({"hook_event_name": "PostFileSave",
                   "file_path": os.path.join(self.root, path)})

    def events(self):
        path = os.path.join(self.root, ".kiro", "retro", "events.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def classes(self):
        """テストIDごとの分類の一覧（発生順）。"""
        data = aggregate.aggregate(self.root, by="all")
        return {row["test_id"]: row["class"] for row in data["test_failures"]}, data


class RecordTest(Sandbox):
    def test_hook_is_silent_and_never_fails(self):
        done = self.shell("ls /no/such/dir", 2, "ls: cannot access")
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        done = subprocess.run([sys.executable, RECORD], input="not json",
                              capture_output=True, text=True, cwd=self.root)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_tool_failure_is_recorded_with_exit_code_and_tail(self):
        self.shell("echo hello", 0, "hello")
        self.shell("ls /no/such/dir", 2, "ls: cannot access '/no/such/dir'")
        events = self.events()
        self.assertEqual(len(events), 1)  # 成功したコマンドは記録しない
        event = events[0]
        self.assertEqual(event["type"], "tool_failure")
        self.assertEqual(event["exit_code"], 2)
        self.assertEqual(event["command"], "ls /no/such/dir")
        self.assertIn("cannot access", event["output_tail"])
        self.assertEqual(event["session_id"], self.session)
        self.assertTrue(event["ts"].endswith("Z"))

    def test_cancelled_tool_is_not_a_failure(self):
        self.shell("rm -rf build", -1, "The tool call was cancelled before it was approved.")
        self.assertEqual(self.events()[0]["type"], "tool_cancelled")

    def test_policy_denial_is_told_apart_from_human_cancel(self):
        self.shell("date", -1, "Denied by a permission rule. The user did not reject this.")
        self.assertEqual(self.events()[0]["type"], "tool_denied")

    def test_kiroku_own_commands_are_not_recorded(self):
        self.shell("python3 .kiro/kiroku/aggregate.py --since 14d", 1, "boom")
        self.assertEqual(self.events(), [])

    def test_secrets_are_masked(self):
        self.shell("curl -H 'Authorization: Bearer abcdef1234567890' https://u:hunter2pw@example.com",
                   7, "API_KEY=supersecretvalue\ntoken: ghp_abcdefghijklmnopqrstuvwxyz0123")
        raw = json.dumps(self.events()[0])
        for secret in ("abcdef1234567890", "hunter2pw", "supersecretvalue", "ghp_abcdefghij"):
            self.assertNotIn(secret, raw)

    def test_test_run_records_results_commit_and_dirty_files(self):
        self.write("src/app.py", "x = 1\n")
        self.run_tests([("tests.test_a", "test_ok", "passed"), ("tests.test_a", "test_ng", "failed")])
        event = self.events()[0]
        self.assertEqual(event["type"], "test_run")
        self.assertEqual(event["results"]["passed"], ["tests.test_a::test_ok"])
        self.assertEqual(event["results"]["failed"], ["tests.test_a::test_ng"])
        self.assertEqual(event["head"], self.git("rev-parse", "HEAD").strip())
        self.assertIn("src/app.py", event["dirty"])
        self.assertNotIn(REPORT, event["dirty"])  # .gitignore 済みのものは入らない

    def test_stale_report_is_not_read_twice(self):
        self.run_tests([("tests.test_a", "test_ok", "passed")])
        self.shell("pytest -q", 0)  # レポートは更新されていない
        second = self.events()[1]
        self.assertIsNone(second["results"])
        self.assertEqual(second["note"], "report_stale")

    def test_written_file_is_recorded_with_hash(self):
        self.write("src/app.py", "x = 1\n")
        self.saved("src/app.py")
        self.hook({"hook_event_name": "PostToolUse", "tool_name": "str_replace",
                   "tool_input": {"path": os.path.join(self.root, "src/app.py")},
                   "tool_response": "ok"})
        self.saved(".kiro/retro/reports/x.md")  # 記録先そのものは対象外
        events = self.events()
        row = aggregate.aggregate(self.root, by="all")["periods"]["全期間"]
        self.assertEqual(row["writes"], 1)  # 同じ書き込みの2件は、要約では1回と数える
        self.assertEqual([e["type"] for e in events], ["write", "write"])
        self.assertEqual(events[0]["path"], "src/app.py")
        self.assertEqual(events[0]["hash"], self.git("hash-object", "src/app.py").strip())


class ClassifyTest(Sandbox):
    A = ("tests.test_a", "test_a")
    B = ("tests.test_b", "test_b")

    def baseline(self):
        """src と test_a があり、全テストが通っている状態を作る。"""
        self.write("src/app.py", "def f():\n    return 1\n")
        self.write("tests/test_a.py", "def test_a():\n    assert f() == 1\n")
        self.commit("baseline")
        self.run_tests([self.A + ("passed",)])

    def test_new_test_failure_is_normal_red(self):
        self.baseline()
        self.write("tests/test_b.py", "def test_b():\n    assert g() == 2\n")
        self.run_tests([self.A + ("passed",), self.B + ("failed",)])
        classes, _ = self.classes()
        self.assertEqual(classes, {"tests.test_b::test_b": aggregate.RED})

    def test_previously_passing_test_failure_is_regression(self):
        self.baseline()
        self.write("src/app.py", "def f():\n    return 2\n")  # テストは触らず、実装だけ壊す
        self.run_tests([self.A + ("failed",)])
        classes, _ = self.classes()
        self.assertEqual(classes, {"tests.test_a::test_a": aggregate.REGRESSION})

    def test_changed_test_failure_is_normal_red(self):
        self.baseline()
        self.write("tests/test_a.py", "def test_a():\n    assert f() == 99\n")  # テストを書き換えた
        self.run_tests([self.A + ("failed",)])
        classes, _ = self.classes()
        self.assertEqual(classes, {"tests.test_a::test_a": aggregate.RED})

    def test_red_and_regression_in_the_same_run_are_told_apart(self):
        self.baseline()
        self.write("tests/test_b.py", "def test_b():\n    assert g() == 2\n")
        self.write("src/app.py", "def f():\n    return 2\n")
        self.run_tests([self.A + ("failed",), self.B + ("failed",)])
        classes, data = self.classes()
        self.assertEqual(classes, {"tests.test_a::test_a": aggregate.REGRESSION,
                                   "tests.test_b::test_b": aggregate.RED})
        row = data["periods"]["全期間"]
        self.assertEqual((row[aggregate.RED], row[aggregate.REGRESSION]), (1, 1))

    def test_regression_is_detected_across_commits(self):
        self.baseline()
        self.write("tests/test_b.py", "def test_b():\n    assert True\n")
        self.run_tests([self.A + ("passed",), self.B + ("failed",)])   # 正常な Red
        self.run_tests([self.A + ("passed",), self.B + ("passed",)])   # Green
        self.commit("add b")
        self.write("src/app.py", "def f():\n    return 3\n")
        self.commit("break a")                                          # コミット後に壊れたことが分かる
        self.run_tests([self.A + ("failed",), self.B + ("passed",)])
        _, data = self.classes()
        got = {(r["test_id"], r["class"]) for r in data["test_failures"]}
        self.assertEqual(got, {("tests.test_b::test_b", aggregate.RED),
                               ("tests.test_a::test_a", aggregate.REGRESSION)})

    def test_failure_without_green_baseline_is_other(self):
        self.write("tests/test_a.py", "def test_a():\n    assert False\n")
        self.run_tests([self.A + ("failed",)])
        classes, _ = self.classes()
        self.assertEqual(classes, {"tests.test_a::test_a": aggregate.OTHER})

    def test_file_attribute_in_report_is_used(self):
        """classname からファイルを推測できなくても、レポートの file 属性があれば使う。"""
        self.write("spec/login.spec.ts", "it('logs in', () => {})\n")
        self.commit("spec")
        xml = ('<testsuites><testsuite name="s" file="spec/login.spec.ts">'
               '<testcase classname="Login page" name="logs in">%s</testcase>'
               '</testsuite></testsuites>')
        self.write(REPORT, xml % "")
        self.shell("pytest", 0)
        self.write("spec/login.spec.ts", "it('logs in', () => { expect(1).toBe(2) })\n")
        time.sleep(0.02)
        self.write(REPORT, xml % "<failure/>")
        self.shell("pytest", 1)
        classes, _ = self.classes()
        self.assertEqual(classes, {"Login page::logs in": aggregate.RED})


class FixCandidateTest(Sandbox):
    def agent_writes(self, path, text):
        self.write(path, text)
        self.saved(path)

    def candidates(self):
        data = aggregate.aggregate(self.root, by="all")
        return ([r["path"] for r in data["fix_candidates"]],
                [r["path"] for r in data["fix_excluded"]])

    def test_untouched_file_is_not_a_candidate(self):
        self.agent_writes("src/app.py", "x = 1\n")
        self.commit("agent")
        self.assertEqual(self.candidates(), ([], []))

    def test_human_edit_is_a_candidate(self):
        self.agent_writes("src/app.py", "x = 1\n")
        self.write("src/app.py", "x = 2  # 人が直した\n")
        self.assertEqual(self.candidates(), (["src/app.py"], []))

    def test_human_edit_after_commit_is_a_candidate(self):
        self.agent_writes("src/app.py", "x = 1\n")
        self.commit("agent")
        self.write("src/app.py", "x = 2\n")
        self.commit("human fix")
        self.assertEqual(self.candidates(), (["src/app.py"], []))

    def test_change_from_merge_is_excluded(self):
        self.agent_writes("src/app.py", "x = 1\n")
        self.agent_writes("src/other.py", "y = 1\n")
        self.commit("agent")
        self.git("checkout", "-q", "-b", "teammate")
        self.write("src/app.py", "x = 1\nz = 3\n")
        self.commit("teammate change")
        self.git("checkout", "-q", "main")
        self.write("README.md", "doc\n")
        self.commit("local work")
        self.git("merge", "-q", "--no-edit", "teammate")
        self.write("src/other.py", "y = 2\n")  # こちらは人の手直し
        self.assertEqual(self.candidates(), (["src/other.py"], ["src/app.py"]))

    def test_change_from_rebase_is_excluded(self):
        self.write("base.txt", "base\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "feature")
        self.agent_writes("src/app.py", "x = 1\n")
        self.commit("agent on feature")
        self.git("checkout", "-q", "main")
        self.write("src/app.py", "x = 0\n")
        self.commit("upstream")
        self.git("checkout", "-q", "feature")
        subprocess.run(["git", "-C", self.root, "rebase", "-q", "-X", "ours", "main"],
                       capture_output=True)
        self.assertEqual(self.candidates(), ([], ["src/app.py"]))


class SummaryTest(Sandbox):
    def test_summary_groups_repeated_tool_failures(self):
        for _ in range(3):
            self.shell("npm run build", 1, "error TS2304: Cannot find name")
        self.shell("make lint", 2, "lint failed")
        self.shell("rm -rf dist", -1, "The tool call was cancelled before it was approved.")
        data = aggregate.aggregate(self.root, by="all")
        top = data["tool_failures"][0]
        self.assertEqual((top["command_key"], top["count"]), ("npm run build", 3))
        row = data["periods"]["全期間"]
        self.assertEqual((row["tool_failures"], row["cancelled"], row["sessions"]), (4, 1, 1))
        text = aggregate.render(data)
        self.assertIn("npm run build", text)
        self.assertIn("人による取り消し（1 種類）", text)

    def test_period_filter_and_empty_summary(self):
        self.shell("make lint", 2)
        future = aggregate.parse_bound("2999-01-01", None)
        data = aggregate.aggregate(self.root, since=future)
        self.assertEqual(data["event_count"], 0)
        self.assertIn("記録はありません", aggregate.render(data))

    def test_broken_line_is_skipped(self):
        self.shell("make lint", 2)
        with open(os.path.join(self.root, ".kiro/retro/events.jsonl"), "a") as handle:
            handle.write("{broken\n")
        self.assertEqual(aggregate.aggregate(self.root, by="all")["event_count"], 1)


class ExcerptTest(unittest.TestCase):
    def test_excerpt_finds_command_and_masks(self):
        home = tempfile.mkdtemp(prefix="kiroku-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        log_dir = os.path.join(home, "sessions", "0123456789abcdef", "sess_x")
        os.makedirs(log_dir)
        rows = [
            {"type": "user", "content": "ビルドして"},
            {"type": "turn_start"},
            {"type": "tool_call", "toolName": "execute_bash", "toolCallId": "1",
             "args": {"command": "npm run build"}},
            {"type": "tool_result", "toolCallId": "1", "success": True,
             "content": "password=hunter2pw failed"},
            {"type": "assistant", "content": "失敗しました"},
        ]
        with open(os.path.join(log_dir, "messages.jsonl"), "w", encoding="utf-8") as handle:
            for index, payload in enumerate(rows):
                handle.write(json.dumps({"id": str(index), "payload": payload,
                                         "timestamp": "2026-11-12T00:00:0%dZ" % index}) + "\n")
        env = dict(os.environ, KIRO_HOME=home)
        done = subprocess.run(
            [sys.executable, os.path.join(KIT, "excerpt.py"), "--session", "sess_x",
             "--command", "npm run"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0)
        self.assertIn("← 該当", done.stdout)
        self.assertIn("失敗しました", done.stdout)
        self.assertNotIn("hunter2pw", done.stdout)
        self.assertNotIn("turn_start", done.stdout)
        done = subprocess.run(
            [sys.executable, os.path.join(KIT, "excerpt.py"), "--session", "sess_x",
             "--around", "2026-11-12T00:00:04Z", "--context", "0"],
            capture_output=True, text=True, env=env)
        self.assertIn("失敗しました", done.stdout)
        self.assertEqual(excerpt.parse_time("not a time"), None)


class MaskTest(unittest.TestCase):
    def test_plain_text_is_kept(self):
        text = "ls: cannot access '/no/such/dir': No such file or directory"
        self.assertEqual(record.mask(text), text)


if __name__ == "__main__":
    unittest.main()
