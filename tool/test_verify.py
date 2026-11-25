import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from verify import verify_case, subsequence


class AcceptanceTest(unittest.TestCase):
    def test_source_file_and_line_are_required(self):
        for location, expected in [("other.dart:24", False), ("lib/main.dart:999", False),
                                   ("/project/lib/main.dart:24:3", True)]:
            self.assertEqual(subsequence([{"function": "fixtureDartCompute", "location": location}],
                                         ["fixtureDartCompute"]), expected)

    def test_one_false_dart_frame_fails_native_only(self):
        case = {"name":"Native only", "pid":7, "begin":"10 dartTid=7", "end":"19",
                "am_anr":["15 [0,7,dev.stalltrace.stalltrace_example, Input dispatching timed out"]}
        rows = [dict(pid=7, session="s", probe=1, ageMs=5000, sampleWallMs=sec*1000,
                     sampleStartMonoNs=1, sampleEndMonoNs=3, pendingAfterNative=True,
                     native="2 7 0\npc x 01 1") for sec in [13, 15, 17]]
        stack = [dict(module="libfixture.so", function="fixture_native_wait", location="src/main/cpp/fixtures.c:8"),
                 dict(module="libfixture.so", function="fixture_native_entry", location="src/main/cpp/fixtures.c:11")]
        symbols = Mock()
        with patch("verify.system_stack", return_value=None):
            symbols.decode.return_value = stack
            self.assertTrue(verify_case(case,rows,symbols,Path("unused"),28)["passed"])
            symbols.decode.side_effect = [stack, stack, stack + [dict(module="libapp.so", function="wrong", location="??")]]
            self.assertFalse(verify_case(case,rows,symbols,Path("unused"),28)["passed"])
