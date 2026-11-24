"""レポートの骨組み：事実の部分はスクリプトが書き、保存後にその部分が残っているかを確かめる。

エージェントが欄を埋める操作は、ここでは文字列の置き換えで模している。Kiro と LLM の実際の
書き方は確かめていない。
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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


OLD_VERSION = "0499158"   # 版2の骨組みを作っていた main（PR #13 の前）
THIRD_FORMAT_VERSION = "89731e1"   # 版3の骨組みを作っていた main（PR #13 のマージ）
FOURTH_FORMAT_VERSION = "647e60c"   # 版4の骨組みを作っていた main（PR #20 のマージ。質問を足す前）
REPO = Path(__file__).resolve().parents[1]


def old_scripts(destination, version=OLD_VERSION):
    """以前の版のスクリプトを git の履歴から取り出す。履歴が無ければ None。"""
    archive = subprocess.run(["git", "-C", str(REPO), "archive", version, "kit/.kiro/skills/kiroku-retro/scripts"],
                             capture_output=True)
    if archive.returncode != 0:
        return None
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)
    return destination / "kit/.kiro/skills/kiroku-retro/scripts"


class SkeletonBase(ReviewSandbox):
    """骨組みの試験の準備と道具。試験の本体は持たない。"""

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
                              capture_output=True, text=True, cwd=self.root)

    def new_report(self):
        done = self.cli("--since", "7d", "--new-report")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # 名前で並べる（同じ秒の `-2` は、拡張子を含めて並べると前に来てしまう）
        files = sorted((self.data / "reports").glob("*.md"), key=lambda p: p.stem)
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


class SkeletonTest(SkeletonBase):
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

    def test_slots_replaced_with_nothing_are_not_a_finished_report(self):
        """Issue #12 指摘6：目印だけを空文字に置き換えると、全欄が空でも検査を通り、最新のレポートになった。"""
        self.report("2026-11-01-0900", OLD_REPORT)
        path, text = self.new_report()
        self.assertIn("- 載せなかった案と理由：【ここに書く", text)
        self.assertIn("- 補足と、利用者に確かめたいこと：【ここに書く", text)
        emptied = skeleton.SLOT.sub("", text)
        done = self.check(path, emptied)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("欄が空のままです", done.stdout)
        for field in ("読んだセッション", "観測した型・継続方針", "様子見", "載せなかった案と理由",
                      "`.kiro/steering/testing.md`"):
            self.assertIn(field, done.stdout)
        self.assertTrue(skeleton.is_draft(emptied))
        self.assertEqual(aggregate.ledger.latest_report(self.root), "2026-11-01-0900")
        # 欄の一部だけが空でも同じ。「なし」と明記した欄は受け付ける。
        one_blank = self.fill(text).replace("## 様子見\n\nなし\n", "## 様子見\n\n")
        self.assertEqual(self.check(path, one_blank).returncode, 1)
        self.assertEqual(aggregate.ledger.latest_report(self.root), "2026-11-01-0900")
        self.assertEqual(self.check(path, self.fill(text)).returncode, 0)
        self.assertEqual(aggregate.ledger.latest_report(self.root), path.stem)
        # 表の列を消したものも空欄として扱う。
        broken = self.fill(text).replace("| あり | 確認済み | なし |", "| あり | 確認済み |", 1)
        self.assertIn("列が 4 個", self.check(path, broken).stdout)

    def test_second_format_reports_keep_their_reading(self):
        """版2のレポート（見出しの行が無い）は、版2の骨組みと検証値で比べる。書き終えたものは書き終えたまま。"""
        data = aggregate.as_version(aggregate.aggregate(self.root, aggregate.parse_bound(
            "7d", aggregate.datetime.datetime.now(aggregate.datetime.timezone.utc))), 2)
        text = skeleton.build(self.root, data, aggregate.GAP_REASONS, os.environ.get("KIRO_SESSION_ID"), version=2)
        self.assertTrue(text.startswith("<!-- kiroku-report:2 -->"))
        self.assertNotIn("補足と、利用者に確かめたいこと：", text)
        self.assertNotIn("`AGENTS.md`", text)
        path = self.data / "reports" / "2026-11-10-0900.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        old = self.fill(text)
        self.assertFalse(skeleton.is_draft(old))
        done = self.check(path, old)
        self.assertEqual(done.returncode, 0, done.stdout)
        # 版2でも、スクリプトが書いた行は照合する。
        self.assertEqual(self.check(path, old.replace("- イベント数、セッション数：1 件", "- イベント数、セッション数：3 件")
                                    ).returncode, 1)

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
        self.assertEqual(sorted((self.data / "reports").glob("*.md")), [path])
        started = text.replace(skeleton.slot("無ければ「なし」"), "なし", 1)
        path.write_text(started, encoding="utf-8")
        again = self.cli("--since", "7d", "--new-report")
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertEqual(path.read_text(encoding="utf-8"), started)   # 書き始めたものは残る
        if path.stem in again.stdout:   # 同じ分のうちに実行できた場合
            self.assertIn("上書きせず、別の名前で作りました", again.stdout)
        self.assertEqual(len(list((self.data / "reports").glob("*.md"))), 2)

    def same_minute(self, path):
        """次の骨組みの名前の候補を、既存の骨組みと同じ分から始める。"""
        original = aggregate.report_names
        self.addCleanup(setattr, aggregate, "report_names", original)
        aggregate.report_names = lambda moment: [path.stem] + original(moment)[1:]

    def make_in_process(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return aggregate.new_report(self.root, aggregate.aggregate(self.root))

    def test_new_report_keeps_edits_even_when_the_slot_count_happens_to_match(self):
        """Issue #12 指摘4：2欄を書き、表が1行減ると、目印の数が偶然そろって上書きしていた。"""
        self.write(".kiro/steering/release.md", "リリース手順\n")
        path, text = self.new_report()
        state = skeleton.slot("確認済み／未確認")
        role = skeleton.slot("役割と読み込みの条件")
        kept = "| プロジェクト内 | `.kiro/steering/testing.md` | あり | %s | %s |" % (state, role)
        edited = text.replace(kept, "| プロジェクト内 | `.kiro/steering/testing.md` | あり | 確認済み | テストの手順 |")
        path.write_text(edited, encoding="utf-8")
        os.remove(os.path.join(self.root, ".kiro/steering/release.md"))
        # 同じ分のうちに作り直す状況を、時刻に頼らずに作る。
        self.same_minute(path)
        self.assertEqual(self.make_in_process(), 0)
        self.assertEqual(path.read_text(encoding="utf-8"), edited)
        made = sorted((self.data / "reports").glob("*.md"))
        self.assertEqual(len(made), 2)
        self.assertNotIn("release.md", made[-1].read_text(encoding="utf-8"))
        self.assertGreater(made[-1].stem, path.stem)   # 新しいほうが最新として並ぶ

    def test_two_retros_in_the_same_minute_get_separate_files(self):
        path, _ = self.new_report()
        self.addCleanup(os.environ.pop, "KIRO_SESSION_ID", None)
        os.environ["KIRO_SESSION_ID"] = "sess_other_retro"
        self.same_minute(path)
        self.assertEqual(self.make_in_process(), 0)
        self.assertEqual(len(list((self.data / "reports").glob("*.md"))), 2)   # 他の振り返りの骨組みは使わない

    def test_same_proposal_number_twice_fails_the_check_and_cannot_be_adopted(self):
        """Issue #12 指摘3：プロジェクト内とユーザー共通の両方に P1 があると、採否がどの案か決まらない。"""
        path, text = self.new_report()
        user = "### P1. ユーザー共通の手順を直す\n\n- 対象の型：`tool:make test`\n- 変更の内容：…"
        marker = "## ハーネスへの改善案（ユーザー共通）\n\n"
        filled = self.fill(text)
        start = filled.index(marker) + len(marker)
        body = filled[:start] + user + filled[filled.index("\n", start):]
        done = self.check(path, body)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("案の番号 P1 が 2 回", done.stdout)
        self.assertIn("テストの実行方法を直す／ユーザー共通の手順を直す", done.stdout)
        # 検査を通さずに保存されたレポート（旧版を含む）でも、台帳は判断を記録しない。
        adopt = self.cli("adopt", "P1", script=LEDGER)
        self.assertEqual(adopt.returncode, 1, adopt.stdout)
        self.assertIn("記録しません", adopt.stdout)
        self.assertFalse((self.data / "ledger.jsonl").exists())
        # 以前の版が記録してしまった判断は、どちらの案のものか推測して表示しない。
        self.decide(path.stem + "#P1", "adopted", 1, target="tool:make test")
        listing = self.cli("list", script=LEDGER).stdout
        self.assertEqual(listing.count("番号が重複"), 2, listing)
        self.assertNotIn("[採用]", listing)
        # PR #13 のレビュー指摘3：見直し用の要約と集約の追跡でも、この判断を数えない。
        summary = self.cli(script=os.path.join(os.path.dirname(AGGREGATE), "review.py")).stdout
        self.assertNotIn("| tool |", summary)   # 以前は「採用 2」と数えていた
        self.assertIn("番号が重複した案：2 件（1 個の番号）", summary)
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["adopted"], [])
        self.assertEqual(data["ambiguous_decisions"], [path.stem + "#P1"])
        self.assertIn("追跡から外しています", aggregate.render(data))
        # 通し番号に直せば検査を通り、記録できる。
        fixed = body.replace("### P1. ユーザー共通", "### P2. ユーザー共通")
        self.assertEqual(self.check(path, fixed).returncode, 0)
        self.assertEqual(self.cli("adopt", "P2", script=LEDGER).returncode, 0)

    def test_report_checked_by_the_previous_version_still_passes(self):
        """PR #13 のレビュー指摘1：以前の版で検査を通った版2のレポートが、更新しただけで不一致になった。"""
        import tempfile
        import shutil
        work = Path(tempfile.mkdtemp(prefix="kiroku-old-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        scripts = old_scripts(work)
        if scripts is None:
            self.skipTest("以前の版（%s）が git の履歴にありません" % OLD_VERSION)
        self.write("app/AGENTS.md", "app の決まり\n")
        # 以前の版が書いたノート（要約表示で見せた期間の控えが無い）。PR #13 の再レビュー指摘1。
        self.note("sess_live", [], shown=False)
        # 期間内の1回の指摘と、期間の外の同じ話題の継続方針。版2は候補にした（再々レビュー指摘1）。
        self.log("sess_in", ["報告は結論から書いて"], days_ago=1)
        self.log("sess_out", ["今後も報告は必ず結論から書くこと"], days_ago=20)
        self.note("sess_in", [{"kind": "correction", "topic": "報告順", "summary": "結論から",
                               "quote": "報告は結論から書いて", "index": 0, "context": "未確認"}], shown=False)
        self.note("sess_out", [{"kind": "standing_policy", "topic": "報告順", "summary": "結論から",
                                "quote": "今後も報告は必ず結論から書くこと", "index": 0, "context": "未確認"}],
                  shown=False)
        future = time.time() + 3600
        for name in ("sess_live", "sess_in", "sess_out"):
            os.utime(self.data / "notes" / (name + ".json"), (future, future))
        old = self.cli("--since", "7d", "--new-report", script=str(scripts / "aggregate.py"))
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        path = sorted((self.data / "reports").glob("*.md"))[-1]
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- kiroku-report:2 -->"))
        self.assertIn('"topic:報告順"', text)
        # 版2の検査は、案の番号の重複も、目印を消しただけの空欄も見なかった。
        user = "### P1. ユーザー共通の手順\n\n- 対象の型：`tool:make test`"
        filled = skeleton.SLOT.sub("なし", self.propose(text, PROPOSAL))
        marker = "## ハーネスへの改善案（ユーザー共通）\n\n"
        start = filled.index(marker) + len(marker)
        filled = filled[:start] + user + filled[filled.index("\n", start):]
        filled = filled.replace("## 様子見\n\nなし\n", "## 様子見\n\n")
        path.write_text(filled, encoding="utf-8")
        before = self.cli("--check-report", str(path), script=str(scripts / "aggregate.py"))
        self.assertEqual(before.returncode, 0, before.stdout)
        self.assertIn("- 未読のセッション：0 件", filled)
        self.assertFalse(skeleton.is_draft(filled))   # 書き終えたレポートは、更新しても書き終えたまま
        after = self.cli("--check-report", str(path))
        self.assertEqual(after.returncode, 0, after.stdout)
        # 振り返りで読むセッションは、今の判定で決める（以前の版のノートは読み直す）。
        self.assertIn("sess_live", aggregate.aggregate(self.root)["reading_plan"]["read"])
        # 版3では、期間の外の継続方針で候補にしない。
        self.assertNotIn("topic:報告順", aggregate.aggregate(self.root, aggregate.parse_bound(
            "7d", aggregate.datetime.datetime.now(aggregate.datetime.timezone.utc)))["report_facts"]["targets"])
        self.assertEqual(list(Path(self.root, ".kiro").rglob("__pycache__")), [])

    def test_reports_without_the_mark_are_checked_by_the_previous_rules(self):
        facts = aggregate.as_version(aggregate.aggregate(self.root), 0)["report_facts"]
        path = self.data / "reports" / "2026-11-10-0900.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "### P1. 手順\n- 対象の型：`tool:make test`\n\n```kiroku-facts\n%s\n```\n" % json.dumps(facts)
        self.assertEqual(self.check(path, body).returncode, 0)

    def test_same_records_give_the_same_skeleton(self):
        first = aggregate.aggregate(self.root)
        second = aggregate.aggregate(self.root, until=None)
        self.assertEqual(skeleton.build(self.root, first), skeleton.build(self.root, second))
        self.assertEqual(list(Path(self.root, ".kiro").rglob("__pycache__")), [])

    def later(self, minutes=1):
        """record.py が record-errors.log の行の先頭に書く形の、少し後の時刻。"""
        moment = aggregate.datetime.datetime.now(aggregate.datetime.timezone.utc)
        return (moment + aggregate.datetime.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")

    def test_hook_errors_after_the_skeleton_do_not_change_the_lines_the_script_wrote(self):
        """骨組みを作った後に記録フックのエラーが増えても、スクリプトが書いた行を不一致にしない。

        振り返りの最中も、振り返り自身のシェル実行で記録フックは動く。検査までの間に
        record-errors.log が増えると、骨組みの「記録フックのエラー：N 行」と数が合わなくなっていた。
        """
        log = self.data / "record-errors.log"
        log.write_text("2026-11-01T00:00:00Z KeyError: 'x'\n", encoding="utf-8")
        path, text = self.new_report()
        self.assertIn("- 記録フックのエラー：1 行", text)
        filled = self.fill(text)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("%s ValueError: 骨組みの後\n" % self.later())
        done = self.check(path, filled)
        self.assertEqual(done.returncode, 0, done.stdout)
        # 集約そのものは、今までどおり今の行数を出す。
        self.assertEqual(aggregate.aggregate(self.root)["coverage"]["record_errors"], 2)

    def test_reports_without_the_kept_count_count_lines_up_to_when_they_were_made(self):
        """行数の控えが無いレポート（以前の版で作ったもの）は、作った時刻までの行だけを数える。

        例外の文面が改行を含むと、1件が複数の行になる。時刻の無い行は、直前の行と同じ件として扱う。
        """
        log = self.data / "record-errors.log"
        log.write_text("2026-11-01T00:00:00Z KeyError: 'x'\n  続きの行\n", encoding="utf-8")
        path, text = self.new_report()
        self.assertIn("- 記録フックのエラー：2 行", text)
        state_path = self.data / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("report_record_errors", None)   # 以前の版は控えなかった
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("%s ValueError: 骨組みの後\n  続きの行\n" % self.later())
        done = self.check(path, self.fill(text))
        self.assertEqual(done.returncode, 0, done.stdout)


ONE_STEP = ("### P1. テストの実行を終了時の hook にする\n\n"
            "- 観点：V3、V5\n- 段：1→3\n"
            "- 手がかり：`D2:.kiro/steering/build.md:make build`、`tool:make test`\n"
            "- 価値：テストの実行のし忘れと、コマンドの言い直しが無くなる\n"
            "- 対象のファイル：`.kiro/hooks/test.json`（新規）\n"
            "- 小さく試す方法：1週間だけ有効にする\n"
            "- 確かめ方：次の振り返りで `tool:make test` が0回になる\n"
            "- 不要になる条件：CI が同じ検査をするようになったとき")


class OneStepTest(SkeletonBase):
    """一歩先の改善案（版4）。文章の意味と価値は判定せず、形と手がかりの実在だけを確かめる。"""

    def setUp(self):
        super().setUp()
        self.write(".kiro/steering/build.md", "- ビルドは `make build` で行う。\n")   # Makefile は無い（D2）

    def one_step(self, text, body):
        marker = "## 一歩先の改善案\n\n"
        start = text.index(marker) + len(marker)
        return text[:start] + body + text[text.index("\n", start):]

    def written(self, body):
        path, text = self.new_report()
        filled = self.one_step(text, body)
        filled = skeleton.SLOT.sub("なし", self.propose(filled, PROPOSAL.replace("### P1.", "### P2.")))
        return path, filled.replace(skeleton.slot("確認済み／未確認"), "確認済み")

    def test_fifth_format_puts_questions_and_one_step_first_and_diagnostics_after_the_table(self):
        path, text = self.new_report()
        self.assertTrue(text.startswith("<!-- kiroku-report:5 -->"))
        order = [text.index(h) for h in ("- 読めなかった範囲（未確認）：", "## 利用者に確かめたこと", "## 一歩先の改善案",
                                          "## 観測した型・継続方針", "## 様子見", "## 比べたハーネス",
                                          "## ハーネスの診断", "## 記録の不足、確認事項")]
        self.assertEqual(order, sorted(order))
        self.assertIn("- `D2:.kiro/steering/build.md:make build`：Makefile が無い。", text)
        self.assertIn("観測した型・診断値より一段上の案を、件数の上限なし（あれば全部）。", text)
        self.assertIn('"diagnostics": [', text)

    def test_a_well_formed_one_step_proposal_passes_and_is_a_proposal_in_the_ledger(self):
        path, body = self.written(ONE_STEP)
        done = self.check(path, body)
        self.assertEqual(done.returncode, 0, done.stdout)
        listing = self.cli("list", script=LEDGER).stdout
        self.assertIn("%s#P1  [未判断]  テストの実行を終了時の hook にする" % path.stem, listing)
        self.assertIn("%s#P2  [未判断]  テストの実行方法を直す" % path.stem, listing)
        first = aggregate.ledger.proposals(self.root)[0]
        self.assertEqual((first["files"], first["lenses"]), ([".kiro/hooks/test.json"], ["V3", "V5"]))
        self.assertEqual(self.cli("adopt", "P1", script=LEDGER).returncode, 0)
        summary = self.cli(script=os.path.join(os.path.dirname(AGGREGATE), "review.py")).stdout
        self.assertIn("| V3 | 1 | 0 | 0 |", summary)
        self.assertIn("| V5 | 1 | 0 | 0 |", summary)

    def test_malformed_one_step_proposals_fail(self):
        cases = {
            "観点が無い": ONE_STEP.replace("- 観点：V3、V5\n", ""),
            "観点が範囲外": ONE_STEP.replace("V3、V5", "V9"),
            "段が上がらない": ONE_STEP.replace("1→3", "2→2"),
            "手がかりが一般論": ONE_STEP.replace("`D2:.kiro/steering/build.md:make build`、`tool:make test`",
                                          "テストは大事"),
            "集約に無い手がかり": ONE_STEP.replace("`tool:make test`", "`tool:npm run build`"),
            "確かめ方が無い": ONE_STEP.replace("- 確かめ方：次の振り返りで `tool:make test` が0回になる\n", ""),
            "見出しが無い": ONE_STEP.replace("### P1. ", ""),
        }
        messages = {"観点が無い": "観点を V1〜V8", "段が上がらない": "案の段を観測より上", "手がかりが一般論": "一般論は",
                    "集約に無い手がかり": "集約に無い手がかり：tool:npm run build", "確かめ方が無い": "確かめ方",
                    "見出しが無い": "`### P<番号>. `"}
        for label, body in cases.items():
            path, written = self.written(body)
            done = self.check(path, written)
            self.assertEqual(done.returncode, 1, label + done.stdout)
            if label in messages:
                self.assertIn(messages[label], done.stdout, label)

    def test_without_a_limit_every_one_step_proposal_is_written(self):
        """件数の上限の既定は無い（2026-11-24 の決定）。あれば全部書き、形は1件ずつ確かめる。"""
        more = "\n\n".join(ONE_STEP.replace("### P1. テストの実行を終了時の hook にする", "### P%d. 案 %d" % (n, n))
                           for n in (1, 3, 4, 5))
        path, body = self.written(more)
        done = self.check(path, body)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertNotIn("上限は", done.stdout)
        path, body = self.written(more.replace("### P5. 案 5\n\n- 観点：V3、V5", "### P5. 案 5\n\n- 観点：V9"))
        done = self.check(path, body)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("P5：観点を V1〜V8", done.stdout)

    def test_the_number_of_one_step_proposals_is_capped_by_the_setting(self):
        config = json.loads((self.data.parent / "config.json").read_text(encoding="utf-8"))
        config["one_step_max"] = 1
        (self.data.parent / "config.json").write_text(json.dumps(config), encoding="utf-8")
        second = ONE_STEP.replace("### P1. テストの実行を終了時の hook にする", "### P3. もう1つの案")
        path, body = self.written(ONE_STEP + "\n\n" + second)
        done = self.check(path, body)
        self.assertEqual(done.returncode, 1)
        self.assertIn("2 件あります。上限は 1 件", done.stdout)
        self.assertIn("最大 1 件", self.new_report()[1])

    def test_fourth_format_report_from_the_previous_version_still_passes(self):
        """版4（質問を足す前の main）で作り、検査を通ったレポートが、更新しただけで不一致にならない。"""
        work = Path(tempfile.mkdtemp(prefix="kiroku-old-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        scripts = old_scripts(work, FOURTH_FORMAT_VERSION)
        if scripts is None:
            self.skipTest("以前の版（%s）が git の履歴にありません" % FOURTH_FORMAT_VERSION)
        old = self.cli("--since", "7d", "--new-report", script=str(scripts / "aggregate.py"))
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        path = sorted((self.data / "reports").glob("*.md"))[-1]
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- kiroku-report:4 -->"))
        self.assertNotIn("## 利用者に確かめたこと", text)
        filled = skeleton.SLOT.sub("なし", self.propose(text, PROPOSAL)).replace(
            skeleton.slot("確認済み／未確認"), "確認済み")
        path.write_text(filled, encoding="utf-8")
        before = self.cli("--check-report", str(path), script=str(scripts / "aggregate.py"))
        self.assertEqual(before.returncode, 0, before.stdout)
        after = self.cli("--check-report", str(path))
        self.assertEqual(after.returncode, 0, after.stdout)
        self.assertFalse(skeleton.is_draft(filled))   # 質問の節が無くても、書き終えたまま

    def test_third_format_report_from_the_previous_version_still_passes(self):
        """版3（PR #13 の main）で作り、検査を通ったレポートが、更新しただけで不一致にならない。"""
        work = Path(tempfile.mkdtemp(prefix="kiroku-old-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        scripts = old_scripts(work, THIRD_FORMAT_VERSION)
        if scripts is None:
            self.skipTest("以前の版（%s）が git の履歴にありません" % THIRD_FORMAT_VERSION)
        old = self.cli("--since", "7d", "--new-report", script=str(scripts / "aggregate.py"))
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        path = sorted((self.data / "reports").glob("*.md"))[-1]
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- kiroku-report:3 -->"))
        filled = skeleton.SLOT.sub("なし", self.propose(text, PROPOSAL))
        path.write_text(filled, encoding="utf-8")
        before = self.cli("--check-report", str(path), script=str(scripts / "aggregate.py"))
        self.assertEqual(before.returncode, 0, before.stdout)
        after = self.cli("--check-report", str(path))
        self.assertEqual(after.returncode, 0, after.stdout)


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
        path, _ = self.new_report()
        data = aggregate.as_version(aggregate.aggregate(self.root, aggregate.parse_bound(
            "7d", aggregate.datetime.datetime.now(aggregate.datetime.timezone.utc))), 1)
        text = skeleton.build(self.root, data, aggregate.GAP_REASONS, version=2)
        old = self.fill(text).replace("<!-- kiroku-report:2 -->", "<!-- kiroku-report:1 -->").replace(
            "- 引用（ファイル）：`.kiro/steering/testing.md`「テストは make test で実行する。」\n", "")
        self.assertEqual(self.check(path, old).returncode, 0)



class ReadRecordTest(SkeletonTest):
    """表の「確認済み」を、振り返り自身のセッションログの読み取りと照合する。

    ログの形は Kiro CLI 3.3.0 で実測したもの。ここでは模している。
    """
    RETRO = "sess_retro"

    def setUp(self):
        super().setUp()
        self.addCleanup(os.environ.pop, "KIRO_SESSION_ID", None)
        os.environ["KIRO_SESSION_ID"] = self.RETRO
        self.write("AGENTS.md", "共通の決まり\n")

    def retro_log(self, rows):
        directory = self.home / "sessions" / "0123456789abcdef" / self.RETRO
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "session.json").write_text(json.dumps(
            {"id": self.RETRO, "workspacePaths": [self.root], "agentMode": "kiroku-retro"}), encoding="utf-8")
        with open(directory / "messages.jsonl", "w", encoding="utf-8") as handle:
            for index, payload in enumerate(rows):
                handle.write(json.dumps({"id": str(index), "timestamp": "2026-11-17T00:00:%02dZ" % index,
                                         "payload": payload}, ensure_ascii=False) + "\n")

    def call(self, call_id, name, args, success=True, meta=None):
        return [{"type": "tool_call", "toolCallId": call_id, "toolName": name, "status": "approved",
                 "args": args, "_meta": meta or {}},
                {"type": "tool_result", "toolCallId": call_id, "success": success, "content": "…"}]

    def read(self, call_id, relative, success=True):
        return self.call(call_id, "read_file", {"path": os.path.join(self.root, relative),
                                                "offset": None, "limit": None}, success)

    def made(self):
        return self.call("new", "execute_bash", {"command": "python3 .kiro/skills/kiroku-retro/scripts/"
                                                              "aggregate.py --since 7d --new-report --require-agent"})

    def confirm(self, text, *names):
        for name in names:
            text = text.replace("| `%s` | あり | %s |" % (name, skeleton.slot("確認済み／未確認")),
                                "| `%s` | あり | 確認済み |" % name)
        return text

    def checked(self, rows, *names):
        self.retro_log(rows)
        for stale in (self.data / "reports").glob("*.md"):
            stale.unlink()
        path, text = self.new_report()
        self.assertIn("<!-- kiroku-session:%s -->" % self.RETRO, text)
        body = self.fill(self.confirm(text, *names), state="未確認").replace(
            "- 読めなかった範囲（未確認）：なし", "- 読めなかった範囲（未確認）：表のとおり")
        done = self.check(path, body)
        return done.returncode, done.stdout

    def test_files_read_in_this_retro_can_be_confirmed(self):
        rows = self.made() + self.read("r1", ".kiro/steering/testing.md")
        code, out = self.checked(rows, ".kiro/steering/testing.md")
        self.assertEqual(code, 0, out)

    def test_confirmed_without_a_read_fails(self):
        code, out = self.checked(self.made(), ".kiro/steering/testing.md")
        self.assertEqual(code, 1)
        self.assertIn("本文を開いた記録がありません", out)

    def test_failed_reads_listings_and_searches_are_not_reads(self):
        rows = self.made() + self.read("r1", ".kiro/steering/testing.md", success=False)
        rows += self.call("l1", "list_directory", {"path": os.path.join(self.root, ".kiro/steering")})
        rows += self.call("g1", "grep_search", {"query": "make test", "includePattern": ".kiro/steering/testing.md"})
        code, out = self.checked(rows, ".kiro/steering/testing.md")
        self.assertEqual(code, 1, out)

    def test_skill_loading_and_automatic_context_count_as_reads(self):
        skill = Path(self.root, ".kiro/skills/add-category/SKILL.md").as_uri()
        rows = self.made() + self.call("s1", "load_resource", {"id": "skill::add-category"},
                                       meta={"kiro": {"disclosedContext": {"uri": skill}}})
        rows.append({"type": "steering_inclusion",
                     "documents": [Path(self.root, ".kiro/steering/testing.md").as_uri()]})
        code, out = self.checked(rows, ".kiro/skills/add-category/SKILL.md", ".kiro/steering/testing.md")
        self.assertEqual(code, 0, out)

    def test_cannot_check_is_reported_but_not_a_failure(self):
        """ログがまだ今の実行まで届いていない、読めない場合は、照合できないと知らせるだけにする。"""
        code, out = self.checked(self.read("r1", ".kiro/steering/release.md"), ".kiro/steering/testing.md")
        self.assertEqual(code, 0, out)
        self.assertIn("照合はできませんでした", out)
        os.environ["KIRO_SESSION_ID"] = "sess_nolog"
        for stale in (self.data / "reports").glob("*.md"):
            stale.unlink()
        path, text = self.new_report()
        done = self.check(path, self.fill(self.confirm(text, ".kiro/steering/testing.md"), state="未確認").replace(
            "- 読めなかった範囲（未確認）：なし", "- 読めなかった範囲（未確認）：表のとおり"))
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("ログを読めません", done.stdout)

    def test_session_line_cannot_be_changed_to_another_session(self):
        rows = self.made() + self.read("r1", ".kiro/steering/testing.md")
        self.retro_log(rows)
        path, text = self.new_report()
        body = self.fill(text).replace(self.RETRO, "sess_other")
        done = self.check(path, body)
        self.assertEqual(done.returncode, 1)
        self.assertIn("書き換えられています", done.stdout)


class SinceLastTest(SkeletonBase):
    """期間の既定「前回の振り返りの後から」（--since last）。経過した日数で、読む範囲を変えない。"""

    def finished(self, name, since, until, unread):
        facts = {"since": since, "until": until, "unread": unread}
        path = self.data / "reports" / (name + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 振り返り\n\n```kiroku-facts\n%s\n```\n" % json.dumps(facts), encoding="utf-8")
        return path

    def test_reads_from_the_end_of_the_last_finished_report(self):
        self.assertIsNone(aggregate.since_last(self.root))   # レポートが無ければ、すべて
        self.finished("2026-11-01-0900", "2026-10-25T00:00:00+00:00", "2026-11-01T00:00:00+00:00", 0)
        self.finished("2026-11-10-0900", "2026-11-01T00:00:00+00:00", "2026-11-10T00:00:00+00:00", 0)
        self.assertEqual(aggregate.since_last(self.root).isoformat(), "2026-11-10T00:00:00+00:00")
        # 書きかけの骨組みは、前回の振り返りに数えない。
        self.new_report()
        self.assertEqual(aggregate.since_last(self.root).isoformat(), "2026-11-10T00:00:00+00:00")

    def test_unread_left_last_time_are_read_again(self):
        self.finished("2026-11-10-0900", "2026-11-01T00:00:00+00:00", "2026-11-10T00:00:00+00:00", 2)
        self.assertEqual(aggregate.since_last(self.root).isoformat(), "2026-11-01T00:00:00+00:00")
        self.finished("2026-11-12-0900", None, "2026-11-12T00:00:00+00:00", 1)
        self.assertIsNone(aggregate.since_last(self.root))   # 前回が「すべて」から読み、読み残しがある

    def test_the_command_line_uses_it(self):
        self.finished("2026-11-10-0900", "2026-11-01T00:00:00+00:00", "2026-11-10T00:00:00+00:00", 0)
        done = self.cli("--since", "last")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("- 期間：2026-11-10T00:00:00+00:00 〜 ", done.stdout)
        done = self.cli("--since", "last", "--new-report")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        made = sorted((self.data / "reports").glob("*.md"), key=lambda p: p.stem)[-1].read_text(encoding="utf-8")
        self.assertIn('"since": "2026-11-10T00:00:00+00:00"', made)
