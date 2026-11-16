"""レポートの骨組み：事実の部分はスクリプトが書き、保存後にその部分が残っているかを確かめる。

エージェントが欄を埋める操作は、ここでは文字列の置き換えで模している。Kiro と LLM の実際の
書き方は確かめていない。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from test_kiroku import KIT, aggregate
from test_review import ReviewSandbox
import test_unrecorded

BLOCKED = test_unrecorded.BLOCKED

skeleton = aggregate.skeleton
AGGREGATE = os.path.join(KIT, "aggregate.py")
LEDGER = os.path.join(KIT, "ledger.py")
OLD_REPORT = "### P1. 以前の案\n- 対象の型：`tool:make test`\n"
PROPOSAL = ("### P1. テストの実行方法を直す\n\n- 対象の型：`tool:make test`\n"
            "- 対象のファイル：`.kiro/steering/testing.md`\n"
            "- 引用（ファイル）：`.kiro/steering/testing.md`「テストは make test で実行する。」\n- 変更の内容：…")


class SkeletonTest(ReviewSandbox):
    def setUp(self):
        super().setUp()
        self.write(".kiro/steering/testing.md", "テストは make test で実行する。\n")
        self.write(".kiro/skills/add-category/SKILL.md", "---\nname: add-category\n---\n")
        self.write(".kiro/skills/kiroku-retro/SKILL.md", "kiroku 自身")
        self.shell("make test", 2)
        helper = test_unrecorded.UnrecordedCallTest("session_log")
        helper.home = self.home
        helper.root = self.root
        rows = [{"type": "user", "content": "実行"}, helper.hook_row("guard-shell")]
        rows += helper.call("c1", BLOCKED, "denied", "{}") + helper.call("c2", BLOCKED, "denied", "{}")
        rows += helper.call("c3", "sleep 30", "failed", "This tool was interrupted before it reported a result.")
        helper.session_log("sess_live", rows)

    def cli(self, *args, script=AGGREGATE):
        return subprocess.run([sys.executable, script, "--root", self.root] + list(args),
                              capture_output=True, text=True)

    def new_report(self):
        done = self.cli("--since", "7d", "--new-report")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        files = sorted((self.data / "reports").glob("*.md"))
        return files[-1], files[-1].read_text(encoding="utf-8")

    def fill(self, text, state="確認済み"):
        """欄を、それらしい内容で埋める（エージェントの書き込みの代わり）。"""
        text = text.replace(skeleton.slot("確認済み／未確認"), state)
        return skeleton.SLOT.sub("なし", self.propose(text, PROPOSAL))

    def propose(self, text, proposal):
        marker = "## ハーネスへの改善案（プロジェクト内）\n\n"
        start = text.index(marker) + len(marker)
        return text[:start] + proposal + text[text.index("\n", start):]

    def observe(self, text, body):
        marker = "## 観測した型・継続方針\n\n"
        start = text.index(marker) + len(marker)
        return text[:start] + body + text[text.index("\n", start):]

    def check(self, path, text):
        path.write_text(text, encoding="utf-8")
        return self.cli("--check-report", str(path))

    def test_script_writes_the_facts_and_the_agent_only_fills_slots(self):
        path, text = self.new_report()
        self.assertTrue(text.startswith(skeleton.MARK))
        self.assertIn("| プロジェクト内 | `.kiro/steering/testing.md` | あり | 【ここに書く", text)
        self.assertIn("| プロジェクト内 | `.kiro/agents` | なし | — | — |", text)
        self.assertIn("| ユーザー共通 | `~/.kiro/steering` | なし | — | — |", text)
        self.assertNotIn("kiroku-retro/SKILL.md", text)   # kiroku 自身のファイルは比べない
        self.assertIn("- 実行前に止められた呼び出し：`%s`、2 回、1 セッション。" % BLOCKED, text)
        self.assertIn("- 実行中に中断された呼び出し：`sleep 30`、1 回、1 セッション。", text)
        self.assertIn("```kiroku-facts", text)
        done = self.check(path, self.fill(text))
        self.assertEqual(done.returncode, 0, done.stdout)

    def test_unfilled_slots_and_rewritten_facts_fail_the_check(self):
        path, text = self.new_report()
        filled = self.fill(text)
        cases = {
            "欄を残した": text.replace(skeleton.slot("無ければ「なし」"), "なし"),
            "回数を言い換えた": filled.replace("`、2 回、1 セッション", "`、数回"),
            "中断の行を消した": "\n".join(l for l in filled.splitlines() if "実行中に中断された" not in l),
            "所在を親にまとめた": filled.replace("| ユーザー共通 | `~/.kiro/steering` | なし | — | — |",
                                           "| ユーザー共通 | `~/.kiro/` | なし | — | — |"),
            "件数を書き換えた": filled.replace("- イベント数、セッション数：1 件", "- イベント数、セッション数：3 件"),
        }
        for label, body in cases.items():
            done = self.check(path, body)
            self.assertEqual(done.returncode, 1, label + done.stdout)
        self.assertIn("目印（【ここに書く…】）ごと", self.check(path, cases["欄を残した"]).stdout)
        # 実機で起きた形：内容を書き足したが、目印を消していない。検証値の囲みを消した。
        appended = filled.replace("## 様子見\n\nなし", "## 様子見\n\n%s\n特になし" % skeleton.slot("無ければ「なし」"))
        self.assertEqual(self.check(path, appended).returncode, 1)
        self.assertEqual(self.check(path, filled.replace("```kiroku-facts\n", "")).returncode, 1)
        lines = filled.splitlines()   # 検証値は1行。本文を埋もれさせない
        self.assertEqual(lines.index("```", lines.index("```kiroku-facts")) - lines.index("```kiroku-facts"), 2)
        self.assertRegex(filled, r"- 対象期間：\d{4}-\d\d-\d\d \d\d:\d\d 〜 \d{4}-\d\d-\d\d \d\d:\d\d（UTC）")
        self.assertIn("消えているか、書き換えられています", self.check(path, cases["中断の行を消した"]).stdout)

    def test_unread_file_in_the_table_contradicts_none_in_the_header(self):
        path, text = self.new_report()
        done = self.check(path, self.fill(text, state="未確認"))   # 冒頭は「なし」になる
        self.assertEqual(done.returncode, 1)
        self.assertIn("読めなかった範囲", done.stdout)
        fixed = self.fill(text, state="未確認").replace(
            "- 読めなかった範囲（未確認）：なし", "- 読めなかった範囲（未確認）：testing.md と add-category の本文")
        self.assertEqual(self.check(path, fixed).returncode, 0)

    def test_draft_is_not_a_report_until_it_is_filled(self):
        """書きかけの骨組みが「案の無い最新のレポート」になって、前回の未判断の案を外さない。"""
        self.report("2026-11-01-0900", OLD_REPORT)
        path, text = self.new_report()
        self.assertIn("- `2026-11-01-0900#P1` 以前の案", text)
        data = aggregate.aggregate(self.root)
        self.assertEqual([p["id"] for p in data["pending"]], ["2026-11-01-0900#P1"])
        listing = self.cli("list", script=LEDGER).stdout
        self.assertIn("2026-11-01-0900#P1  [未判断]", listing)
        # 書き終えると最新のレポートになり、検査は「書く前の時点」の未判断の案と照合する。
        self.assertEqual(self.check(path, self.fill(text)).returncode, 0)
        self.assertEqual([p["id"] for p in aggregate.aggregate(self.root)["pending"]], [path.stem + "#P1"])
        self.assertEqual(self.check(path, self.fill(text)).returncode, 0)

    def test_new_report_replaces_an_untouched_draft_but_not_a_started_one(self):
        path, text = self.new_report()
        self.assertEqual(self.cli("--since", "7d", "--new-report").returncode, 0)   # 手を付けていない
        started = text.replace(skeleton.slot("無ければ「なし」"), "なし", 1)
        path.write_text(started, encoding="utf-8")
        again = self.cli("--since", "7d", "--new-report")
        if sorted((self.data / "reports").glob("*.md"))[-1] == path:   # 同じ分のうちに実行できた場合
            self.assertEqual(again.returncode, 1)
            self.assertIn("上書きしません", again.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), started)

    def test_reports_without_the_mark_are_checked_by_the_previous_rules(self):
        facts = aggregate.aggregate(self.root)["report_facts"]
        path = self.data / "reports" / "2026-11-10-0900.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "### P1. 手順\n- 対象の型：`tool:make test`\n\n```kiroku-facts\n%s\n```\n" % json.dumps(facts)
        self.assertEqual(self.check(path, body).returncode, 0)

    def test_same_records_give_the_same_skeleton(self):
        first = aggregate.aggregate(self.root)
        second = aggregate.aggregate(self.root, until=None)
        self.assertEqual(skeleton.build(self.root, first), skeleton.build(self.root, second))
        self.assertEqual(list(Path(self.root, ".kiro").rglob("__pycache__")), [])


class CitationTest(SkeletonTest):
    """主張に付けた引用の実在を確かめる。引用の解釈が正しいかどうかは判定しない。"""

    def setUp(self):
        super().setUp()
        directory = self.home / "sessions" / "0123456789abcdef" / "sess_talk"
        directory.mkdir(parents=True)
        (directory / "session.json").write_text(json.dumps(
            {"id": "sess_talk", "workspacePaths": [self.root]}), encoding="utf-8")
        rows = [("user", "医療カテゴリを追加してください"),
                ("assistant", "テストは12件すべて成功しました。変更点は models.py と cli.py です。"),
                ("user", "完了報告は結論から短く書いてください")]
        with open(directory / "messages.jsonl", "w", encoding="utf-8") as handle:
            for index, (kind, content) in enumerate(rows):
                handle.write(json.dumps({"id": str(index), "timestamp": "2026-11-16T0%d:00:00Z" % index,
                                         "payload": {"type": kind, "content": content}}, ensure_ascii=False) + "\n")
        self.note("sess_talk", [{"kind": "standing_policy", "topic": "完了報告の書き方", "summary": "結論から",
                                 "quote": "完了報告は結論から短く書いてください", "index": 2, "context": "未確認"}])

    TYPE = ("### 1. 完了報告の順序\n\n- 対象の型：`topic:完了報告の書き方`\n"
            "- 引用（利用者）：session `sess_talk` index 2「完了報告は結論から短く書いてください」\n%s"
            "- 原因の見立て：%s\n- 対応する既存の決まり：%s\n")

    def result(self, type_body, proposal=PROPOSAL):
        for stale in (self.data / "reports").glob("*.md"):   # 1つのテストの中で、骨組みから書き直す
            stale.unlink()
        path, text = self.new_report()
        body = skeleton.SLOT.sub("なし", self.observe(self.propose(text, proposal), type_body))
        done = self.check(path, body)
        return done.returncode, done.stdout

    def test_real_quotes_from_user_reply_and_file_pass(self):
        code, out = self.result(self.TYPE % (
            "- 引用（応答）：session `sess_talk` index 1「テストは12件すべて成功しました。」\n"
            "- 引用（ファイル）：`.kiro/steering/testing.md`「テストは make test で実行する」\n",
            "応答は結論を先に書いていた。指摘は今後の方針の明示", "`.kiro/steering/testing.md`"))
        self.assertEqual(code, 0, out)

    def test_a_reply_that_was_never_said_fails(self):
        """実機で出た誤り：応答の順序を、実際と逆に説明した。引用を作っても、照合で落ちる。"""
        code, out = self.result(self.TYPE % (
            "- 引用（応答）：session `sess_talk` index 1「変更点は models.py です。テストは成功しました。」\n",
            "応答が変更点を先に書いていた", "なし"))
        self.assertEqual(code, 1)
        self.assertIn("照合できない引用", out)
        self.assertIn("応答の発話に、この引用が無い", out)

    def test_text_that_is_not_in_the_file_fails(self):
        """実機で出た誤り：ファイルに無い記述を、あるかのように書いた。"""
        proposal = PROPOSAL.replace("「テストは make test で実行する。」", "「5. git commit する」")
        code, out = self.result("なし", proposal)
        self.assertEqual(code, 1)
        self.assertIn("に、この引用が無い", out)

    def test_cause_without_reply_or_file_quote_fails_but_unconfirmed_passes(self):
        stated = self.TYPE % ("", "応答が変更点を先に書いていた", "なし")
        code, out = self.result(stated)
        self.assertEqual(code, 1)
        self.assertIn("原因の見立てに、応答またはファイルの引用がありません", out)
        self.assertEqual(self.result(self.TYPE % ("", "未確認", "なし"))[0], 0)
        self.assertEqual(self.result(self.TYPE % ("", "該当なし（方針の明示）", "なし"))[0], 0)

    def test_naming_an_existing_file_requires_a_quote_from_it(self):
        code, out = self.result(self.TYPE % ("", "未確認", "`.kiro/steering/testing.md` の冒頭"))
        self.assertEqual(code, 1)
        self.assertIn("そのファイルからの引用", out)
        without = PROPOSAL.replace("- 引用（ファイル）：`.kiro/steering/testing.md`「テストは make test で実行する。」\n", "")
        self.assertEqual(self.result("なし", without)[0], 1)
        new_file = without.replace("`.kiro/steering/testing.md`", "`.kiro/steering/report.md`（新規）")
        self.assertEqual(self.result("なし", new_file)[0], 0)

    def test_conversation_type_needs_a_user_quote_and_wrong_role_fails(self):
        body = self.TYPE % ("", "未確認", "なし")
        missing = body.replace("- 引用（利用者）：session `sess_talk` index 2「完了報告は結論から短く書いてください」\n", "")
        code, out = self.result(missing)
        self.assertEqual(code, 1)
        self.assertIn("利用者の発話の引用", out)
        # 利用者の発話を、応答として引用しても通らない。
        swapped = body.replace("引用（利用者）", "引用（応答）")
        self.assertEqual(self.result(swapped)[0], 1)

    def test_quotes_outside_the_repository_and_missing_logs_are_not_accepted(self):
        outside = PROPOSAL.replace("`.kiro/steering/testing.md`「テストは make test で実行する。」",
                                   "`/etc/hostname`「localhost」")
        code, out = self.result("なし", outside)
        self.assertEqual(code, 1)
        self.assertIn("リポジトリと ~/.kiro の外", out)
        gone = self.TYPE % ("", "未確認", "なし")
        code, out = self.result(gone.replace("sess_talk", "sess_missing"))
        self.assertEqual(code, 1)
        self.assertIn("ログを読めない", out)

    def test_first_format_reports_are_not_held_to_the_citation_rules(self):
        path, text = self.new_report()
        old = self.fill(text).replace(skeleton.MARK, "<!-- kiroku-report:1 -->").replace(
            "- 引用（ファイル）：`.kiro/steering/testing.md`「テストは make test で実行する。」\n", "")
        self.assertEqual(self.check(path, old).returncode, 0)

