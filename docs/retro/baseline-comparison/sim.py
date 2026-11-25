"""デモの5セッションを、Kiro のセッションログの形（verification.md で実測した形）で作り、
kiroku の記録フック（record.py）を実際に動かして記録を作る。コード・テスト・git の操作は本物を実行する。

使い方: KIROKU=<kiroku のリポジトリ> python3 sim.py base kirohome（このフォルダを一時ディレクトリに写してから実行する）
"""
import datetime
import json
import os
import shutil
import subprocess
import sys

KIROKU = os.environ["KIROKU"]   # kiroku のリポジトリの場所
ROOT = os.path.abspath(sys.argv[1])
HOME = os.path.abspath(sys.argv[2])
HASH = "0123456789abcdef"
SCRIPTS = os.path.join(ROOT, ".kiro/skills/kiroku-retro/scripts")


def sh(cmd, check=True):
    done = subprocess.run(cmd, cwd=ROOT, shell=True, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit("失敗: %s\n%s%s" % (cmd, done.stdout, done.stderr))
    return done


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def hook(payload, at):
    """record.py を、時刻だけ差し替えて動かす（フックと同じ標準入力の JSON）。"""
    code = ("import sys,json; sys.path.insert(0, %r); sys.dont_write_bytecode=True; import record; "
            "record.now=lambda: %r; record.main()" % (SCRIPTS, iso(at)))
    payload = dict(payload, cwd=ROOT)
    done = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, input=json.dumps(payload),
                          capture_output=True, text=True)
    if done.returncode != 0 or done.stdout:
        raise SystemExit("record.py: %s %s" % (done.stdout, done.stderr))


class Session:
    def __init__(self, sid, agent, start):
        self.sid, self.agent, self.t, self.rows, self.n, self.turn = sid, agent, start, [], 0, 0
        self.dir = os.path.join(HOME, "sessions", HASH, sid)
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "session.json"), "w") as f:
            json.dump({"id": sid, "workspacePaths": [ROOT], "agentMode": agent}, f)

    def tick(self, seconds=20):
        self.t += datetime.timedelta(seconds=seconds)
        return self.t

    def row(self, payload):
        self.rows.append({"id": str(len(self.rows)), "timestamp": iso(self.tick(5)), "payload": payload})

    def base(self):
        return {"session_id": self.sid}

    def user(self, text):
        if self.turn:
            hook(dict(self.base(), hook_event_name="Stop"), self.tick())
        self.turn += 1
        hook(dict(self.base(), hook_event_name="UserPromptSubmit", prompt=text), self.tick())
        self.row({"type": "user", "content": text})
        self.row({"type": "turn_start", "turnId": "t%d" % self.turn})

    def say(self, text):
        self.row({"type": "assistant", "content": text})

    def _ids(self):
        self.n += 1
        return "tooluse_%s_%d" % (self.sid[-2:], self.n), "e%d" % self.turn

    def shell(self, command, answer="accept", blocked=False, expect=0):
        call, execution = self._ids()
        tool_input = {"command": command}
        pre = dict(self.base(), hook_event_name="PreToolUse", tool_name="execute_bash", tool_input=tool_input)
        hook(pre, self.tick())
        meta = {"kiro": {"agentMode": self.agent}}
        common = {"toolCallId": call, "executionId": execution, "turnId": "t%d" % self.turn}
        if blocked:
            # guard-shell が止めた形（verification.md の確認1）。PostToolUse は来ない。
            self.row(dict(common, type="ContextualHookInvoked", hookId="h1", operationId="op%d" % self.n,
                          name="guard-shell", hookActionType="runCommand", status="completed"))
            self.row(dict(common, type="tool_call", toolName="execute_bash", status="denied", kind="execute",
                          args={"command": command, "cwd": ROOT, "description": None, "timeout": None,
                                "run_in_background": False}, _meta=meta, modelToolUseId=call))
            self.row(dict(common, type="tool_result", content="{}", success=False, _meta=meta))
            return None
        if answer:
            self.row(dict(common, type="interaction_resolved", outcome="selected", selectedOption=answer,
                          modelToolUseId=call, consentRound=1))
        done = sh(command, check=False)
        if expect is not None and (done.returncode == 0) != (expect == 0):
            raise SystemExit("想定と違う終了コード %d: %s\n%s%s" % (done.returncode, command, done.stdout, done.stderr))
        output = (done.stdout + done.stderr).strip()
        content = "Output:\n%s\n\nExit Code: %d" % (output, done.returncode)
        self.row(dict(common, type="tool_call", toolName="execute_bash",
                      status="completed" if done.returncode == 0 else "failed",
                      args={"command": command, "run_in_background": False}, _meta=meta, modelToolUseId=call))
        self.row(dict(common, type="tool_result", content=content, success=done.returncode == 0, _meta=meta))
        hook(dict(pre, hook_event_name="PostToolUse", tool_response=content), self.tick())
        return done

    def write(self, path, text=None, replace=None):
        full = os.path.join(ROOT, path)
        if replace:
            with open(full, encoding="utf-8") as f:
                body = f.read()
            old, new = replace
            if old not in body:
                raise SystemExit("置き換え元が無い: %s %r" % (path, old))
            body = body.replace(old, new, 1)
            tool, args = "str_replace", {"path": path, "oldStr": old, "newStr": new}
        else:
            body = text
            tool, args = "fs_write", {"path": path, "command": "create", "content": text}
        with open(full, "w", encoding="utf-8") as f:
            f.write(body)
        call, execution = self._ids()
        common = {"toolCallId": call, "executionId": execution, "turnId": "t%d" % self.turn}
        meta = {"kiro": {"agentMode": self.agent}}
        self.row(dict(common, type="tool_call", toolName=tool, status="completed", args=args, _meta=meta))
        self.row(dict(common, type="tool_result", content="Successfully wrote %s" % path, success=True, _meta=meta))
        hook(dict(self.base(), hook_event_name="PostToolUse", tool_name=tool, tool_input=args,
                  tool_response="ok"), self.tick())

    def end(self):
        hook(dict(self.base(), hook_event_name="Stop"), self.tick())
        with open(os.path.join(self.dir, "messages.jsonl"), "w", encoding="utf-8") as f:
            for r in self.rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def at(day, hour):
    return datetime.datetime(2026, 11, day, hour, 0, tzinfo=datetime.timezone.utc)


def setup():
    shutil.copytree(os.path.join(KIROKU, "examples/demo"), ROOT)
    config = os.path.join(ROOT, ".kiro/kiroku/config.json")
    with open(config, encoding="utf-8") as f:
        kiroku_config = f.read()
    shutil.rmtree(os.path.join(ROOT, ".kiro/kiroku"))
    sh("git init -q -b main && git config user.email demo@example.com && git config user.name demo")
    sh("git add -A && git commit -q -m init")
    bare = ROOT + "-origin.git"
    sh("git init -q --bare %s && git remote add origin %s && git push -q origin main" % (bare, bare))
    sh("%s %s/install.py %s" % (sys.executable, KIROKU, ROOT))
    with open(config, "w", encoding="utf-8") as f:
        f.write(kiroku_config)
    # kiroku の導入物は、比較の A・B から外せるよう、履歴に入れない。
    sh("git status --porcelain > /dev/null")


def main():
    setup()

    # --- セッション1（11-18）
    s = Session("sess_s1", "kiro_default", at(18, 1))
    s.user("テストを実行して")
    s.shell("make test", expect=2)
    s.say("`make test` は Makefile が無いため失敗しました。README にある `python3 run_tests.py` で実行します。")
    s.shell("python3 run_tests.py")
    s.say("テストを実行しました。まず `.kiro/steering/testing.md` の決まりに従って `make test` を試しましたが、"
          "このリポジトリには Makefile が無いため `No rule to make target 'test'` で失敗しました。次に README の"
          "手順にある `python3 run_tests.py` を使いました。これは unittest でテストを集めて実行し、結果を "
          "`reports/junit.xml` にも書き出すスクリプトです。結果として、11件のテストがすべて成功しました。"
          "内訳は models、storage、report、cli の各モジュールのテストです。失敗はありませんでした。")
    s.user("支出に「医療」のカテゴリを足して")
    s.write("kakeibo/models.py", replace=('"娯楽", "その他"', '"娯楽", "医療", "その他"'))
    s.write("tests/test_models.py", replace=("class ", "class MedicalTest(unittest.TestCase):\n"
                                              "    def test_medical(self):\n"
                                              "        from kakeibo.models import make_entry\n"
                                              "        self.assertEqual(make_entry('2026-11-03', '医療', 1500).category, '医療')\n\n\nclass "))
    s.shell("python3 run_tests.py")
    s.say("add-category の skill の手順に沿って、`kakeibo/models.py` の `CATEGORIES` の「その他」の前に「医療」を"
          "足しました。「その他」は最後のままです。過去の記録への影響はありません。テストとして "
          "`tests/test_models.py` に、医療で記録できることを確かめるテストを1件足しました。集計の順は "
          "`CATEGORIES` の順なので、医療は娯楽の次に出ます。テストを実行し、12件すべて通りました。")
    s.user("説明が長い。結論から書いて")
    s.say("医療カテゴリを追加し、テスト12件が通りました。")
    s.user("コミットして。コミットメッセージは日本語で書いて")
    s.shell("git add kakeibo tests && git commit -q -m '医療カテゴリを追加'")
    s.say("コミットしました。")
    s.user("今回だけ、比較する月は2026-10にして集計を見せて。既定値にはしないで")
    s.shell("python3 -m kakeibo report 2026-10")
    s.say("2026-10 の支出はありません（記録がまだ無いため）。")
    s.end()

    # --- セッション2（11-19）
    s = Session("sess_s2", "kiro_default", at(19, 2))
    s.user("カテゴリごとの年間の合計を出す year コマンドを足して")
    s.write("kakeibo/report.py", replace=('def format_report', 'def yearly_totals(entries, year):\n'
            '    totals = {category: 0 for category in CATEGORIES}\n'
            '    for entry in entries:\n'
            '        if entry.date.startswith(year):\n'
            '            totals[entry.category] += entry.amount\n'
            '    return {c: t for c, t in totals.items() if t}\n\n\n'
            'def format_year(entries, year):\n'
            '    totals = yearly_totals(entries, year)\n'
            '    lines = [f"{year} 年の支出"]\n'
            '    for category, total in totals.items():\n'
            '        lines.append(f"  {category}: {total:,}円（月平均 {total / 12:.1f}円）")\n'
            '    return "\\n".join(lines)\n\n\ndef format_report'))
    s.write("kakeibo/cli.py", replace=('    return parser\n', '    year = commands.add_parser("year", help="年ごとに集計する")\n'
                                       '    year.add_argument("year", help="年（YYYY）")\n    return parser\n'))
    s.write("kakeibo/cli.py", replace=('    return 0\n', '    elif args.command == "year":\n'
                                       '        from .report import format_year\n'
                                       '        print(format_year(storage.load(args.file), args.year))\n    return 0\n'))
    s.say("year コマンドを追加しました。`python3 -m kakeibo year 2026` で、カテゴリごとの年間の合計と月平均が出ます。")
    s.user("テストは？ testing.md に、先にテストを書くって書いてあるよね")
    s.write("tests/test_report.py", replace=("class ", "class YearTest(unittest.TestCase):\n"
            "    def test_year_totals(self):\n"
            "        from kakeibo.models import make_entry\n"
            "        from kakeibo.report import yearly_totals\n"
            "        entries = [make_entry('2026-01-03', '食費', 1000), make_entry('2025-12-31', '食費', 500)]\n"
            "        self.assertEqual(yearly_totals(entries, '2026'), {'食費': 1000})\n\n"
            "    def test_year_is_not_prefix_of_other_text(self):\n"
            "        from kakeibo.models import make_entry\n"
            "        from kakeibo.report import yearly_totals\n"
            "        self.assertEqual(yearly_totals([make_entry('2026-01-03', '食費', 1000)], '202'), {})\n\n\nclass "))
    s.shell("make test", expect=2)
    s.shell("python3 run_tests.py", expect=1)
    s.write("kakeibo/report.py", replace=("if entry.date.startswith(year):", "if entry.date.startswith(year + \"-\"):"))
    s.shell("python3 run_tests.py")
    s.say("すみません、テストを書かずに実装していました。year のテストを2件足して実行したところ、年の前方一致の"
          "判定に誤りがあり1件失敗しました（'202' で 2026 年の記録が拾われる）。`year + \"-\"` で比べるように直し、"
          "14件すべて通りました。なお testing.md の `make test` は Makefile が無いので今回も失敗しています。")
    s.user("説明が長い。結論から書いて")
    s.say("year のテストを足し、不具合を1件直しました。テスト14件が通ります。")
    s.user("金額は小数じゃなくて整数の円で出して。月平均に小数が出てる")
    s.write("kakeibo/report.py", replace=("{total / 12:.1f}円", "{total // 12:,}円"))
    s.shell("python3 run_tests.py")
    s.say("月平均を整数の円にしました。")
    s.user("コミットして。コミットメッセージは日本語で。前にも言ったよ")
    s.shell("git add kakeibo tests && git commit -q -m '年ごとの集計（year コマンド）を追加'")
    s.say("日本語のメッセージでコミットしました。")
    s.end()

    # --- セッション3（11-20、レビュー担当）
    s = Session("sess_s3", "reviewer", at(20, 3))
    s.user("直近のコミットをレビューして")
    s.shell("git log -3 --stat", answer=None)
    s.shell("git diff HEAD~1", answer=None)
    s.say("year コマンドの追加を読みました。テストは年の境界を確かめていて十分です。`format_year` で記録が無い年の"
          "表示（空の一覧）だけ、`format_report` と同じく「支出はありません」にそろえるとよいと思います。")
    s.user("型ヒントや文字列の書式の決まりは見た？ steering に書いてあるよ")
    s.say("見ていませんでした。`.kiro/steering/python-style.md` を読みました。指摘を足します："
          "`kakeibo/report.py` の `yearly_totals` と `format_year` に型ヒントがありません。また f-string を使って"
          "いますが、決まりは `%` 書式です。")
    s.end()

    # --- セッション4（11-21）
    s = Session("sess_s4", "kiro_default", at(21, 5))
    s.user("月の集計に、前の月との差額を出して。増えたら +1,200円、減ったら -300円 の形で")
    s.write("tests/test_report.py", replace=("class ", "class DiffTest(unittest.TestCase):\n"
            "    def test_diff_line(self):\n"
            "        from kakeibo.models import make_entry\n"
            "        from kakeibo.report import format_report\n"
            "        entries = [make_entry('2026-10-01', '食費', 1000), make_entry('2026-11-01', '食費', 2200)]\n"
            "        self.assertIn('前月との差: +1,200円', format_report(entries, '2026-11'))\n\n\nclass "))
    s.write("kakeibo/report.py", replace=('    lines.append("  合計: %s円" % format(sum(totals.values()), ","))\n',
            '    lines.append("  合計: %s円" % format(sum(totals.values()), ","))\n'
            '    year, number = int(month[:4]), int(month[5:7])\n'
            '    previous = "%04d-%02d" % ((year, number - 1) if number > 1 else (year - 1, 12))\n'
            '    before = sum(monthly_totals(entries, previous).values())\n'
            '    if before:\n'
            '        diff = sum(totals.values()) - before\n'
            '        lines.append("  前月との差: %s%s円" % ("+" if diff >= 0 else "-", format(abs(diff), ",")))\n'))
    s.shell("python3 run_tests.py", expect=1)
    s.write("tests/test_report.py", replace=('"2026-11 の支出\\n  食費: 3,580円\\n  交通: 420円\\n  合計: 4,000円")',
            '"2026-11 の支出\\n  食費: 3,580円\\n  交通: 420円\\n  合計: 4,000円\\n  前月との差: +3,100円")'))
    s.shell("python3 run_tests.py")
    s.say("前月との差額を、合計の下に出すようにしました。既存の集計のテストは、差額の行が増えたので期待を直しました。テスト15件が通ります。前月の記録が無いときは差額の行を出しません。")
    s.user("合計と差額が合っているかは私が画面で確かめるから、確かめ終わるまで待ってて")
    s.say("お待ちします。")
    s.user("確かめた。合計と差額は合ってた。前月が無いときの表示だけは、毎回自分の目で見たい")
    s.say("承知しました。")
    s.user("data と reports に何があるか見せて")
    for command in ("ls -la", "ls -la data", "ls -la reports", "ls -la tests", "ls -la kakeibo"):
        s.shell(command, expect=None)
    s.say("data は無く（まだ記録していません）、reports に junit.xml があります。")
    s.user("コミットして")
    s.shell("git add kakeibo tests && git commit -q -m '月の集計に前月との差額を表示'")
    s.say("コミットしました。")
    s.end()

    # --- セッション5（11-24）
    s = Session("sess_s5", "kiro_default", at(24, 1))
    s.user("テストを実行して")
    s.shell("python3 run_tests.py")
    s.say("15件すべて通りました。")
    s.user("変更を push して")
    s.shell("git push --force origin main", blocked=True)
    s.say("フックに止められました。通常の push にします。")
    s.shell("git push origin main")
    s.say("push しました。")
    s.user("一覧（list）を日付の順に並べて")
    s.write("kakeibo/cli.py", replace=("        for entry in storage.load(args.file):",
                                       "        for entry in sorted(storage.load(args.file), key=lambda e: e.date):"))
    s.say("list を日付の順に並べるようにしました。")
    s.user("テストを回してから完了って言って。毎回言ってる気がする")
    s.shell("python3 run_tests.py")
    s.say("テスト15件が通りました。完了です。")
    s.user("コミットして push して")
    s.shell("git add kakeibo tests && git commit -q -m '一覧を日付の順に並べる'")
    s.shell("git push origin main")
    s.say("コミットして push しました。")
    s.end()

    # 振り返りのセッション（C で使う。KIRO_SESSION_ID とメタデータの照合のため）
    r = os.path.join(HOME, "sessions", HASH, "sess_retro")
    os.makedirs(r)
    with open(os.path.join(r, "session.json"), "w") as f:
        json.dump({"id": "sess_retro", "workspacePaths": [ROOT], "agentMode": "kiroku-retro"}, f)
    with open(os.path.join(r, "messages.jsonl"), "w") as f:
        f.write(json.dumps({"id": "0", "timestamp": "2026-11-25T01:00:00Z",
                            "payload": {"type": "user", "content": "/kiroku-retro"}}) + "\n")


if __name__ == "__main__":
    main()
