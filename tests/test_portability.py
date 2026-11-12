"""製品の実機試験ではなく、導入とログアダプターの契約を検証する。"""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_kiroku import Sandbox, aggregate, excerpt, RECORD, REPORT, junit

spec = importlib.util.spec_from_file_location("installer", Path(__file__).resolve().parents[1] / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
    def test_installed_scripts_run_from_another_directory(self):
        with tempfile.TemporaryDirectory(prefix="kiroku 日本語 space ") as directory:
            root = Path(directory)
            installer.install(root)
            scripts = root / ".kiro/skills/kiroku-retro/scripts"
            payload = {"cwd": str(root), "session_id": "installed-session",
                       "hook_event_name": "UserPromptSubmit", "prompt": "保存しない本文"}
            done = subprocess.run([sys.executable, str(scripts / "record.py")],
                                  input=json.dumps(payload), capture_output=True, text=True)
            self.assertEqual((done.returncode, done.stdout), (0, ""))
            done = subprocess.run([sys.executable, str(scripts / "aggregate.py"),
                                   "--root", str(root), "--since", "all", "--prompts", "--json"],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            data = json.loads(done.stdout)
            self.assertEqual(data["event_count"], 1)
            self.assertEqual(data["prompt_page"]["coverage"][0]["status"], "missing_log")
            self.assertNotIn("保存しない本文", done.stdout)

    def test_windows_commands_and_preserved_configuration(self):
        with tempfile.TemporaryDirectory(prefix="kiroku 日本語 space ") as directory:
            root = Path(directory)
            installer.install(root, python_command="py -3")
            hooks = json.loads((root / ".kiro/hooks/kiroku.json").read_text(encoding="utf-8"))
            self.assertTrue(all(h["action"]["command"].startswith("py -3 ") for h in hooks["hooks"]))
            agent = (root / ".kiro/agents/kiroku-retro.json").read_text(encoding="utf-8")
            self.assertIn("py -3 .kiro/", agent)
            self.assertNotIn("python3 .kiro/", agent)
            config = root / ".kiro/kiroku/config.json"
            config.write_text('{"tests": [{"match": "custom"}]}', encoding="utf-8")
            installer.install(root, update=True, python_command="python")
            self.assertEqual(json.loads(config.read_text())["tests"][0]["match"], "custom")
            self.assertEqual((root / ".gitignore").read_text(encoding="utf-8").count(".kiro/kiroku/data/"), 1)

    def test_conflict_is_checked_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".kiro/hooks/kiroku.json"
            path.parent.mkdir(parents=True)
            path.write_text("custom", encoding="utf-8")
            with self.assertRaises(ValueError):
                installer.install(root)
            self.assertEqual(path.read_text(), "custom")
            self.assertFalse((root / ".kiro/agents").exists())
            self.assertFalse((root / ".gitignore").exists())

    def test_update_migrates_old_data_and_removes_old_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / ".kiro/retro"
            old.mkdir(parents=True)
            (old / "events.jsonl").write_text("data", encoding="utf-8")
            script = root / ".kiro/kiroku/record.py"
            script.parent.mkdir()
            script.write_text("old", encoding="utf-8")
            (root / ".gitignore").write_text(".kiro/retro/\n", encoding="utf-8")
            installer.install(root, update=True)
            self.assertFalse(script.exists())
            self.assertEqual((root / ".kiro/kiroku/data/events.jsonl").read_text(), "data")


class AdapterTest(Sandbox):
    def test_concurrent_hooks_consume_junit_once_without_corrupting_events(self):
        self.write(REPORT, junit([("tests.test_a", "test_a", "passed")]))
        payload = json.dumps({"cwd": self.root, "session_id": self.session,
                              "hook_event_name": "PostToolUse", "tool_name": "execute_bash",
                              "tool_input": {"command": "pytest"},
                              "tool_response": "Output:\nok\nExit Code: 0"})
        processes = []
        for _ in range(6):
            process = subprocess.Popen([sys.executable, RECORD], cwd=self.root,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            process.stdin.write(payload)
            process.stdin.close()
            process.stdin = None
            processes.append(process)
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual((process.returncode, stdout, stderr), (0, "", ""))
        events = self.events()
        self.assertEqual(len(events), 6)
        self.assertEqual(sum(e.get("results") is not None for e in events), 1)
        self.assertEqual(sum(e.get("note") == "report_stale" for e in events), 5)

    def test_unknown_shell_format_reports_a_gap(self):
        self.hook({"hook_event_name": "PostToolUse", "tool_name": "execute_pwsh",
                   "tool_input": {"command": "pytest"},
                   "tool_response": {"unknown_result": "something"}})
        self.assertEqual(self.events()[0]["type"], "recording_gap")
        data = aggregate.aggregate(self.root)
        self.assertEqual(data["recording_gaps"][0]["reason"], "unknown_shell_response")
        self.assertIn("成功・失敗を判定できない", aggregate.render(data))

    def test_harness_inventory_includes_both_scopes_without_secrets(self):
        self.write(".kiro/steering/dev.md", "project rules")
        home = Path(self.root) / "user settings [1]"
        (home / "settings").mkdir(parents=True)
        (home / "settings/mcp.json").write_text('{"token": "PRIVATE_SECRET"}', encoding="utf-8")
        with patch.dict(os.environ, {"KIRO_HOME": str(home)}):
            data = aggregate.aggregate(self.root)
        entries = data["harness"]
        self.assertTrue(any(e["scope"] == "user" and e["path"].endswith("settings/mcp.json")
                            and e["status"] == "present" for e in entries))
        self.assertTrue(any(e["scope"] == "project" and e["path"].endswith("steering")
                            and e["status"] == "present" for e in entries))
        self.assertNotIn("PRIVATE_SECRET", aggregate.render(data))
        self.assertIn("比較対象の所在", aggregate.render(data))

    def test_unreadable_inventory_is_not_reported_as_absent(self):
        with patch("harness.Path.stat", side_effect=PermissionError("denied")):
            entries = aggregate.harness.inventory(self.root)
        self.assertTrue(all(e["status"] == "unreadable" for e in entries))

    def test_powershell_failure(self):
        result = self.hook({"hook_event_name": "PostToolUse", "tool_name": "execute_pwsh",
                            "tool_input": {"command": "Get-Item missing"},
                            "tool_response": "Output:\nmissing\n\nExit Code: 1"})
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(self.events()[0]["type"], "tool_failure")

    def test_append_tool_records_final_hash(self):
        self.write("a.txt", "appended")
        self.hook({"hook_event_name": "PostToolUse", "tool_name": "fs_append",
                   "tool_input": {"path": "a.txt"}})
        self.assertEqual(self.events()[0]["path"], "a.txt")
        self.assertIsNotNone(self.events()[0]["hash"])

    def test_exported_conversation_uses_both_roles_from_log(self):
        self.write(".kiro/kiroku/config.json", json.dumps({"sessions_dir": ".kiro/kiroku/data/exports"}))
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "fallback only"})
        relative = ".kiro/kiroku/data/exports/%s/messages.jsonl" % self.session
        rows = [None, {"payload": []}, {"timestamp": "2026-11-12T00:00:00Z",
                 "payload": {"type": "user", "content": "ログの質問"}},
                {"timestamp": "2026-11-12T00:00:01Z",
                 "payload": {"type": "assistant", "content": "ログの回答"}}]
        self.write(relative, "\n".join(json.dumps(r) for r in rows))
        talk = aggregate.aggregate(self.root, with_prompts=True)["prompt_page"]
        self.assertEqual(talk["chunks"][0]["text"], "ログの質問")
        self.assertEqual(len(talk["chunks"]), 1)
        os.remove(os.path.join(self.root, relative))
        talk = aggregate.aggregate(self.root, with_prompts=True)["prompt_page"]
        self.assertEqual(talk["coverage"][0]["status"], "missing_log")
        self.assertEqual(talk["chunks"], [])

    def test_distilled_session_view_and_note_status(self):
        home = Path(tempfile.mkdtemp(prefix="kiroku-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self.addCleanup(os.environ.pop, "KIRO_HOME", None)
        os.environ["KIRO_HOME"] = str(home)
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "x"})
        log_dir = home / "sessions" / "0123456789abcdef" / self.session
        log_dir.mkdir(parents=True)
        now = datetime.datetime.now(datetime.timezone.utc)
        rows = [
            {"type": "user", "content": "設定の読み込みを作って。password=hunter2pw"},
            {"type": "tool_call", "toolName": "fs_write", "args": {"path": "a.py"}},
            {"type": "tool_result", "success": True, "content": "ok"},
            {"type": "tool_call", "toolName": "execute_bash", "args": {"command": "pytest"}},
            {"type": "tool_result", "success": False, "content": "boom"},
            {"type": "assistant", "content": "途中経過です"},
            {"type": "assistant", "content": "できました。" + "長" * 900},
            {"type": "assistant", "content": "..."},  # 実機のログに入る、中身のない応答
            {"type": "user", "content": "長い。結論から言って。" + "あ" * 4000 + "末尾の指示"},
            {"type": "assistant", "content": "結論です"},
        ]
        with open(log_dir / "messages.jsonl", "w", encoding="utf-8") as handle:
            for index, payload in enumerate(rows):
                stamp = (now + datetime.timedelta(seconds=index)).isoformat()
                handle.write(json.dumps({"id": str(index), "timestamp": stamp, "payload": payload}) + "\n")

        view = aggregate.prompts.distill(self.root, self.session)
        self.assertEqual([t["index"] for t in view["turns"]], [0, 8])
        first, second = view["turns"]
        self.assertEqual(first["tools"], {"fs_write": 1, "execute_bash": 1})
        self.assertEqual(first["tool_failures"], 1)
        self.assertEqual(first["reply"]["index"], 6)           # 次の発話の直前の応答
        self.assertLessEqual(len(first["reply"]["text"]), 301)
        self.assertNotIn("hunter2pw", first["text"])
        self.assertIn("長い。結論から言って。", second["text"])   # 頭と
        self.assertIn("末尾の指示", second["text"])              # 末尾を残す
        self.assertLess(len(second["text"]), 1600)
        text = aggregate.render_session(view)
        self.assertIn("ツール実行：execute_bash×1、fs_write×1、失敗 1 件", text)
        self.assertNotIn("途中経過です", text)

        row = aggregate.aggregate(self.root)["sessions"][0]
        self.assertEqual((row["log"], row["prompts"], row["note"]), ("ok", 2, "none"))
        notes = Path(self.root) / ".kiro" / "kiroku" / "data" / "notes"
        notes.mkdir(parents=True)
        note = notes / (self.session + ".md")
        note.write_text("# note\n", encoding="utf-8")
        future = (now + datetime.timedelta(hours=1)).timestamp()
        os.utime(note, (future, future))
        self.assertEqual(aggregate.aggregate(self.root)["sessions"][0]["note"], "fresh")
        os.utime(note, (now.timestamp() - 3600, now.timestamp() - 3600))
        self.assertEqual(aggregate.aggregate(self.root)["sessions"][0]["note"], "stale")
        self.assertIn("ノートの後に発話あり", aggregate.render(aggregate.aggregate(self.root)))

    def test_missing_log_is_reported_in_session_view(self):
        self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "x"})
        view = aggregate.prompts.distill(self.root, self.session)
        self.assertEqual((view["status"], view["turns"]), ("missing_log", []))
        self.assertIn("会話に基づく判断を保留", aggregate.render_session(view))
        self.assertEqual(aggregate.aggregate(self.root)["sessions"][0]["log"], "missing_log")

    def test_session_id_cannot_select_unrelated_logs(self):
        self.assertIsNone(excerpt.find_log("../private", self.root))
        self.assertIsNone(excerpt.find_log("*", self.root))
