import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from diagnose import (
    application_frames,
    capture_candidates,
    classify,
    diagnose,
    line_evidence,
    neighbors,
    primary_dump,
    retrace,
)
from symbolize import Symbols

SUBJECT = "Subject: Input dispatching timed out (test)\n"
HEADER = (
    "----- pid 42 at 2024-01-01 00:00:00.123456789+0000 -----\nCmd line: test.app\n"
)
MAIN = """"main" prio=5 tid=1 Blocked
  | sysTid=42
  at test.app.Store.write(Store.java:24)
  - waiting to lock <0xabc> (a java.lang.Object) held by thread 7

"""
OWNER = """"sync" prio=5 tid=7 Sleeping
  | sysTid=91
  at test.app.Store.sync(Store.java:10)
  - locked <0xabc> (a java.lang.Object)

"""
STAMP = 1704067200123
ID = "00000000-0000-0000-0000-000000000000-2"


class DiagnoseTest(unittest.TestCase):
    def dump(self, text, stamp=STAMP):
        return primary_dump(text, 42, "test.app", stamp, Symbols(None, {}))

    def test_holder_identified_by_art_tid_with_separate_linux_tid(self):
        result = self.dump(SUBJECT + HEADER + MAIN + OWNER)
        self.assertTrue(result["monitorWait"])
        self.assertEqual(
            result["owner"],
            dict(
                thread="sync",
                artTid=7,
                sysTid=91,
                javaFrames=["test.app.Store.sync(Store.java:10)"],
                sameMonitorListed=True,
            ),
        )

    def test_rejects_wrong_primary_identity_time_or_input_subject(self):
        valid = SUBJECT + HEADER + MAIN
        for text in (
            valid.replace("pid 42", "pid 43"),
            valid.replace("test.app\n", "test.app:other\n"),
            valid.replace("sysTid=42", "sysTid=43"),
            HEADER + MAIN,
            SUBJECT + "----- pid garbage -----\n" + HEADER + MAIN,
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.dump(text))
        self.assertIsNone(self.dump(valid, STAMP + 1))

    def test_secondary_or_ambiguous_owner_never_fills_primary(self):
        base = SUBJECT + HEADER + MAIN
        for tail in (
            "",
            "----- end 42 -----\n" + HEADER + OWNER,
            OWNER + OWNER,
            OWNER.replace("tid=7", "tid=91").replace("sysTid=91", "sysTid=7"),
        ):
            with self.subTest(tail=tail):
                self.assertIsNone(self.dump(base + tail)["owner"])

    def test_framework_or_source_required_for_specific_classification(self):
        self.assertEqual(
            classify(["java.lang.Object.wait(Native method)"], [], False),
            "undetermined",
        )
        self.assertEqual(
            classify(
                ["android.database.sqlite.SQLiteConnectionPool.waitForConnection(X)"],
                [],
                False,
            ),
            "sqliteConnectionWait",
        )
        frame = dict(
            module="libapp.so", function="FakeJsonDecoder", location="app/my.dart:1"
        )
        self.assertEqual(classify([], [frame], False), "dartAotExecution")
        self.assertEqual(
            classify(
                [],
                [
                    {
                        **frame,
                        "function": "jsonDecode",
                        "location": "sdk/lib/convert/json.dart:160",
                    }
                ],
                False,
            ),
            "dartJsonDecode",
        )
        self.assertEqual(
            application_frames(
                ["app.fake2.Foo.bar(x)"], [frame], "app.fake", ["other/my.dart"]
            ),
            {"java": None, "dart": None},
        )

    def test_wrong_r8_id_fails_before_retrace(self):
        with TemporaryDirectory() as tmp, patch("diagnose.subprocess.run") as run:
            mapping = Path(tmp) / "mapping.txt"
            mapping.write_text("# pg_map_id: 1a2b3c\na.A -> b:\n")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                retrace([["b.f(r8-map-id-cccccc:3)"]], mapping, ["retrace"])
            run.assert_not_called()

    def test_line_evidence_uses_the_dump_and_can_pick_the_other_dart_neighbor(self):
        def stack(mono, line):
            frame = (
                {"function": "load", "file": "lib/cache.dart", "line": line}
                if line
                else None
            )
            return {
                "captureMonoNs": mono,
                "applicationFrames": {"dart": frame},
                "nativeStack": [
                    {
                        "module": "libapp.so",
                        "function": "load",
                        "location": f"/pkg/lib/cache.dart:{line}",
                    }
                ],
            }

        earlier, later = stack(31, 27), stack(42, 24)
        adjacent = [(-1600, earlier), (380, later)]
        result = line_evidence(stack(0, 27), later, adjacent, "dart")
        self.assertEqual(result["atDump"]["line"], 27)
        self.assertEqual(result["selected"]["line"], 24)
        self.assertEqual(result["relation"], "differentLine")
        self.assertEqual(result["corroboratingCaptureMonoNs"], 31)
        missing = line_evidence(
            stack(0, None), later, [(100, later), (500, later)], "dart"
        )
        self.assertEqual(missing["relation"], "unconfirmed")
        self.assertIsNone(missing["atDump"])
        self.assertIsNone(missing["corroboratingCaptureMonoNs"])

        other_package = stack(51, 27)
        other_package["nativeStack"][0]["location"] = "/other/lib/cache.dart:27"
        result = line_evidence(earlier, other_package, [(100, other_package)], "dart")
        self.assertEqual(result["relation"], "differentLine")
        self.assertIsNone(result["corroboratingCaptureMonoNs"])

    def test_ambiguous_or_unpositioned_java_cannot_confirm_a_line(self):
        method = "app.Data.save(Data.java:12)"
        live = {
            "captureMonoNs": 15,
            "applicationFrames": {"java": method},
            "javaFrames": [method],
            "javaDelayMs": 10,
            "javaEndDelayMs": 25,
        }
        system = {"applicationFrames": {"java": method}, "mainJava": [method]}
        self.assertIsNone(
            line_evidence(system, live, [(2490, live)], "java")[
                "corroboratingCaptureMonoNs"
            ]
        )
        system["mainJava"].append("<OR> app.Data.other(Other.java:3)")
        self.assertIsNone(line_evidence(system, live, [], "java")["atDump"])
        for frame in (
            "app.Data.save(Unknown Source)",
            "app.Data.save(SourceFile:1)",
            "app.Data.save(r8-map-id-ff0123ab:1)",
        ):
            system = {"applicationFrames": {"java": frame}, "mainJava": [frame]}
            self.assertIsNone(line_evidence(system, live, [], "java")["atDump"])

    def test_neighbors_reject_other_process_duplicate_stamp_and_outside_window(self):
        def sample(wall, process="app"):
            return {
                "process": process,
                "pid": 7,
                "native": "123 7 0",
                "sampleStartMonoNs": 122,
                "sampleEndMonoNs": 124,
                "sampleWallMs": wall,
            }

        selected = sample(3100)
        items = [
            sample(100),
            sample(2300),
            sample(2800, "other"),
            selected,
            sample(3100),
            sample(6000),
        ]
        actual = list(neighbors([(r, None) for r in items], selected, 3000))
        self.assertEqual([d for d, _, _ in actual], [-700])

    def test_java_must_be_adjacent_pending_and_after_the_native_acquisition(self):
        native = dict(
            type="stall_sample",
            session=ID.rsplit("-", 1)[0],
            probe=2,
            sampleStartMonoNs=3,
            sampleEndMonoNs=8,
            pendingAfterNative=True,
        )
        java = dict(type="java_sample", pendingAfterJava=True, monoNs=9, frames=["one"])
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for segment, rows in (
                ("", [native]),
                ("-tail0", [java, native, {**java, "monoNs": 6}]),
                ("-tail1", [native, {**java, "pendingAfterJava": False}, native, java]),
            ):
                (folder / (ID + segment + ".jsonl")).write_text(
                    "".join(json.dumps(r) + "\n" for r in rows)
                )
            self.assertEqual(
                [j for _, j in capture_candidates(folder, ID)], [None, None, None, java]
            )

    def test_unmatched_java_only_stall_needs_no_system_dump(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "catalog.json").write_text(
                json.dumps([dict(kind="stall", id=ID, association="ambiguous")])
            )
            sample = dict(
                type="stall_sample",
                session=ID.rsplit("-", 1)[0],
                probe=2,
                pendingAfterNative=True,
                sampleStartMonoNs=1,
                sampleEndMonoNs=3,
                native=None,
            )
            java = dict(
                type="java_sample",
                pendingAfterJava=True,
                monoNs=5,
                frames=["example.App.save(App.java:7)"],
            )
            (folder / (ID + ".jsonl")).write_text(
                json.dumps(sample) + "\n" + json.dumps(java) + "\n"
            )
            result = diagnose(folder, ID, Symbols(None, {}), java_package="example")
            self.assertIsNone(result["system"])
            self.assertEqual(result["association"], "ambiguous")
            self.assertEqual(
                result["live"]["applicationFrames"]["java"], java["frames"][0]
            )
            self.assertIsNone(result["live"]["javaDelayMs"])
