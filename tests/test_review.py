"""ノートの照合、台帳、セッションの探索、steering の重さのテスト。"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_kiroku import KIT, Sandbox, aggregate

ledger = aggregate.ledger
notes = aggregate.notes
LEDGER = os.path.join(KIT, "ledger.py")
NOW = datetime.datetime.now(datetime.timezone.utc)


class ReviewSandbox(Sandbox):
    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp(prefix="kiroku-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.addCleanup(os.environ.pop, "KIRO_HOME", None)
        os.environ["KIRO_HOME"] = str(self.home)
        self.data = Path(self.root) / ".kiro" / "kiroku" / "data"

    def log(self, session, texts, days_ago=0, workspace=None):
        """ユーザー発話だけのセッションログを作る。"""
        directory = self.home / "sessions" / "0123456789abcdef" / session
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "session.json").write_text(json.dumps(
            {"id": session, "workspacePaths": [workspace or self.root]}), encoding="utf-8")
        start = NOW - datetime.timedelta(days=days_ago, minutes=5)
        with open(directory / "messages.jsonl", "w", encoding="utf-8") as handle:
            for index, text in enumerate(texts):
                stamp = (start + datetime.timedelta(seconds=index)).isoformat()
                handle.write(json.dumps({"id": str(index), "timestamp": stamp,
                                         "payload": {"type": "user", "content": text}},
                                        ensure_ascii=False) + "\n")

    def note(self, session, findings):
        directory = self.data / "notes"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (session + ".json")).write_text(
            json.dumps({"session_id": session, "findings": findings}, ensure_ascii=False),
            encoding="utf-8")

    def report(self, name, body):
        directory = self.data / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (name + ".md")).write_text(body, encoding="utf-8")

    def decide(self, proposal_id, decision, days_ago, target=None):
        """過去の日付で台帳に書く。"""
        stamp = (NOW - datetime.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
        self.data.mkdir(parents=True, exist_ok=True)
        with open(self.data / "ledger.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": proposal_id, "decision": decision, "title": "案",
                                     "target": target, "reason": None, "ts": stamp}) + "\n")


def finding(quote, topic="テスト実行コマンド", kind="correction", index=0):
    return {"kind": kind, "topic": topic, "summary": "テストは make test で回す",
            "quote": quote, "index": index, "context": "未確認"}


class NotesTest(ReviewSandbox):
    def test_topic_in_two_sessions_becomes_a_candidate(self):
        self.log("sess_a", ["テストは make test で回して"])
        self.log("sess_b", ["pytest じゃなくて   make test を使って。前にも言った"])
        self.note("sess_a", [finding("テストは make test で回して")])
        self.note("sess_b", [finding("make test を使って。前にも言った")])  # 空白の違いは無視する
        data = aggregate.aggregate(self.root)
        topic = data["topics"][0]
        self.assertEqual((topic["topic"], topic["sessions"], topic["candidate"]),
                         ("テスト実行コマンド", 2, True))
        self.assertEqual(data["note_problems"], [])
        self.assertIn("`topic:テスト実行コマンド`：2 セッション", aggregate.render(data))

    def test_topic_in_one_session_is_not_a_candidate(self):
        self.log("sess_a", ["説明が長い", "やっぱり説明が長い。結論だけにして"])
        self.note("sess_a", [finding("説明が長い", "説明の長さ", "explanation_feedback", 0),
                             finding("結論だけにして", "説明の長さ", "explanation_feedback", 1)])
        data = aggregate.aggregate(self.root)
        topic = data["topics"][0]
        self.assertEqual((topic["sessions"], topic["count"], topic["candidate"]), (1, 2, False))
        text = aggregate.render(data)
        self.assertIn("会話からの型の候補（0 件）", text)
        self.assertIn("まだ型ではないトピック", text)

    def test_standing_policy_is_a_candidate_from_one_session(self):
        self.log("sess_a", ["今後は必ずコミット前に lint を通すこと"])
        self.note("sess_a", [finding("今後は必ずコミット前に lint を通すこと", "コミット前のlint",
                                     "standing_policy")])
        self.assertTrue(aggregate.aggregate(self.root)["topics"][0]["candidate"])

    def test_fabricated_quote_is_rejected(self):
        self.log("sess_a", ["テストを実行して"])
        self.log("sess_b", ["テストを実行して"])
        self.note("sess_a", [finding("毎回 make test を使えと言っているだろう")])
        self.note("sess_b", [finding("毎回 make test を使えと言っているだろう")])
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["topics"], [])
        self.assertEqual(len(data["note_problems"]), 2)
        self.assertIn("引用がユーザー発話に見つからない", aggregate.render(data))

    def test_malformed_findings_and_notes_are_reported(self):
        self.log("sess_a", ["テストは make test で回して"])
        self.note("sess_a", [finding("テストは make test で回して", kind="made_up"),
                             finding("テストは make test で回して", topic="空白 入り"),
                             finding("短")])
        (self.data / "notes" / "sess_b.json").write_text("{broken", encoding="utf-8")
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["topics"], [])
        self.assertEqual(len(data["note_problems"]), 4)

    def test_verified_quote_survives_log_deletion(self):
        self.log("sess_a", ["テストは make test で回して"])
        self.note("sess_a", [finding("テストは make test で回して")])
        self.assertEqual(aggregate.aggregate(self.root)["note_problems"], [])
        shutil.rmtree(self.home / "sessions")
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["note_problems"], [])
        self.assertEqual(data["topics"][0]["count"], 1)

    def test_unverified_quote_without_log_is_not_counted(self):
        self.note("sess_a", [finding("テストは make test で回して")])
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["topics"], [])
        self.assertIn("ログが無く", data["note_problems"][0]["problem"])


class LedgerTest(ReviewSandbox):
    REPORT = ("# 振り返り\n\n## ハーネスへの改善案（プロジェクト内）\n\n"
              "### P1. ビルド前に依存を入れる hook を足す\n\n- 区分：実績に基づく改善\n"
              "- 対象の型：`tool:npm run build`\n\n"
              "### P2. テストの回し方を steering に書く\n\n- 対象の型：`topic:テスト実行コマンド`\n\n"
              "## 様子見\n\n- 対象の型：`tool:ignored`\n")

    def cli(self, *args):
        return subprocess.run([sys.executable, LEDGER, "--root", self.root] + list(args),
                              capture_output=True, text=True)

    def test_cli_reads_proposals_and_records_decisions(self):
        self.report("2026-11-01-0900", "### P1. 古い案\n- 対象の型：`tool:old`\n")
        self.report("2026-11-08-0900", self.REPORT)
        listed = self.cli("list").stdout
        self.assertIn("2026-11-08-0900#P1  [未判断]  ビルド前に依存を入れる hook を足す", listed)
        self.assertIn("対象の型：topic:テスト実行コマンド", listed)
        self.assertIn("記録しました", self.cli("adopt", "P1").stdout)       # 番号だけなら最新のレポート
        self.cli("reject", "2026-11-08-0900#P2", "--reason", "今は不要")
        known = ledger.decisions(self.root)
        self.assertEqual(known["2026-11-08-0900#P1"]["target"], "tool:npm run build")
        self.assertEqual(known["2026-11-08-0900#P2"]["decision"], "rejected")
        self.assertIn("[採用]", self.cli("list").stdout)
        self.assertEqual(self.cli("adopt", "P9").returncode, 1)
        self.assertEqual(self.cli("adopt", "P1", "--target", "bogus:x").returncode, 1)

    def backdate(self, days_ago, count, command="npm run build"):
        """過去の日付のツール失敗を、記録に直接書く。"""
        self.data.mkdir(parents=True, exist_ok=True)
        with open(self.data / "events.jsonl", "a", encoding="utf-8") as handle:
            for number in range(count):
                stamp = NOW - datetime.timedelta(days=days_ago, minutes=number)
                handle.write(json.dumps({
                    "type": "tool_failure", "command": command, "exit_code": 1, "session_id": "s",
                    "ts": stamp.isoformat().replace("+00:00", "Z"), "v": 2}) + "\n")

    def test_adopted_proposal_is_tracked_before_and_after(self):
        self.backdate(12, 3)
        self.backdate(9, 2)
        self.decide("r#P1", "adopted", 7, "tool:npm run build")
        self.backdate(1, 1, command="npm run lint")  # 別の型は数えない
        row = aggregate.aggregate(self.root, since=None)["adopted"][0]
        self.assertEqual((row["before"], row["after"], row["verdict"]), (5, 0, "再発なし"))
        self.backdate(2, 4)
        row = aggregate.aggregate(self.root, since=None)["adopted"][0]
        self.assertEqual((row["before"], row["after"], row["verdict"]), (5, 4, "減っていない"))
        self.assertIn("採用前 5 回", aggregate.render(aggregate.aggregate(self.root, since=None)))

    def test_recent_adoption_is_not_judged_yet(self):
        self.backdate(5, 3)
        self.decide("r#P1", "adopted", 1, "tool:npm run build")
        self.assertEqual(aggregate.aggregate(self.root)["adopted"][0]["verdict"], "まだ日が浅い")

    def test_topic_target_is_tracked_and_rejections_are_listed(self):
        self.log("sess_a", ["テストは make test で回して"], days_ago=10)
        self.log("sess_b", ["テストは make test で回して"], days_ago=2)
        self.note("sess_a", [finding("テストは make test で回して")])
        self.note("sess_b", [finding("テストは make test で回して")])
        self.decide("r#P2", "adopted", 6, "topic:テスト実行コマンド")
        self.decide("r#P3", "rejected", 6, "topic:テスト実行コマンド")
        self.decide("r#P4", "adopted", 6, None)
        data = aggregate.aggregate(self.root)
        tracked = {row["id"]: row for row in data["adopted"]}
        self.assertEqual((tracked["r#P2"]["before"], tracked["r#P2"]["after"]), (1, 1))
        self.assertEqual(tracked["r#P4"]["verdict"], "対象の型を数えられない")
        self.assertEqual(data["rejected"][0]["after"], 1)
        self.assertIn("見送った案（1 件）", aggregate.render(data))


class DiscoveryTest(ReviewSandbox):
    def test_sessions_without_events_are_found_by_workspace(self):
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "x"})   # sess_test は記録あり
        self.log(self.session, ["記録のあるセッション"])
        self.log("sess_chat", ["この関数は何をしているの？"])                 # 質問だけ。記録なし
        self.log("sess_sub", ["下の階層で作業"], workspace=os.path.join(self.root, "pkg"))
        self.log("sess_other", ["別のプロジェクト"], workspace="/somewhere/else")
        self.log("sess_parent", ["親フォルダ"], workspace=os.path.dirname(self.root))
        self.log("sess_old", ["古い発話"], days_ago=30)
        rows = {r["session_id"]: r for r in aggregate.aggregate(
            self.root, since=NOW - datetime.timedelta(days=7))["sessions"]}
        self.assertEqual(set(rows), {self.session, "sess_chat", "sess_sub"})
        self.assertTrue(rows[self.session]["recorded"])
        self.assertFalse(rows["sess_chat"]["recorded"])
        self.assertEqual(rows["sess_chat"]["prompts"], 1)

    def test_log_only_sessions_are_listed_even_without_any_event(self):
        self.log("sess_chat", ["この関数は何をしているの？"])
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["event_count"], 0)
        text = aggregate.render(data)
        self.assertIn("ログのみ", text)
        self.assertIn("sess_chat", text)


class SteeringWeightTest(ReviewSandbox):
    def test_always_loaded_total_counts_only_unconditional_files(self):
        steering = Path(self.root) / ".kiro" / "steering"
        steering.mkdir(parents=True)
        (steering / "core.md").write_text("あ" * 100, encoding="utf-8")
        (steering / "api.md").write_text(
            '---\ninclusion: fileMatch\nfileMatchPattern: "src/**"\n---\n' + "い" * 500, encoding="utf-8")
        (steering / "ref.md").write_text("---\ninclusion: manual\n---\n" + "う" * 300, encoding="utf-8")
        (Path(self.root) / "AGENTS.md").write_text("え" * 50, encoding="utf-8")
        user = self.home / "steering"
        user.mkdir()
        (user / "me.md").write_text("---\ninclusion: always\n---\n" + "お" * 20, encoding="utf-8")
        weight = aggregate.aggregate(self.root)["steering"]
        by_name = {os.path.basename(r["path"]): r["inclusion"] for r in weight["files"]}
        self.assertEqual(by_name, {"core.md": "always", "api.md": "fileMatch", "ref.md": "manual",
                                   "AGENTS.md": "always", "me.md": "always"})
        self.assertEqual(weight["always_chars"]["project"], 150)
        self.assertEqual(weight["always_total"], 150 + len("---\ninclusion: always\n---\n") + 20)
        text = aggregate.render(aggregate.aggregate(self.root))
        self.assertIn("常時読み込みの合計", text)
        self.assertNotIn("目安の", text)

    def test_large_always_loaded_steering_is_flagged(self):
        steering = Path(self.root) / ".kiro" / "steering"
        steering.mkdir(parents=True)
        (steering / "big.md").write_text("あ" * 9000, encoding="utf-8")
        self.assertIn("目安の 8000 文字を超えています", aggregate.render(aggregate.aggregate(self.root)))

    def test_no_steering(self):
        self.assertIn("steering と AGENTS.md はありません", aggregate.render(aggregate.aggregate(self.root)))


class ReviewSummaryTest(ReviewSandbox):
    def test_summary_has_counts_but_no_project_content(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("review", os.path.join(KIT, "review.py"))
        review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(review)

        for _ in range(3):
            self.shell("secret-build-tool --target internal/payments", 1, "機密の出力 PAYMENT_TABLE")
        self.write("src/internal_payments.py", "x = 1\n")
        self.saved("src/internal_payments.py")
        self.log("sess_a", ["顧客台帳の移行手順をもう一度説明して"])
        self.log("sess_b", ["顧客台帳の移行手順をもう一度説明して"])
        self.note("sess_a", [finding("顧客台帳の移行手順をもう一度説明して", "顧客台帳の移行", "repeated_explanation")])
        self.note("sess_b", [finding("顧客台帳の移行手順をもう一度説明して", "顧客台帳の移行", "repeated_explanation")])
        self.report("2026-11-10-0900", "### P1. 顧客台帳の移行手順を steering に書く\n"
                                       "- 対象の型：`topic:顧客台帳の移行`\n"
                                       "### P2. ビルドの前提を hook で確かめる\n"
                                       "- 対象の型：`tool:secret-build-tool --target internal/payments`\n")
        self.decide("2026-11-10-0900#P1", "adopted", 5, "topic:顧客台帳の移行")
        steering = Path(self.root) / ".kiro" / "steering"
        steering.mkdir(parents=True)
        (steering / "payments-rules.md").write_text("社外秘の決まり" * 10, encoding="utf-8")

        text = review.summarize(self.root, None, NOW + datetime.timedelta(minutes=1))
        for secret in ("secret-build-tool", "payments", "PAYMENT_TABLE", "顧客台帳", "社外秘",
                       "sess_a", self.root):
            self.assertNotIn(secret, text)
        self.assertIn("| ツール失敗 | 1 | 1 | 0 | 3 |", text)
        self.assertIn("トピック：1 個（型の候補 1 個）", text)
        self.assertIn("レポート：1 個、案：2 件", text)
        self.assertIn("| topic | 1 | 0 | 0 |", text)
        self.assertIn("| tool | 0 | 0 | 1 |", text)
        self.assertIn("セッション：3 件（記録あり 1、ログのみ 2）", text)
        self.assertIn("常時読み込みの合計：70 文字", text)

    def test_summary_runs_on_an_empty_repository(self):
        done = subprocess.run([sys.executable, os.path.join(KIT, "review.py"), "--root", self.root],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("イベント数：0", done.stdout)
        self.assertIn("（案なし）", done.stdout)


class AgentPermissionTest(unittest.TestCase):
    def test_retro_agent_may_run_only_kiroku_scripts(self):
        with open(os.path.join(KIT, "..", "..", "..", "agents", "kiroku-retro.json"), encoding="utf-8") as handle:
            rules = json.load(handle)["permissions"]["rules"]
        allow = next(r for r in rules if r["capability"] == "shell" and r["effect"] == "allow")
        deny = next(r for r in rules if r["capability"] == "shell" and r["effect"] == "deny")
        self.assertEqual(sorted(allow["match"]), sorted(deny["exclude"]))
        self.assertEqual(deny["match"], ["*"])
        scripts = {m.split("/")[-1].split(" ")[0] for m in allow["match"]}
        self.assertEqual(scripts, {"aggregate.py", "excerpt.py", "ledger.py"})


class ReadingPlanTest(ReviewSandbox):
    def test_plan_is_bounded_by_volume_not_by_count(self):
        for number in range(25):                       # 短いセッションが25件
            self.log("sess_short_%02d" % number, ["短い依頼"], days_ago=0)
        plan = aggregate.aggregate(self.root)["reading_plan"]
        self.assertEqual((len(plan["read"]), plan["rest"]), (25, 0))

        (Path(self.root) / ".kiro" / "kiroku" / "config.json").write_text(
            json.dumps({"retro_read_chars": 3000}), encoding="utf-8")
        self.log("sess_long", ["長い依頼" * 600], days_ago=0)   # 1件で上限に近い
        data = aggregate.aggregate(self.root)
        plan = data["reading_plan"]
        self.assertLess(len(plan["read"]), 26)
        self.assertEqual(len(plan["read"]) + plan["rest"], 26)
        self.assertLessEqual(plan["read_chars"], 3000 + 2800)   # 最初の1件は上限を超えても読む
        text = aggregate.render(data)
        self.assertIn("今回読むセッション", text)
        self.assertIn("もう一度 /kiroku-retro を実行すると続きを読みます", text)

    def test_sessions_with_notes_are_not_read_again(self):
        self.log("sess_a", ["依頼その1"])
        self.log("sess_b", ["依頼その2"])
        self.assertEqual(len(aggregate.aggregate(self.root)["reading_plan"]["read"]), 2)
        self.note("sess_a", [])
        future = (NOW + datetime.timedelta(hours=1)).timestamp()
        os.utime(self.data / "notes" / "sess_a.json", (future, future))
        plan = aggregate.aggregate(self.root)["reading_plan"]
        self.assertEqual(plan["read"], ["sess_b"])
        self.note("sess_b", [])
        os.utime(self.data / "notes" / "sess_b.json", (future, future))
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["reading_plan"]["read"], [])
        self.assertIn("未読のセッションはありません", aggregate.render(data))


class ConfigTest(ReviewSandbox):
    def test_stale_defaults_in_config_do_not_hide_new_defaults(self):
        """古い版の既定値が config.json に残っていても、新しい既定値が効く。"""
        from test_kiroku import record
        self.write(".kiro/kiroku/config.json", json.dumps({
            "tests": [{"match": "pytest", "report": "r.xml"}],
            "write_tools": ["fs_write", "str_replace", "my_tool"],
            "ignore_paths": [".kiro/kiroku/data/**", "vendor/**"],
            "output_tail_chars": 500, "sessions_dir": None}))
        config = record.load_config(self.root)
        self.assertEqual(config["write_tools"], ["fs_write", "fs_append", "str_replace", "my_tool"])
        self.assertEqual(config["ignore_paths"], [".kiro/kiroku/data/**", ".git/**", "vendor/**"])
        self.assertEqual(config["output_tail_chars"], 500)          # 上書きする項目はそのまま
        self.assertEqual(config["retro_read_chars"], 50000)         # 無い項目は既定値
        self.assertEqual(config["tests"][0]["match"], "pytest")

    def test_installer_explains_missing_config_keys(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "installer_cfg", os.path.join(os.path.dirname(KIT), "..", "..", "..", "..", "install.py"))
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        self.write(".kiro/kiroku/config.json", json.dumps({"tests": [], "write_tools": ["fs_write"]}))
        text = "\n".join(installer.config_notes(self.root))
        self.assertIn("retro_read_chars = 50000", text)
        self.assertIn("write_tools は、既定値に足して使います", text)
        self.assertNotIn("tests =", text)
