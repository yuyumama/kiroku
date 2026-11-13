import unittest

from kakeibo.models import make_entry
from kakeibo.report import format_report, monthly_totals

ENTRIES = [
    make_entry("2026-10-31", "食費", 900),
    make_entry("2026-11-03", "食費", 1280),
    make_entry("2026-11-10", "交通", 420),
    make_entry("2026-11-21", "食費", 2300),
]


class ReportTest(unittest.TestCase):
    def test_totals_cover_only_the_month(self):
        self.assertEqual(monthly_totals(ENTRIES, "2026-11"), {"食費": 3580, "交通": 420})

    def test_report_text(self):
        self.assertEqual(format_report(ENTRIES, "2026-11"),
                         "2026-11 の支出\n  食費: 3,580円\n  交通: 420円\n  合計: 4,000円")

    def test_month_without_entries(self):
        self.assertEqual(format_report(ENTRIES, "2026-12"), "2026-12 の支出はありません。")
