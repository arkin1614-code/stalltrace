import unittest
from pathlib import Path
from unittest.mock import patch
from symbolize import Symbols


class SymbolMatchingTest(unittest.TestCase):
    def setUp(self):
        with patch("symbolize.llvm_output", return_value="Build ID: 0123456789abcdef"):
            self.symbols = Symbols(Path("llvm"), {"libapp.so": Path("app.symbols")})

    def test_rejects_different_build_of_same_module(self):
        with self.assertRaisesRegex(ValueError, "build ID mismatch"):
            self.symbols.decode(["pc libapp.so 0123456789abcdee abcd"])

    def test_missing_module_and_unverified_lr_remain_unattributed(self):
        frames = self.symbols.decode(["pc other.so 01234567 ab", "lr libapp.so 0123456789abcdef 42"])
        self.assertEqual(["??"], [f["function"] for f in frames])


if __name__ == "__main__":
    unittest.main()
