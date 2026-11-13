import os
import tempfile
import unittest

from kakeibo import storage
from kakeibo.models import make_entry


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "data", "kakeibo.json")

    def test_missing_file_is_an_empty_book(self):
        self.assertEqual(storage.load(self.path), [])

    def test_added_entries_are_saved_in_date_order(self):
        storage.add(make_entry("2026-11-10", "交通", 420), self.path)
        storage.add(make_entry("2026-11-03", "食費", 1280, "昼食"), self.path)
        self.assertEqual([entry.date for entry in storage.load(self.path)], ["2026-11-03", "2026-11-10"])
        self.assertFalse(os.path.exists(self.path + ".tmp"))
