"""Verify device/trace identity, ELF IDs, capture times and ordered fixture frames."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from symbolize import Symbols, main_thread_dumps, complete_records, samples_with_raw

PACKAGE = "dev.stalltrace.stalltrace_example"


# Source ranges belong to the version of the example used to build the ELF.
SOURCE = {
    "fixtureDartCompute": ("lib/main.dart", 17, 26),
    "fixtureDartCaller": ("lib/main.dart", 29, 29),
    "fixtureDartFfiCaller": ("lib/main.dart", 32, 35),
    "fixture_native_wait": ("src/main/cpp/fixtures.c", 6, 9),
    "fixture_native_entry": ("src/main/cpp/fixtures.c", 10, 13),
    "fixture_slow_constructor": ("src/main/cpp/slow.c", 5, 10),
}
DART = ["fixtureDartCompute", "fixtureDartCaller"]
FFI = ["fixture_native_wait", "fixture_native_entry", "fixtureDartFfiCaller"]


def subsequence(frames, expected):
    i = 0
    for frame in frames:
        if i == len(expected) or frame["function"] != expected[i]:
            continue
        path, lo, hi = SOURCE[expected[i]]
        pos = re.fullmatch(r"(.+?):(\d+)(?::\d+)?", frame["location"])
        if pos and (pos[1] == path or pos[1].endswith("/" + path)) and lo <= int(pos[2]) <= hi:
            i += 1
    return i == len(expected)


def system_stack(raw, case, symbols, begin, end):
    """Match a system dump by process, main sysTid, subject and dump time."""
    pid = case["pid"]
    for meta_file in raw.glob(f"exit-*-{pid}.json"):
        meta = json.loads(meta_file.read_text())
        if meta["process"] != PACKAGE or meta["pid"] != pid or not meta["traceFile"]:
            continue
        filename = raw / Path(meta["traceFile"]).name
        if not filename.is_file():
            continue
        trace = filename.read_text(errors="replace")
        subject = re.search(r"^Subject: (Input dispatching timed out .+)$", trace, re.M)
        if not subject or not any(subject[1] in line for line in case["am_anr"]):
            continue
        for dump in main_thread_dumps(trace, symbols):
            if dump["process"] != PACKAGE or dump["pid"] != pid:
                continue
            stamp = re.sub(r"(\.\d{6})\d+", r"\1", dump["stamp"])
            dump_time = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f%z").timestamp()
            if begin <= dump_time <= end:
                return {"exitReason": meta["reason"], "anrExit": meta["reasonAnr"],
                        "traceFile": filename.name, "dumpWall": dump_time, "stack": dump["stack"]}
    return None


def foreground_anr_death(case):
    """Verify the ordered system event, foreground kill reason and process death."""
    identity = r"\[\d+," + str(case["pid"]) + "," + re.escape(PACKAGE) + ","
    kill, death = case.get("anrKill", ""), case["end"]
    reason = re.search(r"\bam_kill\s*:\s*" + identity +
                       r"0,user request after error:(Input dispatching timed out .+),\d+\]$", kill)
    if not reason or not re.search(r"\bam_proc_died\s*:\s*" + identity, death):
        return False
    begin, killed, died = (float(line.split()[0]) for line in (case["begin"], kill, death))
    return any(re.search(r"\bam_anr\s*:\s*" + identity, event) and
               reason[1] in event and begin <= float(event.split()[0]) <= killed <= died
               for event in case["am_anr"])


def verify_case(case, rows, symbols, raw, api):
    name, pid = case["name"], case["pid"]
    begin, end = [float(case[key].split()[0]) for key in ("begin", "end")]
    dart_tid = int(re.search(r"dartTid=(\d+)", case["begin"])[1])
    switched = float(case["switched"].split()[0]) if "switched" in case else None
    expected = FFI if name in ("Native FFI", "Switch late") else DART
    if name == "Native only":
        expected = FFI[:2]
    def phase(at):
        if switched is None:
            return expected
        return DART if at < switched else FFI
    observed = []
    for row in rows:
        wall = row["sampleWallMs"] / 1000
        if row["pid"] != pid or not begin <= wall <= end:
            continue
        stack, stop, qualified, mono = [], None, False, None
        if row["native"]:
            header, *lines = row["native"].splitlines()
            mono, target, stop = map(int, header.split())
            # Project the signal-time monotonic clock onto the paired device wall clock.
            wall -= (row["sampleEndMonoNs"] - mono) / 1e9
            qualified = (row["sampleStartMonoNs"] <= mono <= row["sampleEndMonoNs"]
                         and target == dart_tid == pid and row["pendingAfterNative"] and begin <= wall <= end)
            stack = symbols.decode(lines)
        observed.append({"wall": wall, "probeAgeMs": row["ageMs"], "stopCode": stop,
                         "hit": qualified and subsequence(stack, phase(wall)), "stack": stack,
                         "captureMonoNs": mono, "qualified": qualified,
                         "reportId": str(row["session"]) + "-" + str(row["probe"])})
    times = [float(e.split()[0]) for e in case["am_anr"]
             if re.search(r"\[\d+," + str(pid) + "," + re.escape(PACKAGE) + ",", e)
             and "Input dispatching timed out" in e and begin <= float(e.split()[0]) <= end]
    hits = [s for s in observed if s["hit"]]
    delay = min((abs(s["wall"] - t) * 1000 for s in hits for t in times), default=None)
    system = system_stack(raw, case, symbols, begin, end)
    system_hit = bool(system and subsequence(system["stack"], phase(system["dumpWall"])))
    if name == "Short stall":
        passed = bool(hits) and not case["am_anr"] and end - begin < 5
    else:
        passed = len(hits) >= 2 and delay is not None and delay <= 1500 and (system_hit if api >= 30 else system is None)
    killed = foreground_anr_death(case)
    death_recorded = "anrKill" in case or "am_proc_died" in case["end"]
    if death_recorded:
        passed = passed and killed and name != "Short stall"
    if name == "Native only":
        passed = passed and not any(f["module"] == "libapp.so"
                    for o in observed if o["qualified"] for f in o["stack"])
        if system:
            passed = passed and not any(f["module"] == "libapp.so" for f in system["stack"])
    if name.startswith("Switch "):
        if "injectedWall" in case:
            expected_touch = False
            for event in case["am_anr"]:
                wait = re.search(r"Waited (\d+)ms for (?:MotionEvent|KeyEvent)", event)
                if wait and float(event.split()[0]) - case["injectedWall"] >= int(wait[1])/1000 - 0.5:
                    expected_touch = True
            passed = passed and expected_touch
        # A killed process cannot execute the second phase after its death.
        if switched is None:
            phases_observed = name == "Switch early" and killed and bool(system_hit) and len(hits) == len(observed)
        else:
            phases_observed = all(any(phase(o["wall"]) == frames for o in hits) for frames in (DART, FFI))
        passed = (passed and phases_observed and len(times) == 1 and phase(times[0]) == expected and
                  any(abs(o["wall"] - times[0]) <= 1.5 and phase(o["wall"]) == expected for o in hits))
    catalog_path = raw / "catalog.json"
    if catalog_path.exists():
        catalog = {r["id"]: r for r in json.loads(catalog_path.read_text())}
        report_ids = {o["reportId"] for o in hits}
        linked = catalog.get(next(iter(report_ids)), {}) if len(report_ids) == 1 else {}
        if api >= 30 and system:
            selected = next((o for o in hits if o["captureMonoNs"] == linked.get("sampleMonoNs")), None)
            passed = (passed and linked.get("association") == "matched" and selected is not None and
                      linked.get("exitReportId") == Path(system["traceFile"]).stem and
                      abs(linked["traceWallMs"]/1000 - system["dumpWall"]) < 0.002 and
                      abs((selected["wall"]-system["dumpWall"])*1000 - linked["sampleDeltaMs"]) < 3)
            if name.startswith("Switch "):
                passed = passed and phase(selected["wall"]) == phase(system["dumpWall"]) == expected
        else:
            passed = passed and linked.get("association") == "none"
    return {"case": name, "pid": pid, "dartTid": dart_tid, "passed": passed, "expected": expected,
            "endSource": ("foregroundAnrDeath" if killed else "unverifiedDeath") if death_recorded else "fixtureReturn",
            "switchObserved": switched is not None,
            "samples": len(observed), "hits": len(hits), "durationMs": round(1000*(end-begin)),
            "closestHitToSystemEventMs": round(delay) if delay is not None else None,
            "am_anr": case["am_anr"], "observations": observed, "systemTrace": system}


def persisted_interruption(raw, sample, symbols):
    pid = sample["pid"]
    begin = float(sample["begin"].split()[0])
    for file in raw.glob("*.jsonl"):
        rows = list(complete_records(file))
        if any(r.get("type") in ("ack", "lifecycle_end") for r in rows):
            continue
        for row in rows:
            if (row.get("type") != "stall_sample" or not row.get("native") or row["pid"] != pid or
                    not begin <= row["sampleWallMs"] / 1000 <= sample["stoppedWall"]):
                continue
            header, *pcs = row["native"].splitlines()
            mono, tid, _ = map(int, header.split())
            if (tid == pid and row["pendingAfterNative"] and
                    row["sampleStartMonoNs"] <= mono <= row["sampleEndMonoNs"] and
                    subsequence(symbols.decode(pcs), ["fixtureDartCompute", "fixtureDartCaller"])):
                return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["run", "llvm", "dart", "native", "report"]:
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--slow", type=Path, help="unstripped slow constructor ELF for interrupted runs")
    args = parser.parse_args()
    raw = args.run / "raw"
    objects = {"libapp.so": args.dart, "libstalltrace_fixture.so": args.native,
                                 "libc.so": args.run / "libc.so"}
    if args.slow:
        objects["libstalltrace_slow.so"] = args.slow
    symbols = Symbols(args.llvm, objects)
    rows = []
    for file in raw.glob("*.jsonl"):
        for row in samples_with_raw(complete_records(file)):
            if row["type"] == "stall_sample":
                rows.append(row)
    device = json.loads((args.run / 'device.json').read_text())
    cases = [verify_case(case, rows, symbols, raw, int(device['api']))
             for case in json.loads((args.run / "cases.json").read_text())]
    names = sorted(c["case"] for c in cases)
    expected = ["Dart CPU", "Native FFI", "Short stall"]
    if device.get("suiteVersion", 1) >= 2 or "Native only" in names:
        expected.append("Native only")
    if device.get("suiteVersion", 1) >= 3:
        expected.extend(["Switch early", "Switch late"])
        if not (raw / "catalog.json").is_file():
            parser.error("the run did not export its public API report catalog")
    passed = names == sorted(expected) and all(c["passed"] for c in cases)
    report = {"device": json.loads((args.run / "device.json").read_text()),
              "buildIds": symbols.ids, "cases": cases, "passed": passed}
    interrupted = args.run / "interrupted.json"
    if interrupted.exists():
        saved = persisted_interruption(raw, json.loads(interrupted.read_text()), symbols)
        report["interruptedSamplePersisted"] = saved
        passed = report["passed"] = passed and saved
    slow = args.run / "slow.json"
    if slow.exists() or (interrupted.exists() and device.get("suiteVersion", 1) >= 3):
        if not args.slow or not slow.exists():
            parser.error("slow-loader evidence and --slow ELF are required")
        fixture = json.loads(slow.read_text())
        begin = float(fixture["begin"].split()[0])
        segments = set()
        for file in raw.glob("*.jsonl"):
            durable = set()
            captured = list(complete_records(file))
            if any(r["type"] in ("ack", "lifecycle_end") for r in captured):
                continue
            for row in captured:
                if row.get("pid") != fixture["pid"] or not begin <= row.get("sampleWallMs",0)/1000 <= fixture["stoppedWall"]:
                    continue
                if row["type"] == "stall_raw" and row.get("mappings"):
                    durable.add(row["rawNative"].splitlines()[0])
                if row["type"] == "stall_sample" and row.get("native"):
                    header, *pcs = row["native"].splitlines()
                    mono, tid, _ = map(int, header.split())
                    if (header in durable and tid == fixture["pid"] and row["pendingAfterNative"] and
                            row["sampleStartMonoNs"] <= mono <= row["sampleEndMonoNs"] and
                            subsequence(symbols.decode(pcs), ["fixture_slow_constructor"])):
                        segments.add(file.name)
        intact = any(name.endswith("-tail1.jsonl") and name.replace("-tail1.jsonl", ".jsonl") in segments
                     for name in segments)
        passed = report["passed"] = passed and intact
        report["slowLoaderSegments"] = sorted(segments)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    for case in cases:
        print(case["case"], "PASS" if case["passed"] else "FAIL", case["hits"], "/", case["samples"],
              "closest ms:", case["closestHitToSystemEventMs"], "system trace:", bool(case["systemTrace"]))
    if "interruptedSamplePersisted" in report:
        print("Interrupted Dart sample persisted:", report["interruptedSamplePersisted"])
    if "slowLoaderSegments" in report:
        print("Slow-loader head and rotated segment:", intact, "decoded segments:", len(segments))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
