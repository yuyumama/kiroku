"""利用者に確かめたこと（骨組みの版5）。答えの引用を、振り返り自身のセッションの利用者の発話と照合する。

ログの形は Kiro CLI 3.3.0 で実測したもの（docs/retro/verification.md）を模している。
エージェントが欄を埋める操作は、文字列の置き換えで模している。Kiro と LLM が実際にどう質問し、
利用者がどう答えるかは確かめていない。
"""
import json
import os

import test_skeleton   # テストのクラスは名前で取り込まない（取り込むと、そのテストがここでも走る）
from test_skeleton import LEDGER, PROPOSAL, aggregate, skeleton

QUESTION = ("### Q1. 作業の終わりに、テストを自動で実行する仕組みを使うか\n\n"
            "- 手がかり：`tool:make test`\n"
            "- 見立て：a（使ってみたい）。同じテストの失敗が続いている\n"
            "- 回答：a（見立てのとおり）\n"
            "- 引用（回答）：「はい」\n"
            "- 反映：P1 を出した")


class QuestionTest(test_skeleton.SkeletonBase):
    RETRO = "sess_retro"
    retro_log = test_skeleton.ReadRecordTest.retro_log
    call = test_skeleton.ReadRecordTest.call
    made = test_skeleton.ReadRecordTest.made

    def setUp(self):
        super().setUp()
        self.addCleanup(os.environ.pop, "KIRO_SESSION_ID", None)
        os.environ["KIRO_SESSION_ID"] = self.RETRO

    def started(self):
        """振り返りの始め：/kiroku-retro の発話と、手順1の集約。"""
        return [{"type": "user", "content": "/kiroku-retro"}] + self.call(
            "a1", "execute_bash",
            {"command": "python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since 7d --require-agent"})

    def answered(self, *texts):
        """振り返りを始め、利用者が答え、骨組みを作った、までのログの行。"""
        return self.started() + [{"type": "user", "content": text} for text in texts] + self.made()

    def ask(self, text, body):
        marker = skeleton.QUESTION_HEADING + "\n\n"
        start = text.index(marker) + len(marker)
        return text[:start] + body + text[text.index("\n", start):]

    def written(self, rows, body, proposal=PROPOSAL):
        if rows is not None:
            self.retro_log(rows)
        for stale in (self.data / "reports").glob("*.md"):
            stale.unlink()
        path, text = self.new_report()
        filled = self.ask(text, body).replace(skeleton.slot("確認済み／未確認"), "未確認")
        filled = skeleton.SLOT.sub("なし", self.propose(filled, proposal))
        filled = filled.replace("- 読めなかった範囲（未確認）：なし", "- 読めなかった範囲（未確認）：表のとおり")
        return path, filled

    def result(self, rows, body, proposal=PROPOSAL):
        path, filled = self.written(rows, body, proposal)
        done = self.check(path, filled)
        return done.returncode, done.stdout

    def setting(self, key, value):
        path = self.data.parent / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config[key] = value
        path.write_text(json.dumps(config), encoding="utf-8")

    def test_the_section_comes_after_the_header_with_the_limit(self):
        path, text = self.new_report()
        self.assertIn(skeleton.QUESTION_HEADING + "\n\n【ここに書く：案を作る前に利用者に聞いた質問を、"
                      "件数の上限なし（あれば全部）。", text)
        self.assertLess(text.index("- 読めなかった範囲（未確認）："), text.index(skeleton.QUESTION_HEADING))
        self.assertIn("- 利用者に聞く質問の上限：なし。答えで変わる案が大きい順に並べる",
                      aggregate.render(aggregate.aggregate(self.root)))
        self.setting("question_max", 2)
        self.assertIn("最大 2 件", self.new_report()[1])
        self.assertIn("- 利用者に聞く質問の上限：2 問", aggregate.render(aggregate.aggregate(self.root)))

    def test_without_a_limit_every_question_asked_is_checked(self):
        """件数の上限の既定は無い（2026-11-24 の決定）。何問でも、引用の照合は1問ずつ行う。"""
        asked = "\n\n".join(QUESTION.replace("### Q1. ", "### Q%d. " % n) for n in range(1, 6))
        code, out = self.result(self.answered("はい"), asked)
        self.assertEqual(code, 0, out)
        wrong = asked.replace("### Q5. 作業の終わりに、テストを自動で実行する仕組みを使うか\n\n- 手がかり：`tool:make test`\n"
                              "- 見立て：a（使ってみたい）。同じテストの失敗が続いている\n- 回答：a（見立てのとおり）\n"
                              "- 引用（回答）：「はい」",
                              "### Q5. 作業の終わりに、テストを自動で実行する仕組みを使うか\n\n- 手がかり：`tool:make test`\n"
                              "- 見立て：a（使ってみたい）。同じテストの失敗が続いている\n- 回答：b\n"
                              "- 引用（回答）：「Q5=b」")
        code, out = self.result(self.answered("はい"), wrong)
        self.assertEqual(code, 1, out)
        self.assertIn("Q5：引用（回答）「Q5=b」が、この振り返りでの利用者の発話にありません", out)

    def test_an_answer_the_user_wrote_passes(self):
        code, out = self.result(self.answered("はい"), QUESTION)
        self.assertEqual(code, 0, out)

    def test_an_answer_the_user_never_wrote_fails(self):
        code, out = self.result(self.answered("Q1=b"), QUESTION)
        self.assertEqual(code, 1, out)
        self.assertIn("この振り返りでの利用者の発話にありません", out)

    def test_answers_before_the_retro_or_by_the_agent_are_not_answers(self):
        rows = [{"type": "user", "content": "はい"}] + self.started() \
            + [{"type": "assistant", "content": "はい"}] + self.made()
        code, out = self.result(rows, QUESTION)
        self.assertEqual(code, 1, out)
        self.assertIn("この振り返りでの利用者の発話にありません", out)

    def test_a_short_answer_must_be_the_whole_message(self):
        rows = self.answered("はい、Q2 は c")
        code, out = self.result(rows, QUESTION)
        self.assertEqual(code, 1, out)
        longer = QUESTION.replace("「はい」", "「はい、Q2 は c」")
        code, out = self.result(rows, longer)
        self.assertEqual(code, 0, out)
        hinted = QUESTION.replace("「はい」", "index 3「はい、Q2 は c」")
        code, out = self.result(rows, hinted)
        self.assertEqual(code, 0, out)

    def test_unanswered_needs_no_quote_but_an_answer_does(self):
        unanswered = QUESTION.replace("- 回答：a（見立てのとおり）", "- 回答：未回答").replace(
            "- 引用（回答）：「はい」\n", "").replace("- 反映：P1 を出した", "- 反映：見立てのまま、様子見に書いた")
        code, out = self.result(self.answered(), unanswered)
        self.assertEqual(code, 0, out)
        unquoted = QUESTION.replace("- 引用（回答）：「はい」\n", "")
        code, out = self.result(self.answered("はい"), unquoted)
        self.assertEqual(code, 1, out)
        self.assertIn("引用（回答）", out)

    def test_every_field_is_needed(self):
        code, out = self.result(self.answered("はい"), QUESTION.replace("- 見立て：a（使ってみたい）。同じテストの失敗が続いている\n", ""))
        self.assertEqual(code, 1, out)
        self.assertIn("Q1：見立て を書いてください", out)
        code, out = self.result(self.answered("はい"), "質問をして、答えをもらった")
        self.assertEqual(code, 1, out)
        self.assertIn("`### Q<番号>. `", out)
        code, out = self.result(self.answered(), "なし")
        self.assertEqual(code, 0, out)

    def test_the_number_of_questions_is_capped_by_the_setting(self):
        second = QUESTION.replace("### Q1. ", "### Q2. ")
        self.setting("question_max", 1)
        code, out = self.result(self.answered("はい"), QUESTION + "\n\n" + second)
        self.assertEqual(code, 1, out)
        self.assertIn("2 件あります。上限は 1 件", out)
        self.setting("question_max", 0)
        path, text = self.new_report()
        self.assertIn("質問しない設定（question_max が 0）", text)
        code, out = self.result(self.answered("はい"), QUESTION)
        self.assertEqual(code, 1, out)
        self.assertIn("上限は 0 件", out)
        code, out = self.result(self.answered(), "なし")
        self.assertEqual(code, 0, out)

    def test_question_numbers_are_unique(self):
        code, out = self.result(self.answered("はい"), QUESTION + "\n\n" + QUESTION)
        self.assertEqual(code, 1, out)
        self.assertIn("質問の番号 Q1 が 2 回", out)

    def test_the_clue_must_be_in_the_aggregate(self):
        general = QUESTION.replace("`tool:make test`", "テストは大事")
        code, out = self.result(self.answered("はい"), general)
        self.assertEqual(code, 1, out)
        self.assertIn("手がかりの無い一般的な質問はしません", out)
        unknown = QUESTION.replace("`tool:make test`", "`tool:npm run build`")
        code, out = self.result(self.answered("はい"), unknown)
        self.assertEqual(code, 1, out)
        for clue in ("`D5`", "`.kiro/steering/testing.md`", "`make test`"):
            code, out = self.result(self.answered("はい"), QUESTION.replace("`tool:make test`", clue))
            self.assertEqual(code, 0, clue + out)

    def test_reflected_proposals_must_be_in_the_report(self):
        code, out = self.result(self.answered("はい"), QUESTION.replace("P1 を出した", "P7 を出した"))
        self.assertEqual(code, 1, out)
        self.assertIn("反映に書いた P7 が、このレポートの案にありません", out)
        older = QUESTION.replace("P1 を出した", "P1 を出した。前回の回答（2026-11-13-0856#P2）による")
        code, out = self.result(self.answered("はい"), older)
        self.assertEqual(code, 0, out)

    def test_answers_that_cannot_be_checked_are_reported_but_not_a_failure(self):
        # ログがまだ骨組みの作成まで届いていない。
        code, out = self.result(self.started() + [{"type": "user", "content": "Q1=b"}], QUESTION)
        self.assertEqual(code, 0, out)
        self.assertIn("利用者に確かめたことの引用は照合できませんでした（ログに、今回の骨組みの作成", out)
        # 振り返りのログが無い。
        os.environ["KIRO_SESSION_ID"] = "sess_nolog"
        code, out = self.result(None, QUESTION)
        self.assertEqual(code, 0, out)
        self.assertIn("振り返りのセッションのログを読めません", out)

    def test_previous_answers_are_shown_and_questions_are_not_proposals(self):
        path, filled = self.written(self.answered("はい"), QUESTION)
        done = self.check(path, filled)
        self.assertEqual(done.returncode, 0, done.stdout)
        data = aggregate.aggregate(self.root)
        self.assertEqual([(row["id"], row["answer"]) for row in data["answers"]],
                         [("%s#Q1" % path.stem, "a（見立てのとおり）")])
        self.assertIn("## 前回までの回答（1 件）", aggregate.render(data))
        self.assertIn("- `%s#Q1` 作業の終わりに、テストを自動で実行する仕組みを使うか：a（見立てのとおり）（反映：P1 を出した）"
                      % path.stem, aggregate.render(data))
        # 検査するレポート自身と、その後のレポートの答えは、前回の答えに数えない。
        self.assertEqual(aggregate.answers.previous(self.root, before=path.stem), [])
        listing = self.cli("list", script=LEDGER).stdout
        self.assertIn("%s#P1" % path.stem, listing)
        self.assertNotIn("Q1", listing)

    def test_drafts_are_not_read_for_previous_answers(self):
        path, text = self.new_report()
        path.write_text(self.ask(text, QUESTION), encoding="utf-8")   # 書きかけ（ほかの欄が残っている）
        self.assertTrue(skeleton.is_draft(path.read_text(encoding="utf-8")))
        self.assertEqual(aggregate.aggregate(self.root)["answers"], [])
