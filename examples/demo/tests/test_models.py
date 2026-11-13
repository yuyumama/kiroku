import unittest

from kakeibo.models import InvalidEntry, make_entry


class MakeEntryTest(unittest.TestCase):
    def test_valid_entry(self):
        entry = make_entry("2026-11-03", "食費", 1280, " 昼食 ")
        self.assertEqual((entry.date, entry.category, entry.amount, entry.memo),
                         ("2026-11-03", "食費", 1280, "昼食"))

    def test_date_must_be_iso(self):
        with self.assertRaises(InvalidEntry):
            make_entry("2026/11/03", "食費", 1280)

    def test_unknown_category(self):
        with self.assertRaises(InvalidEntry):
            make_entry("2026-11-03", "外食", 1280)

    def test_amount_must_be_positive_integer(self):
        for amount in (0, -100, 12.5, True):
            with self.assertRaises(InvalidEntry):
                make_entry("2026-11-03", "食費", amount)
