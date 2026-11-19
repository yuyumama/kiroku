"""reset.py：記録を退避してゼロからやり直す。やり直す前の会話を数えない。"""
import datetime
import json
import os
import subprocess
import sys

from test_kiroku import KIT, aggregate
from test_review import NOW, ReviewSandbox, finding

RESET = os.path.join(KIT, "reset.py")


class ResetTest(ReviewSandbox):
    def run_reset(self, *args):
        env = dict(os.environ)
        env.pop("KIRO_SESSION_ID", None)
        return subprocess.run([sys.executable, RESET, "--root", self.root] + list(args),
                              capture_output=True, text=True, cwd=self.root, env=env, stdin=subprocess.DEVNULL)

    def test_reset_moves_the_data_aside_and_older_conversations_are_not_counted(self):
        self.shell("make test", 2)
        self.log("sess_old", ["テストは make test で回して"], days_ago=1)
        self.note("sess_old", [finding("テストは make test で回して")])
        self.report("2026-11-10-0900", "### P1. 案\n- 対象の型：`tool:make test`\n")
        config = (self.data.parent / "config.json").read_text(encoding="utf-8")
        before = aggregate.aggregate(self.root)
        self.assertEqual((before["event_count"], len(before["sessions"])), (1, 2))

        dry = self.run_reset("--dry-run")
        self.assertEqual(dry.returncode, 0)
        self.assertIn("events.jsonl", dry.stdout)
        self.assertTrue((self.data / "events.jsonl").exists())               # 何も動かさない
        self.assertEqual(self.run_reset().returncode, 1)                       # 確認できないので止まる
        self.assertTrue((self.data / "events.jsonl").exists())

        done = self.run_reset("--yes")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        backups = list((self.data / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        for name in ("events.jsonl", "notes", "reports", "ledger.jsonl" if (backups[0] / "ledger.jsonl").exists()
                     else "events.jsonl"):
            self.assertTrue((backups[0] / name).exists(), name)
        self.assertEqual(sorted(p.name for p in self.data.iterdir() if p.name != "record.lock"),
                         ["backups", "state.json"])
        self.assertEqual((self.data.parent / "config.json").read_text(encoding="utf-8"), config)   # 設定は残す

        after = aggregate.aggregate(self.root, NOW - datetime.timedelta(days=7))
        self.assertEqual((after["event_count"], after["sessions"], after["adopted"]), (0, [], []))
        self.assertIn("記録の始まり", aggregate.render(after))
        self.assertEqual(aggregate.ledger.latest_report(self.root), None)
        # やり直した後の会話と記録は、ふつうに数える。
        self.shell("make build", 2)
        self.assertEqual(aggregate.aggregate(self.root)["event_count"], 1)
        # もう一度やり直すと、別の退避先に移す。前の退避は残る。
        self.assertEqual(self.run_reset("--yes").returncode, 0)
        self.assertEqual(len(list((self.data / "backups").iterdir())), 2)

    def test_reset_is_not_allowed_for_the_retro_agent(self):
        agent = json.loads(open(os.path.join(KIT, "..", "..", "..", "agents", "kiroku-retro.json"),
                                encoding="utf-8").read())
        allowed = [m for rule in agent["permissions"]["rules"]
                   if rule["capability"] == "shell" and rule["effect"] == "allow" for m in rule["match"]]
        self.assertFalse([m for m in allowed if "reset.py" in m])
