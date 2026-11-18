"""ノートの照合、台帳、セッションの探索、steering の重さのテスト。"""
import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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

    def note(self, session, findings, shown=True):
        """ノートを書く。shown なら、その前に要約表示で全期間を見せたことにする（振り返りの手順どおり）。"""
        if shown:
            aggregate.prompts.record_shown(self.root, session, None, None)
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

    def test_policy_stated_after_the_period_does_not_promote_a_past_single_finding(self):
        """Issue #12 指摘7：期間の後の継続方針が、期間内の1回の指摘を型に変えていた。"""
        self.log("sess_old", ["テストは make test で回して"], days_ago=10)
        self.note("sess_old", [finding("テストは make test で回して")])
        since, until = NOW - datetime.timedelta(days=14), NOW - datetime.timedelta(days=7)
        self.assertFalse(aggregate.aggregate(self.root, since, until)["topics"][0]["candidate"])
        self.log("sess_new", ["今後もテストは必ず make test で回すこと"], days_ago=1)
        self.note("sess_new", [finding("今後もテストは必ず make test で回すこと", kind="standing_policy")])
        data = aggregate.aggregate(self.root, since, until)
        topic = data["topics"][0]
        self.assertEqual((topic["candidate"], topic["sessions"], topic["all_sessions"]), (False, 1, 2))
        self.assertEqual(topic["period_kinds"], ["correction"])
        self.assertNotIn("topic:テスト実行コマンド", data["report_facts"]["targets"])
        # 方針が期間内にあれば、これまでどおり1回でも候補になる。
        self.assertTrue(aggregate.aggregate(self.root, since)["topics"][0]["candidate"])

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
    def test_old_broad_target_is_unknown_when_commands_are_ambiguous(self):
        self.shell('cd /tmp && make test', 2)
        self.shell('cd /tmp && make lint', 2)
        self.report('2026-11-14-0100', '### P1. 以前の案\n- 対象の型：`tool:cd /tmp &&`\n')
        self.assertIsNone(aggregate.aggregate(self.root)['pending'][0]['recent'])

    def test_old_target_is_unknown_when_an_exact_match_hides_other_commands(self):
        """旧版の対象は先頭3語が同じ失敗すべて。完全一致の実行だけを数えて対象を狭めない。"""
        self.shell('npm run build', 1)
        self.shell('npm run build --production', 1)
        self.report('2026-11-01-0900', '### P1. 旧版の案\n- 対象の型：`tool:npm run build`\n')
        self.assertIsNone(aggregate.aggregate(self.root)['pending'][0]['recent'])
        self.assertEqual(self.cli('adopt', 'P1').returncode, 0)
        self.assertEqual(aggregate.aggregate(self.root)['adopted'][0]['verdict'], '対象の型を数えられない')

    def test_current_report_target_counts_only_its_own_command(self):
        self.shell('npm run build', 1)
        self.shell('npm run build --production', 1)
        self.report('2026-11-14-0900', '### P1. 今の版の案\n- 対象の型：`tool:npm run build`\n\n'
                    '```kiroku-facts\n{}\n```\n')
        self.assertEqual(aggregate.aggregate(self.root)['pending'][0]['recent'], 1)

    REPORT = ("# 振り返り\n\n## ハーネスへの改善案（プロジェクト内）\n\n"
              "### P1. ビルド前に依存を入れる hook を足す\n\n- 区分：実績に基づく改善\n"
              "- 対象の型：`tool:npm run build`\n\n"
              "### P2. テストの回し方を steering に書く\n\n- 対象の型：`topic:テスト実行コマンド`\n\n"
              "## 様子見\n\n- 対象の型：`tool:ignored`\n")

    def cli(self, *args):
        return subprocess.run([sys.executable, LEDGER, "--root", self.root] + list(args),
                              capture_output=True, text=True)

    def test_latest_report_without_proposals_retires_old_bare_numbers(self):
        self.report("2026-11-01-0900", self.REPORT)
        self.report("2026-11-08-0900", "# 振り返り\n\n改善案なし\n")
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["pending"], [])
        self.assertEqual(self.cli("adopt", "P1").returncode, 1)
        self.assertEqual(ledger.decisions(self.root), {})
        self.assertIn("古いレポート", self.cli("list").stdout)
        self.assertEqual(self.cli("adopt", "2026-11-01-0900#P1").returncode, 0)

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


class AppliedTest(ReviewSandbox):
    """Issue #12 機能追加：反映日を人の申告なしに決め、反映後に観測できた作業量を出す。"""

    backdate = LedgerTest.backdate

    def setUp(self):
        super().setUp()
        self.write(".kiro/steering/build.md", "ビルドの手順\n")
        self.commit_at(13, "steering")   # レポートより前からあるファイル
        stamp = (NOW - datetime.timedelta(days=13)).timestamp()
        os.utime(os.path.join(self.root, ".kiro/steering/build.md"), (stamp, stamp))
        self.made = (NOW - datetime.timedelta(days=12)).astimezone().strftime("%Y-%m-%d-%H%M")
        self.report(self.made, "<!-- kiroku-report:3 -->\n### P1. ビルド前に依存を入れる\n"
                               "- 対象の型：`tool:npm run build`\n"
                               "- 対象のファイル：`.kiro/steering/build.md`、`.kiro/hooks/deps.json`（新規）\n")
        self.backdate(11, 3)
        self.decide(self.made + "#P1", "adopted", 10, "tool:npm run build")

    def commit_at(self, days_ago, message):
        when = days_ago if isinstance(days_ago, datetime.datetime) else NOW - datetime.timedelta(days=days_ago)
        stamp = when.isoformat()
        env = dict(os.environ, GIT_COMMITTER_DATE=stamp, GIT_AUTHOR_DATE=stamp)
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-q", "-m", message], check=True, env=env)

    def row(self):
        return aggregate.aggregate(self.root, since=None)["adopted"][0]

    def test_unchanged_target_file_is_not_called_applied(self):
        row = self.row()
        self.assertEqual(row["files"], [".kiro/steering/build.md", ".kiro/hooks/deps.json"])
        self.assertIsNone(row["applied_at"])
        self.assertEqual(row["verdict"], "対象のファイルに変更なし（反映を確認できない）")
        self.assertIn("反映は未確認", aggregate.render(aggregate.aggregate(self.root, since=None)))

    def test_first_commit_after_the_report_is_the_applied_date(self):
        self.write(".kiro/steering/build.md", "ビルドの前に npm ci を実行する\n")
        self.commit_at(6, "反映")
        self.backdate(8, 2)   # 採用の後、反映の前の失敗は「反映前」に数える
        self.backdate(1, 0)
        with open(self.data / "events.jsonl", "a", encoding="utf-8") as handle:   # 反映後の別の作業
            handle.write(json.dumps({"type": "prompt", "session_id": "s2", "v": 2,
                                     "ts": (NOW - datetime.timedelta(days=2)).isoformat()}) + "\n")
        row = self.row()
        self.assertEqual(row["split"], "反映")
        self.assertTrue(row["applied_basis"].startswith("コミット "), row["applied_basis"])
        self.assertAlmostEqual(aggregate.parse_time(row["applied_at"]).timestamp(),
                               (NOW - datetime.timedelta(days=6)).timestamp(), delta=2)
        self.assertEqual((row["before"], row["after"], row["verdict"]), (5, 0, "再発なし"))
        self.assertEqual(row["observed"], {"unit": "フックの記録があるセッション", "count": 1,
                                           "note": "実行総数は不明"})
        text = aggregate.render(aggregate.aggregate(self.root, since=None))
        self.assertIn("反映前 5 回", text)
        self.assertIn("反映後の観測：フックの記録があるセッション 1（実行総数は不明）", text)

    def test_commit_before_the_report_in_the_same_minute_is_not_the_applied_date(self):
        """PR #13 のレビュー指摘2：名前の分の頭を作成時刻とみなし、作る前の同じ分のコミットを反映としていた。"""
        minute = datetime.datetime.strptime(self.made, "%Y-%m-%d-%H%M").astimezone()
        state_path = self.data / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        state["report_made"] = {self.made: (minute + datetime.timedelta(seconds=40)).isoformat()}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.write(".kiro/steering/build.md", "作る前の変更\n")
        self.commit_at(minute + datetime.timedelta(seconds=15), "作る前")
        self.assertIsNone(self.row()["applied_at"])
        self.write(".kiro/steering/build.md", "作った後の変更\n")
        self.commit_at(minute + datetime.timedelta(minutes=2), "作った後")
        applied = aggregate.parse_time(self.row()["applied_at"])
        self.assertEqual(applied, (minute + datetime.timedelta(minutes=2)).astimezone(datetime.timezone.utc))
        # 正確な時刻の控えが無い以前のレポートは、名前の分の終わりを基準にする。同じ分のコミットは数えない。
        state_path.write_text("{}", encoding="utf-8")
        self.assertEqual(aggregate.parse_time(self.row()["applied_at"]), applied)
        self.assertEqual(aggregate.ledger.report_time(self.root, self.made), minute + datetime.timedelta(minutes=1))

    def test_commit_in_the_same_second_after_the_report_is_the_applied_date(self):
        """PR #13 の再レビュー指摘2：作った直後の同じ秒のコミットを、反映に数えていなかった。"""
        self.write(".kiro/hooks/deps.json", "{}")
        self.commit_at(13, "レポートより前の状態")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(aggregate.new_report(self.root, aggregate.aggregate(self.root)), 0)
        name = sorted((self.data / "reports").glob("*.md"))[-1].stem
        made = aggregate.ledger.report_made(self.root, name)
        self.assertTrue(made["exact"])
        self.assertRegex(made["head"], r"^[0-9a-f]{40}$")
        self.report(name, "<!-- kiroku-report:3 -->\n### P1. 依存を入れる\n- 対象の型：`tool:npm run build`\n"
                          "- 対象のファイル：`.kiro/hooks/deps.json`\n")
        self.decide(name + "#P1", "adopted", 0, "tool:npm run build")
        self.write(".kiro/hooks/deps.json", '{"hooks": []}')
        self.commit_at(datetime.datetime.fromtimestamp(int(made["at"].timestamp()), datetime.timezone.utc),
                       "作った直後、同じ秒")
        row = [r for r in aggregate.aggregate(self.root, since=None)["adopted"] if r["id"] == name + "#P1"][0]
        self.assertIsNotNone(row["applied_at"])
        self.assertNotIn("判別できない", row["applied_basis"])
        # HEAD の控えが無ければ、時刻では前後を判別できないと書く。
        state = json.loads((self.data / "state.json").read_text(encoding="utf-8"))
        state.pop("report_heads")
        (self.data / "state.json").write_text(json.dumps(state), encoding="utf-8")
        row = [r for r in aggregate.aggregate(self.root, since=None)["adopted"] if r["id"] == name + "#P1"][0]
        self.assertIn("作る前か後かは判別できない", row["applied_basis"])

    def test_commit_exactly_at_the_end_of_the_named_minute_counts_for_old_reports(self):
        minute = datetime.datetime.strptime(self.made, "%Y-%m-%d-%H%M").astimezone()
        self.write(".kiro/steering/build.md", "次の分の頭の変更\n")
        self.commit_at(minute + datetime.timedelta(minutes=1), "次の分の頭")
        self.assertEqual(aggregate.parse_time(self.row()["applied_at"]),
                         (minute + datetime.timedelta(minutes=1)).astimezone(datetime.timezone.utc))

    def test_new_report_keeps_the_exact_creation_time(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(aggregate.new_report(self.root, aggregate.aggregate(self.root)), 0)
        name = sorted((self.data / "reports").glob("*.md"))[-1].stem
        made = aggregate.ledger.report_time(self.root, name)
        self.assertLess(abs((made - datetime.datetime.now(datetime.timezone.utc)).total_seconds()), 60)
        self.assertNotEqual(made.second + made.microsecond, 0)   # 分の頭ではなく、作った時刻

    def test_uncommitted_change_uses_the_file_time_and_no_activity_is_not_no_recurrence(self):
        self.write(".kiro/hooks/deps.json", "{}")   # 新規のファイル。未コミット
        stamp = (NOW - datetime.timedelta(days=5)).timestamp()
        os.utime(os.path.join(self.root, ".kiro/hooks/deps.json"), (stamp, stamp))
        row = self.row()
        self.assertIn("コミットされていない変更", row["applied_basis"])
        # 反映後に作業が1件も無い。発生0回でも「再発なし」とは言わない。
        self.assertEqual((row["after"], row["observed"]["count"], row["verdict"]), (0, 0, "反映後の観測なし"))

    def test_proposal_without_target_files_keeps_the_adoption_date(self):
        """以前の版のレポート（対象のファイルの行が無い）は、採用の時刻で比べ、そのことを表示する。"""
        self.backdate(12, 2)
        self.decide("r#P1", "adopted", 7, "tool:npm run build")
        row = [r for r in aggregate.aggregate(self.root, since=None)["adopted"] if r["id"] == "r#P1"][0]
        self.assertEqual((row["split"], row["applied_at"]), ("採用", None))
        self.assertEqual(row["verdict"], "採用後の観測なし")   # 採用後に作業が無い（Issue の例）
        self.assertIn("反映日は不明", aggregate.render(aggregate.aggregate(self.root, since=None)))

    def test_test_target_counts_runs_that_include_the_test(self):
        self.data.mkdir(parents=True, exist_ok=True)
        with open(self.data / "events.jsonl", "a", encoding="utf-8") as handle:
            for days, ids in ((3, ["t::a"]), (2, ["t::b"]), (1, ["t::a", "t::b"])):
                handle.write(json.dumps({"type": "test_run", "session_id": "s", "v": 2, "exit_code": 0,
                                         "results": {"passed": ids}, "head": None, "dirty": {},
                                         "ts": (NOW - datetime.timedelta(days=days)).isoformat()}) + "\n")
        self.decide("r#P2", "adopted", 4, "test:t::a")
        row = [r for r in aggregate.aggregate(self.root, since=None)["adopted"] if r["id"] == "r#P2"][0]
        self.assertEqual(row["observed"], {"unit": "そのテストを含むテスト実行", "count": 2, "note": None})


class DiscoveryTest(ReviewSandbox):
    def test_session_agent_is_available_in_summary_and_report_facts(self):
        self.log('sess_actor', ['今後の完了報告は短くしてください'])
        path = self.home / 'sessions/0123456789abcdef/sess_actor/session.json'
        meta = json.loads(path.read_text(encoding='utf-8'))
        meta['agentMode'] = 'kiroku-live-driver'
        path.write_text(json.dumps(meta), encoding='utf-8')
        data = aggregate.aggregate(self.root)
        self.assertEqual(data['report_facts']['session_agents'], {'sess_actor': 'kiroku-live-driver'})
        view = aggregate.prompts.distill(self.root, 'sess_actor')
        self.assertIn('kiroku-live-driver', aggregate.render_session(view))
        self.assertIn('kiroku-live-driver', aggregate.render(data))

    def test_oldest_retained_event_does_not_claim_recording_start(self):
        self.log('old', ['古い会話'], days_ago=2)
        self.shell('make test', 2)
        text = aggregate.render(aggregate.aggregate(self.root))
        self.assertNotIn('記録を始める前', text)
        self.assertIn('原因は未確認', text)

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

    def test_agents_md_in_subdirectories_is_counted_and_compared(self):
        """Issue #12 改善：Kiro はサブディレクトリの AGENTS.md も常に読み込む（kiro.dev/docs/steering/#agentsmd）。"""
        self.write("app/AGENTS.md", "app の決まり\n")
        self.write("vendor/AGENTS.md", "無視される\n")
        self.write(".gitignore", ".kiro/kiroku/data/\nreports/\nvendor/\n")
        data = aggregate.aggregate(self.root)
        rows = {r["display"]: r for r in data["steering"]["files"]}
        self.assertEqual(set(rows), {"app/AGENTS.md"})
        self.assertEqual((rows["app/AGENTS.md"]["inclusion"], data["steering"]["always_total"]), ("always", 9))
        entry = [e for e in data["harness"] if e["path"].endswith("AGENTS.md")]
        self.assertEqual([(e["status"], [Path(f).name for f in e["files"]]) for e in entry],
                         [("present", ["AGENTS.md"])])
        self.assertIn("| プロジェクト内 | `app/AGENTS.md` | あり |", aggregate.skeleton.build(self.root, data))
        self.write("AGENTS.md", "ルートの決まり\n")
        self.write(".kiro/steering/AGENTS.md", "steering の中\n")
        data = aggregate.aggregate(self.root)
        self.assertEqual(sorted(r["display"] for r in data["steering"]["files"]),
                         [".kiro/steering/AGENTS.md", "AGENTS.md", "app/AGENTS.md"])   # 二重に数えない
        table = aggregate.skeleton.build(self.root, data)
        self.assertEqual(table.count("`.kiro/steering/AGENTS.md`"), 1)
        self.assertIn("| プロジェクト内 | `AGENTS.md` | あり |", table)

    def test_missing_agents_md_is_listed_as_absent(self):
        self.assertIn("| プロジェクト内 | `AGENTS.md` | なし | — | — |",
                      aggregate.skeleton.build(self.root, aggregate.aggregate(self.root)))

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
        self.assertIn("レポート：1 個（ほかに書きかけの骨組み 0 個）、案：2 件", text)
        self.assertIn("| topic | 1 | 0 | 0 |", text)
        self.assertIn("| tool | 0 | 0 | 1 |", text)
        self.assertIn("セッション：3 件（記録あり 1、ログのみ 2）", text)
        self.assertIn("常時読み込みの合計：70 文字", text)

    def test_gap_counts_and_drafts_are_counted_like_the_aggregate(self):
        """Issue #12 指摘10：重なった3回の実行を1件と表示し、書きかけの骨組みをレポートに数えていた。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("review", os.path.join(KIT, "review.py"))
        review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(review)
        for _ in range(3):
            self.hook({"hook_event_name": "PreToolUse", "tool_name": "execute_bash",
                       "tool_input": {"command": "pytest -q --junitxml=%s" % "reports/junit.xml"}})
        self.hook({"hook_event_name": "Stop"})
        data = aggregate.aggregate(self.root)
        self.assertEqual([(g["reason"], g["count"]) for g in data["recording_gaps"]], [("test_end_missing", 3)])
        self.report("2026-11-10-0900", "<!-- kiroku-report:3 -->\n# 振り返り\n\n- 読んだセッション：【ここに書く】\n")
        text = review.summarize(self.root, None, NOW + datetime.timedelta(minutes=1))
        self.assertIn("判定できなかったシェル実行（recording_gap）：3 件", text)
        self.assertIn("レポート：0 個（ほかに書きかけの骨組み 1 個）", text)

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
        self.assertIn("未読の合計：26 件", text)
        self.assertIn("1回の振り返りで読む分：次の %d 件" % len(plan["read"]), text)
        self.assertIn("その後に残る分：%d 件" % plan["rest"], text)
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


class LogOnlyTest(ReviewSandbox):
    def test_conversation_without_hook_records_is_read_and_paged(self):
        """Issue #12 改善：記録が0件でもログの会話は読む対象になり、全文ページにも出る。"""
        self.log("sess_question", ["この関数の使い方を教えて"])
        data = aggregate.aggregate(self.root, NOW - datetime.timedelta(days=7), with_prompts=True)
        self.assertEqual(data["event_count"], 0)
        self.assertEqual(data["reading_plan"]["read"], ["sess_question"])
        page = data["prompt_page"]
        self.assertEqual([c["session_id"] for c in page["chunks"]], ["sess_question"])
        self.assertEqual(page["coverage"][0]["status"], "ok")
        self.assertIn("ログのみ 1 件", aggregate.render(data))


class ScriptRootTest(ReviewSandbox):
    """Issue #12 改善：許可したスクリプトの引数で、別のリポジトリに書かせない。"""

    def run_script(self, name, *args, session="sess_retro"):
        env = dict(os.environ)
        env.pop("KIRO_SESSION_ID", None)
        if session:
            env["KIRO_SESSION_ID"] = session
        return subprocess.run([sys.executable, os.path.join(KIT, name)] + list(args),
                              capture_output=True, text=True, cwd=self.root, env=env)

    def test_root_outside_the_current_repository_is_refused_inside_kiro(self):
        other = Path(tempfile.mkdtemp(prefix="kiroku-other-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        for name, args in (("aggregate.py", ["--new-report"]), ("ledger.py", ["list"]), ("review.py", [])):
            done = self.run_script(name, "--root", str(other), *args)
            self.assertEqual(done.returncode, 1, name + done.stdout + done.stderr)
            self.assertIn("--root に現在のリポジトリ以外を指定できません", done.stdout)
        self.assertFalse((other / ".kiro").exists())   # 何も書いていない
        # 現在のリポジトリを指す --root と、--root の省略は通る。
        self.assertEqual(self.run_script("ledger.py", "--root", self.root, "list").returncode, 0)
        self.assertEqual(self.run_script("ledger.py", "list").returncode, 0)
        # Kiro の外（人の端末）では、これまでどおり別のリポジトリを指定できる。
        self.assertEqual(self.run_script("ledger.py", "--root", str(other), "list", session=None).returncode, 0)


class NoteCoverageTest(ReviewSandbox):
    """Issue #12 指摘8：ノートが読み終えたとみなすのは、ノートの前に要約表示で見せた範囲の発話だけ。"""

    def show(self, session, since):
        with contextlib.redirect_stdout(io.StringIO()):
            aggregate.main(["--root", self.root, "--since", since, "--session", session])

    def test_widening_the_period_brings_back_prompts_outside_the_range_read(self):
        directory = self.home / "sessions" / "0123456789abcdef" / "sess_wide"
        directory.mkdir(parents=True)
        (directory / "session.json").write_text(json.dumps(
            {"id": "sess_wide", "workspacePaths": [self.root]}), encoding="utf-8")
        with open(directory / "messages.jsonl", "w", encoding="utf-8") as handle:
            for index, days in enumerate((20, 1)):
                stamp = (NOW - datetime.timedelta(days=days)).isoformat()
                handle.write(json.dumps({"id": str(index), "timestamp": stamp, "payload": {
                    "type": "user", "content": "依頼 %d" % index}}, ensure_ascii=False) + "\n")
        self.show("sess_wide", "7d")
        self.note("sess_wide", [], shown=False)
        week = aggregate.aggregate(self.root, NOW - datetime.timedelta(days=7))
        self.assertEqual((week["sessions"][0]["note"], week["reading_plan"]["read"]), ("fresh", []))
        month = aggregate.aggregate(self.root, NOW - datetime.timedelta(days=30))
        self.assertEqual(month["sessions"][0]["prompts"], 2)
        self.assertEqual(month["sessions"][0]["note"], "uncovered")
        self.assertEqual(month["reading_plan"]["read"], ["sess_wide"])
        self.assertIn("読んだ範囲の外に発話あり", aggregate.render(month))
        # 広げた期間で見せ直してからノートを書き直せば、読み終えたことになる。
        self.show("sess_wide", "30d")
        time.sleep(0.05)
        self.note("sess_wide", [], shown=False)
        self.assertEqual(aggregate.aggregate(self.root, NOW - datetime.timedelta(days=30))
                         ["sessions"][0]["note"], "fresh")

    def test_a_note_that_cannot_be_read_is_read_again(self):
        self.log("sess_a", ["依頼その1"])
        self.note("sess_a", [])
        (self.data / "notes" / "sess_a.json").write_text("{壊れた", encoding="utf-8")
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["sessions"][0]["note"], "broken")
        self.assertEqual(data["reading_plan"]["read"], ["sess_a"])
        self.assertIn("ノートを読めない", aggregate.render(data))


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
        self.assertNotIn("使えません", text)
        for value in (-1, "100", 1.5):
            self.write(".kiro/kiroku/config.json", json.dumps({"output_tail_chars": value}))
            text = "\n".join(installer.config_notes(self.root))
            self.assertIn("output_tail_chars = %s は使えません" % json.dumps(value), text)
        self.write(".kiro/kiroku/config.json", json.dumps({"output_tail_chars": 0}))
        self.assertNotIn("使えません", "\n".join(installer.config_notes(self.root)))


class RetroFeedbackTest(ReviewSandbox):
    def test_mentioning_aggregate_does_not_end_development_recording(self):
        self.shell('echo ".kiro/skills/kiroku-retro/scripts/aggregate.py"', 0)
        self.shell('make build', 2, 'boom')
        data = aggregate.aggregate(self.root)
        self.assertEqual(data['event_count'], 1)
        self.assertEqual(data['tool_failures'][0]['command_key'], 'make build')

    """振り返りを実際に回して分かった不具合の再発防止。"""

    def messages(self, session, rows, start=None):
        """種別を指定してセッションログを作る。rows は (type, 本文またはコマンド) の一覧。"""
        directory = self.home / "sessions" / "0123456789abcdef" / session
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "session.json").write_text(json.dumps(
            {"id": session, "workspacePaths": [self.root]}), encoding="utf-8")
        start = start or NOW - datetime.timedelta(minutes=30)
        with open(directory / "messages.jsonl", "w", encoding="utf-8") as handle:
            for index, (kind, value) in enumerate(rows):
                payload = {"type": kind, "content": value}
                if kind == "tool_call":
                    payload = {"type": kind, "toolName": "execute_bash", "args": {"command": value}}
                stamp = (start + datetime.timedelta(seconds=index)).isoformat()
                handle.write(json.dumps({"id": str(index), "timestamp": stamp, "payload": payload},
                                        ensure_ascii=False) + "\n")

    def test_utterance_logged_twice_is_counted_once(self):
        steer = "そのやり方ではなく、先にテストを書いてから直して"
        self.messages("sess_dup", [
            ("user", "ログイン画面を直して"), ("assistant", "直します"),
            ("user", steer), ("tool_call", "ls"), ("assistant", "途中です"),
            ("user", steer), ("assistant", "テストを先に書きました"),
            ("user", "はい"), ("assistant", "続けます"), ("user", "はい")])
        row = aggregate.aggregate(self.root)["sessions"][0]
        self.assertEqual(row["prompts"], 4)   # 割り込みの二重記録だけを除く。短い「はい」2回は別の発話
        view = aggregate.prompts.distill(self.root, "sess_dup")
        self.assertEqual([t["index"] for t in view["turns"]], [0, 2, 7, 9])
        self.assertEqual(view["turns"][1]["duplicates"], [5])
        self.assertEqual(view["turns"][1]["reply"]["index"], 6)   # 重複の後の応答も、最初の発話に付く
        self.assertIn("index=5 にも記録されています", aggregate.render_session(view))

    def test_conversation_after_retro_start_is_not_development(self):
        self.session = "sess_dev"
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "機能を足して"})
        self.shell("make build", 2, "boom")
        self.messages("sess_dev", [
            ("user", "機能を足して"), ("assistant", "足しました"),
            ("user", "/kiroku-retro"),
            ("tool_call", "python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since 7d"),
            ("user", "P1 は採用で")], start=datetime.datetime.now(datetime.timezone.utc))
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "/kiroku-retro"})
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "P1 は採用で"})
        self.assertEqual(len(self.events()), 3)
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["event_count"], 2)           # 振り返り中の発話は数えない
        self.assertEqual(data["coverage"]["retro_excluded"], 1)
        self.assertEqual(data["sessions"][0]["prompts"], 1)
        view = aggregate.prompts.distill(self.root, "sess_dev")
        self.assertEqual([t["text"] for t in view["turns"]], ["機能を足して"])

    def test_retro_agent_session_without_slash_command_is_excluded(self):
        self.session = "sess_retro"
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "振り返りをお願い"})
        self.shell("python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since 7d", 0)
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "P1 は採用で"})
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["event_count"], 1)           # 集約を実行した後の発話は数えない
        # kiroku 自体の開発で、別の場所にあるスクリプトを動かしても、振り返りとは扱わない。
        self.session = "sess_kit"
        self.shell("python3 kit/.kiro/skills/kiroku-retro/scripts/aggregate.py", 0)
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "続けて"})
        self.assertEqual(aggregate.aggregate(self.root)["event_count"], 2)

    def test_timeout_is_not_reported_as_denial(self):
        self.shell("npm run e2e", -1, "partial output\nCommand timed out after 120000ms")
        self.shell("mystery", -1, "something else")
        self.assertEqual([e["type"] for e in self.events()], ["tool_timeout", "recording_gap"])
        # 古い版が「拒否」として記録したものも、集約のときに分け直す。
        with open(self.data / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "tool_denied", "command": "npm run e2e", "v": 2,
                                     "ts": NOW.isoformat(), "session_id": "sess_old",
                                     "output_tail": "Command timed out after 120000ms"}) + "\n")
        data = aggregate.aggregate(self.root)
        self.assertEqual([(r["type"], r["count"]) for r in data["tool_failures"]], [("tool_timeout", 2)])
        text = aggregate.render(data)
        self.assertIn("## タイムアウト（1 種類）", text)
        self.assertIn("対象の型：`timeout:npm run e2e`", text)
        self.assertIn("## 権限の規則による拒否（0 種類）", text)

    def test_coverage_says_which_sessions_have_no_hook_events(self):
        self.log("sess_before", ["導入前の依頼"], days_ago=3)
        self.log("sess_silent", ["フックが動かなかった依頼"], days_ago=0)
        self.session = "sess_recorded"
        self.log("sess_recorded", ["記録のある依頼"], days_ago=1)
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "記録のある依頼"})
        events = self.data / "events.jsonl"   # 記録は1日前に始まったことにする
        event = json.loads(events.read_text(encoding="utf-8"))
        event["ts"] = (NOW - datetime.timedelta(days=1)).isoformat()
        events.write_text(json.dumps(event) + "\n", encoding="utf-8")
        data = aggregate.aggregate(self.root)
        cover = data["coverage"]
        self.assertEqual((cover["recorded"], cover["log_only"], cover["before_first"], cover["after_first"]),
                         (1, 2, 1, 1))
        self.assertIn("記録（フックによるイベント）があるもの 1 件、ログのみ 2 件", aggregate.render(data))

    def test_default_output_fits_the_budget_and_sections_give_the_rest(self):
        for number in range(60):
            self.session = "sess_%02d" % number
            self.log(self.session, ["依頼 %d" % number])
            self.shell("tool%d --flag value" % number, 1, "\n".join(["error line " + "x" * 150] * 20))
        done = subprocess.run([sys.executable, os.path.join(KIT, "aggregate.py"), "--root", self.root],
                              capture_output=True, text=True, check=True)
        self.assertLessEqual(len(done.stdout), 20000)
        self.assertLess(done.stdout.index("## 未読のセッション"), done.stdout.index("## ツール失敗"))
        self.assertLess(done.stdout.index("## 会話からの型の候補"), done.stdout.index("## ツール失敗"))
        self.assertIn("全件は `--section tools` で出します", done.stdout)
        full = subprocess.run([sys.executable, os.path.join(KIT, "aggregate.py"), "--root", self.root,
                               "--section", "tools"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(full.count("対象の型：`tool:"), 60)
        self.assertNotIn("## セッション一覧", full)


class PendingTest(ReviewSandbox):
    OLD = ("### P1. 古い案\n- 対象の型：`tool:old thing`\n")
    NEW = ("### P1. ビルドの前提を確かめる\n- 対象の型：`tool:npm run build`\n"
           "### P2. テストの回し方を書く\n- 対象の型：`topic:テスト実行コマンド`\n"
           "### P3. 対象の型が無い案\n")

    def test_only_undecided_proposals_of_the_latest_report_are_pending(self):
        self.report("2026-11-01-0900", self.OLD)
        self.report("2026-11-08-0900", self.NEW)
        for _ in range(2):
            self.shell("npm run build", 1, "boom")
        self.decide("2026-11-08-0900#P2", "rejected", 0, "topic:テスト実行コマンド")
        data = aggregate.aggregate(self.root)
        pending = {row["number"]: row for row in data["pending"]}
        self.assertEqual(set(pending), {"P1", "P3"})          # 古いレポートの案と、判断済みの案は入らない
        self.assertEqual(pending["P1"]["recent"], 2)          # 対象の型が、期間内に何回出たか
        self.assertIsNone(pending["P3"]["recent"])
        text = aggregate.render(data)
        self.assertIn("前回のレポートの未判断の案（2 件）", text)
        self.assertIn("`2026-11-08-0900#P1` ビルドの前提を確かめる", text)
        self.assertNotIn("古い案", text)

    def test_nothing_pending_when_everything_is_decided(self):
        self.report("2026-11-08-0900", "### P1. 案\n- 対象の型：`tool:x`\n")
        self.decide("2026-11-08-0900#P1", "adopted", 0, "tool:x")
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["pending"], [])
        self.assertNotIn("未判断の案", aggregate.render(data))

    def test_list_points_to_the_latest_report_for_old_undecided_proposals(self):
        self.report("2026-11-01-0900", self.OLD)
        self.report("2026-11-08-0900", self.NEW)
        done = subprocess.run([sys.executable, LEDGER, "--root", self.root, "list"],
                              capture_output=True, text=True)
        self.assertIn("2026-11-08-0900#P1  [未判断]", done.stdout)
        self.assertIn("2026-11-01-0900#P1  [古いレポート。最新を見てください]", done.stdout)


class DuplicateTest(unittest.TestCase):
    def test_longer_follow_up_is_not_a_duplicate(self):
        rows = [("t", {"type": "user", "content": "テストを実行してください"}),
                ("t", {"type": "assistant", "content": "実行しました"}),
                ("t", {"type": "user", "content": "テストを実行してください。通ったらコミットして"}),
                ("t", {"type": "user", "content": "テストを実行してください"})]
        # 2件目（index=2）は指示を足しているので別の発話。同じ内容の index=3 だけが重複。
        self.assertEqual(aggregate.prompts.duplicates(rows), {3: 0})


class QuoteRuleTest(ReviewSandbox):
    def test_short_quote_is_accepted_only_as_a_whole_utterance(self):
        self.log("sess_a", ["ん？", "ログイン画面を直して"])
        self.log("sess_b", ["おい", "状況は"])
        self.note("sess_a", [finding("ん？", "作業の経過が見えない", "explanation_feedback", 0),
                             finding("画面", "作業の経過が見えない", "explanation_feedback", 1)])  # 切り出しは不可
        self.note("sess_b", [finding("おい", "作業の経過が見えない", "explanation_feedback", 0),
                             finding("状況は", "作業の経過が見えない", "explanation_feedback", 1)])
        data = aggregate.aggregate(self.root)
        topic = data["topics"][0]
        self.assertEqual((topic["sessions"], topic["count"], topic["candidate"]), (2, 3, True))
        self.assertEqual(len(data["note_problems"]), 1)
        self.assertIn("発話の全体と一致するときだけ", data["note_problems"][0]["problem"])

    def test_unsure_only_sessions_do_not_make_a_pattern(self):
        self.log("sess_a", ["ootake にデプロイして"])
        self.log("sess_b", ["あれをデプロイしておいて"])
        self.note("sess_a", [finding("ootake にデプロイして", "デプロイ先", "correction")])
        self.note("sess_b", [finding("あれをデプロイしておいて", "デプロイ先", "unsure")])
        data = aggregate.aggregate(self.root)
        topic = data["topics"][0]
        self.assertEqual((topic["sessions"], topic["unsure_only_sessions"], topic["candidate"]), (2, 1, False))
        self.log("sess_c", ["また ootake にデプロイし忘れてる"])
        self.note("sess_c", [finding("また ootake にデプロイし忘れてる", "デプロイ先", "correction")])
        data = aggregate.aggregate(self.root)
        self.assertTrue(data["topics"][0]["candidate"])
        self.assertIn("3 セッション、うち確認事項だけのセッション 1 件", aggregate.render(data))
