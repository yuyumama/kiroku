import contextlib
import io
import os
import tempfile
import unittest

from kakeibo.cli import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = os.path.join(self.directory.name, "kakeibo.json")

    def run_cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["--file", self.file] + list(arguments))
        return code, output.getvalue(), errors.getvalue()

    def test_add_then_report(self):
        self.assertEqual(self.run_cli("add", "2026-11-03", "食費", "1280", "昼食")[0], 0)
        code, output, _ = self.run_cli("report", "2026-11")
        self.assertEqual(code, 0)
        self.assertIn("合計: 1,280円", output)

    def test_invalid_entry_is_rejected(self):
        code, _, errors = self.run_cli("add", "2026/11/03", "食費", "1280")
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", errors)
