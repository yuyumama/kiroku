import unittest

from calc import add


class AddTest(unittest.TestCase):
    def test_add_small_numbers(self):
        self.assertEqual(add(1, 2), 3)


if __name__ == "__main__":
    unittest.main()
