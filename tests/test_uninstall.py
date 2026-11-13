"""アンインストールのテスト。kiroku のものだけを消し、ほかのハーネスには触らないこと。"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("install")
uninstaller = load("uninstall")


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.target = Path(tempfile.mkdtemp(prefix="kiroku-uninstall-"))
        self.addCleanup(shutil.rmtree, self.target, ignore_errors=True)
        (self.target / ".gitignore").write_text("node_modules\n", encoding="utf-8")
        # kiroku とは別の、利用者のハーネス。改善の成果物も含む。
        self.mine = {
            ".kiro/steering/testing.md": "テストは make test で回す\n",
            ".kiro/hooks/format.json": "{}\n",
            ".kiro/skills/release/SKILL.md": "# release\n",
            ".kiro/agents/reviewer.json": "{}\n",
        }
        for name, text in self.mine.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        installer.install(self.target)
        data = self.target / ".kiro" / "kiroku" / "data"
        (data / "reports").mkdir(parents=True)
        (data / "notes").mkdir()
        (data / "reports" / "2026-11-13-0900.md").write_text("# 振り返り\n", encoding="utf-8")
        (data / "notes" / "sess_a.json").write_text("{}", encoding="utf-8")
        (data / "events.jsonl").write_text("{}\n", encoding="utf-8")
        (data / "ledger.jsonl").write_text("{}\n", encoding="utf-8")

    def files(self):
        return sorted(str(p.relative_to(self.target)) for p in self.target.rglob("*") if p.is_file())

    def assert_user_harness_untouched(self):
        for name, text in self.mine.items():
            self.assertEqual((self.target / name).read_text(encoding="utf-8"), text)

    def test_remove_everything(self):
        result = uninstaller.uninstall(self.target)
        self.assertEqual(self.files(), sorted(list(self.mine) + [".gitignore"]))
        self.assert_user_harness_untouched()
        self.assertEqual((self.target / ".gitignore").read_text(encoding="utf-8"), "node_modules\n")
        self.assertTrue(result["gitignore"])
        self.assertEqual(result["kept"], [])

    def test_keep_reports_only(self):
        result = uninstaller.uninstall(self.target, keep_reports=True)
        self.assertEqual(self.files(), sorted(list(self.mine) + [
            ".gitignore", ".kiro/kiroku/data/reports/2026-11-13-0900.md"]))
        self.assert_user_harness_untouched()
        # レポートが git に載らないよう、除外の行は残す。
        self.assertIn(".kiro/kiroku/data/", (self.target / ".gitignore").read_text(encoding="utf-8"))
        self.assertEqual(result["kept"], [".kiro/kiroku/data/reports"])

    def test_keep_reports_without_reports_removes_everything(self):
        shutil.rmtree(self.target / ".kiro" / "kiroku" / "data" / "reports")
        uninstaller.uninstall(self.target, keep_reports=True)
        self.assertFalse((self.target / ".kiro" / "kiroku").exists())
        self.assertNotIn("kiroku", (self.target / ".gitignore").read_text(encoding="utf-8"))

    def test_dry_run_changes_nothing(self):
        before = self.files()
        result = uninstaller.uninstall(self.target, dry_run=True)
        self.assertEqual(self.files(), before)
        self.assertIn(".kiro/skills/kiroku-retro", result["removed"])

    def test_empty_directories_are_removed_but_not_others(self):
        bare = Path(tempfile.mkdtemp(prefix="kiroku-uninstall-"))
        self.addCleanup(shutil.rmtree, bare, ignore_errors=True)
        installer.install(bare)
        uninstaller.uninstall(bare)
        self.assertEqual(list(bare.iterdir()), [])  # .kiro も .gitignore も残らない

    def test_old_layout_is_removed_too(self):
        old = self.target / ".kiro" / "retro"
        old.mkdir()
        (old / "events.jsonl").write_text("{}\n", encoding="utf-8")
        uninstaller.uninstall(self.target)
        self.assertFalse(old.exists())

    def test_cli_needs_an_explicit_choice_and_confirmation_without_a_terminal(self):
        def run(*args):
            return subprocess.run([sys.executable, str(ROOT / "uninstall.py"), str(self.target)] + list(args),
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)
        before = self.files()
        self.assertEqual(run().returncode, 2)             # 選択が無い
        self.assertEqual(run("--all").returncode, 2)      # 確認できない
        self.assertEqual(self.files(), before)
        preview = run("--keep-reports", "--dry-run")
        self.assertEqual(preview.returncode, 0)
        self.assertIn("残すもの", preview.stdout)
        self.assertEqual(self.files(), before)
        done = run("--keep-reports", "--yes")
        self.assertEqual(done.returncode, 0)
        self.assertIn("取り除きました", done.stdout)
        self.assertTrue((self.target / ".kiro/kiroku/data/reports/2026-11-13-0900.md").is_file())
        again = run("--all", "--yes")
        self.assertEqual(again.returncode, 0)
        self.assertFalse((self.target / ".kiro" / "kiroku").exists())
        self.assertIn("見つかりませんでした", run("--all", "--yes").stdout)


if __name__ == "__main__":
    unittest.main()
