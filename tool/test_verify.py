import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from verify import foreground_anr_death, verify_case, subsequence


class AcceptanceTest(unittest.TestCase):
    def test_only_same_foreground_input_anr_can_supply_the_end(self):
        process = "0,7,dev.stalltrace.stalltrace_example"
        reason = "Input dispatching timed out (window is not responding)."
        kill = f"16 am_kill : [{process},0,user request after error:{reason},25]"
        case = dict(pid=7, begin="10 begin", end=f"17 am_proc_died: [{process},0,2]",
                    am_anr=[f"15 am_anr : [{process},0,{reason}]"], anrKill=kill)
        self.assertTrue(foreground_anr_death(case))
        for wrong in (kill.replace("16 ", "14 "), kill.replace("0,user request", "900,user request"),
                      kill.replace(reason, "bg anr"), kill.replace("0,7,", "0,8,"),
                      kill.replace("window", "other window")):
            self.assertFalse(foreground_anr_death(dict(case, anrKill=wrong)))

    def test_source_file_and_line_are_required(self):
        for location, expected in [("other.dart:24", False), ("lib/main.dart:999", False),
                                   ("/project/lib/main.dart:24:3", True)]:
            self.assertEqual(subsequence([{"function": "fixtureDartCompute", "location": location}],
                                         ["fixtureDartCompute"]), expected)

    def test_early_switch_without_transition_requires_proven_death_and_dart_dump(self):
        proc = "0,7,dev.stalltrace.stalltrace_example"
        reason = "Input dispatching timed out (Waited 5000ms for MotionEvent)."
        case = dict(name="Switch early", pid=7, begin="10 begin dartTid=7", injectedWall=10.1,
                    end=f"16.6 am_proc_died: [{proc},0,2]", am_anr=[f"15.1 am_anr : [{proc},0,{reason}]"],
                    anrKill=f"16.5 am_kill : [{proc},0,user request after error:{reason},42]")
        stack = [dict(function="fixtureDartCompute", location="lib/main.dart:24"),
                 dict(function="fixtureDartCaller", location="lib/main.dart:29")]
        rows = [dict(pid=7, session="s", probe=1, ageMs=1500, sampleWallMs=t*1000,
                     sampleStartMonoNs=1, sampleEndMonoNs=3, pendingAfterNative=True,
                     native="2 7 0\npc x 01 1") for t in (12, 14.8)]
        symbols = Mock()
        symbols.decode.return_value = stack
        with patch("verify.system_stack", return_value={"stack":stack,"dumpWall":15.2}) as dump:
            check = lambda c: verify_case(c,rows,symbols,Path("unused"),35)["passed"]
            self.assertTrue(check(case))
            self.assertFalse(check(dict(case, name="Switch late")))
            self.assertFalse(check(dict(case, end="16.6 returned", anrKill="")))
            dump.return_value = None
            self.assertFalse(check(case))

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
