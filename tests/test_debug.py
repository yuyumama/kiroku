"""kiroku の不具合の候補（/kiroku-retro --debug）。

環境と、振り返りの中で失敗したシェル実行は、骨組みを作った時点で state に控え、検査はその控えから
同じ文面を作り直す。ログの形は Kiro CLI 3.3.0 で実測したもの（docs/retro/verification.md）を模している。
エージェントが欄を埋める操作は、文字列の置き換えで模している。
"""
import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import test_skeleton   # テストのクラスは名前で取り込まない（取り込むと、そのテストがここでも走る）
from test_skeleton import AGGREGATE, PROPOSAL, aggregate, skeleton

debug = aggregate.debug
SCRIPTS = os.path.dirname(AGGREGATE)
AGG = "python3 .kiro/skills/kiroku-retro/scripts/aggregate.py"
TRACE = ("Output:\nTraceback (most recent call last):\n  File \"aggregate.py\", line 1, in <module>\n"
         "KeyError: 'sessions'\n\nExit Code: 1")
DENIED = ('Blocked by a permission rule (shell); the user did not reject it. Source: agent-profile. '
          'Triggered by: "ls -la".\n\nExit Code: -1')
BUG = ("### B1. 要約表示が、形の違うセッションで止まる\n\n"
       "- 現象：`aggregate.py --session` が KeyError で終わった\n"
       "- 期待：読めない項目を飛ばして、要約を出す\n"
       "- 根拠：振り返りの中の実行の出力\n"
       "- 引用（出力）：index 4「KeyError: 'sessions'」\n"
       "- 再現の手がかり：`aggregate.py --since 7d --session sess_x`\n"
       "- 確度：確認済み（出力で確かめた）\n"
       "- 影響：そのセッションを読めず、ノートを書けない")


class DebugTest(test_skeleton.SkeletonBase):
    RETRO = "sess_retro"
    retro_log = test_skeleton.ReadRecordTest.retro_log
    call = test_skeleton.ReadRecordTest.call
    made = test_skeleton.ReadRecordTest.made
    same_minute = test_skeleton.SkeletonTest.same_minute

    def setUp(self):
        super().setUp()
        self.addCleanup(os.environ.pop, "KIRO_SESSION_ID", None)
        os.environ["KIRO_SESSION_ID"] = self.RETRO

    def bash(self, call_id, command, content, success=True):
        return [{"type": "tool_call", "toolCallId": call_id, "toolName": "execute_bash", "status": "approved",
                 "args": {"command": command}},
                {"type": "tool_result", "toolCallId": call_id, "success": success, "content": content}]

    def retro(self, made=True):
        """振り返りのログ。kiroku のスクリプトの失敗、権限の規則による拒否、kiroku 以外の失敗を含む。"""
        rows = [{"type": "user", "content": "/kiroku-retro --debug"}]
        rows += self.bash("a1", AGG + " --since 7d --require-agent", "Output:\n# kiroku 集約\n\nExit Code: 0")
        rows += self.bash("a2", AGG + " --since 7d --session sess_x", TRACE, success=False)   # index 3
        rows += self.bash("l1", "ls -la", DENIED, success=False)                             # index 5
        rows += self.bash("c1", "git status", "Output:\nfatal\n\nExit Code: 128")            # 拾わない
        if made:
            rows += self.made()
            rows += self.bash("a3", AGG + " --check-report x.md", TRACE, success=False)      # 骨組みの後
        return rows

    def report(self, *extra):
        for stale in (self.data / "reports").glob("*.md"):
            stale.unlink()
        done = self.cli("--since", "7d", "--new-report", *extra)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        files = sorted((self.data / "reports").glob("*.md"), key=lambda p: p.stem)
        return files[-1], files[-1].read_text(encoding="utf-8")

    def filled(self, text, bugs="なし"):
        slots = list(skeleton.SLOT.finditer(text))
        last = slots[-1]   # デバッグの節は末尾にあるので、最後の欄がそれ
        self.assertGreater(last.start(), text.index(debug.HEADING))
        text = text[:last.start()] + bugs + text[last.end():]
        text = self.propose(text, PROPOSAL).replace(skeleton.slot("確認済み／未確認"), "未確認")
        text = skeleton.SLOT.sub("なし", text)
        return text.replace("- 読めなかった範囲（未確認）：なし", "- 読めなかった範囲（未確認）：表のとおり")

    def result(self, path, text):
        done = self.check(path, text)
        return done.returncode, done.stdout

    def state(self):
        return json.loads((self.data / "state.json").read_text(encoding="utf-8"))

    def test_without_the_option_there_is_no_section(self):
        self.retro_log(self.retro())
        path, text = self.report()
        self.assertNotIn(debug.HEADING, text)
        self.assertNotIn(path.stem, self.state().get("report_debug", {}))

    def test_the_section_lists_the_environment_and_failed_calls_before_the_skeleton(self):
        (self.data / "record-errors.log").write_text(
            "2026-11-01T00:00:00Z KeyError: 'x'\n  続きの行\n2026-11-02T00:00:00Z ValueError: y\n", encoding="utf-8")
        self.retro_log(self.retro())
        path, text = self.report("--debug")
        section = text[text.index(debug.HEADING):text.index(skeleton.FACTS_INTRO)]
        self.assertLess(text.index("## 記録の不足、確認事項"), text.index(debug.HEADING))
        self.assertIn("- 環境：%s（%s）、Python %s、骨組みの版 %d、kiroku のスクリプトの指紋 `%s`" % (
            platform.system(), platform.machine(), platform.python_version(), skeleton.VERSION,
            debug.fingerprint(SCRIPTS)), section)
        self.assertIn("（kiroku のスクリプトの失敗と、権限の規則による拒否）：2 件", section)
        self.assertIn("  - index 3 `%s --since 7d --session sess_x`：終了コード 1、Traceback あり" % AGG, section)
        self.assertIn("  - index 5 `ls -la`：権限の規則で拒否", section)
        self.assertNotIn("git status", section)       # kiroku 以外の失敗は、不具合ではない
        self.assertNotIn("--check-report", section)   # 骨組みを作った後の実行は、控えに入らない
        self.assertIn("- 記録フックのエラー（骨組みを作った時点まで）：3 行（KeyError 1 件、ValueError 1 件）。"
                      "最後の行：`2026-11-02T00:00:00Z ValueError: y`", section)
        self.assertIn(path.stem, self.state()["report_debug"])

    def test_the_lines_the_script_wrote_are_checked_against_what_was_kept(self):
        self.retro_log(self.retro())
        path, text = self.report("--debug")
        filled = self.filled(text)
        code, out = self.result(path, filled)
        self.assertEqual(code, 0, out)
        # 骨組みを作った後に、記録フックのエラーが増えても、控えで比べるので一致する。
        with open(self.data / "record-errors.log", "a", encoding="utf-8") as handle:
            handle.write("2099-01-01T00:00:00Z ValueError: 後から\n")
        code, out = self.result(path, filled)
        self.assertEqual(code, 0, out)
        for changed in (filled.replace("：終了コード 1、Traceback あり", "：終了コード 0"),
                        filled.replace("  - index 5 `ls -la`：権限の規則で拒否\n", "")):
            code, out = self.result(path, changed)
            self.assertEqual(code, 1, out)
            self.assertIn("スクリプトが書いた行が、消えているか、書き換えられています", out)

    def test_suspected_bugs_are_written_in_a_checked_form(self):
        self.retro_log(self.retro())
        path, text = self.report("--debug")
        code, out = self.result(path, self.filled(text, BUG))
        self.assertEqual(code, 0, out)
        guessed = BUG.replace("- 引用（出力）：index 4「KeyError: 'sessions'」\n", "").replace(
            "確認済み（出力で確かめた）", "推定")
        code, out = self.result(path, self.filled(text, guessed))
        self.assertEqual(code, 0, out)
        cases = {
            "出力に無い引用": (BUG.replace("KeyError: 'sessions'」", "IndexError: list index」"),
                         "この振り返りのツールの出力にありません"),
            "確認済みなのに引用が無い": (BUG.replace("- 引用（出力）：index 4「KeyError: 'sessions'」\n", ""),
                              "確認済みとするなら"),
            "欄が無い": (BUG.replace("- 期待：読めない項目を飛ばして、要約を出す\n", ""), "期待 を書いてください"),
            "確度の書き方": (BUG.replace("確認済み（出力で確かめた）", "たぶん"), "確度は"),
            "短い引用": (BUG.replace("「KeyError: 'sessions'」", "「Key」"), "4 文字以上"),
            "番号の重複": (BUG + "\n\n" + BUG, "B1 が 2 回"),
            "見出しが無い": ("aggregate.py が落ちた", "`### B<番号>. `"),
        }
        for label, (bugs, message) in cases.items():
            code, out = self.result(path, self.filled(text, bugs))
            self.assertEqual(code, 1, label + out)
            self.assertIn(message, out, label)

    def test_file_quotes_in_suspected_bugs_are_checked_too(self):
        self.retro_log(self.retro())
        path, text = self.report("--debug")
        quoted = BUG + "\n- 引用（ファイル）：`.kiro/steering/testing.md`「テストは make test で実行する。」"
        code, out = self.result(path, self.filled(text, quoted))
        self.assertEqual(code, 0, out)
        wrong = BUG + "\n- 引用（ファイル）：`.kiro/steering/testing.md`「テストは pytest で実行する。」"
        code, out = self.result(path, self.filled(text, wrong))
        self.assertEqual(code, 1, out)

    def test_redoing_an_untouched_draft_without_the_option_drops_the_section(self):
        self.retro_log(self.retro())
        path, text = self.report("--debug")
        self.same_minute(path)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(aggregate.new_report(self.root, aggregate.aggregate(self.root)), 0)
        self.assertNotIn(debug.HEADING, path.read_text(encoding="utf-8"))
        self.assertNotIn(path.stem, self.state()["report_debug"])

    def test_without_the_kept_values_lines_are_not_compared_but_entries_are(self):
        self.retro_log(self.retro())
        path, text = self.report("--debug")
        state = self.state()
        state.pop("report_debug")
        (self.data / "state.json").write_text(json.dumps(state), encoding="utf-8")
        code, out = self.result(path, self.filled(text, BUG))
        self.assertEqual(code, 0, out)
        self.assertIn("控えが無いので、スクリプトが書いた行を照合していません", out)
        code, out = self.result(path, self.filled(text, BUG.replace("確認済み（出力で確かめた）", "たぶん")))
        self.assertEqual(code, 1, out)
        # スクリプトが書いた行を、エージェントが書いた行と取り違えない。
        code, out = self.result(path, self.filled(text))
        self.assertEqual(code, 0, out)

    def test_outputs_that_cannot_be_checked_are_reported_but_not_a_failure(self):
        self.retro_log(self.retro(made=False))   # ログが、骨組みの作成まで届いていない
        path, text = self.report("--debug")
        code, out = self.result(path, self.filled(text, BUG.replace("KeyError: 'sessions'」", "IndexError: x」")))
        self.assertEqual(code, 0, out)
        self.assertIn("引用（出力）は照合できませんでした", out)
        os.environ["KIRO_SESSION_ID"] = "sess_nolog"
        path, text = self.report("--debug")
        self.assertIn("この振り返りで失敗したシェル実行：出していません（振り返りのセッションのログを読めない）。", text)

    def test_the_fingerprint_ignores_line_endings(self):
        work = Path(tempfile.mkdtemp(prefix="kiroku-print-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        (work / "lf").mkdir()
        (work / "crlf").mkdir()
        (work / "lf" / "a.py").write_bytes(b"x = 1\ny = 2\n")
        (work / "crlf" / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
        self.assertEqual(debug.fingerprint(str(work / "lf")), debug.fingerprint(str(work / "crlf")))
        (work / "crlf" / "a.py").write_bytes(b"x = 1\r\ny = 3\r\n")
        self.assertNotEqual(debug.fingerprint(str(work / "lf")), debug.fingerprint(str(work / "crlf")))
        shown = subprocess.run([sys.executable, os.path.join(SCRIPTS, "debug.py"), "--fingerprint"],
                               capture_output=True, text=True)
        self.assertEqual(shown.stdout.strip(), debug.fingerprint(SCRIPTS))
        self.assertEqual(list(Path(SCRIPTS).rglob("__pycache__")), [])
