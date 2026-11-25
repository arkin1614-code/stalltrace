"""Verify device/trace identity, ELF IDs, capture times and ordered fixture frames."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from symbolize import Symbols, main_thread_dumps

PACKAGE = "dev.stalltrace.stalltrace_example"


def subsequence(frames, expected):
    i = 0
    for frame in frames:
        if (i < len(expected) and frame["function"] == expected[i] and
                re.search(r":[1-9]\d*(?::\d+)?$", frame["location"])):
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


def verify_case(case, rows, symbols, raw):
    name, pid = case["name"], case["pid"]
    begin, end = [float(case[key].split()[0]) for key in ("begin", "end")]
    dart_tid = int(re.search(r"dartTid=(\d+)", case["begin"])[1])
    expected = (["fixture_native_wait", "fixture_native_entry", "fixtureDartFfiCaller"]
                if name == "Native FFI" else ["fixtureDartCompute", "fixtureDartCaller"])
    observed = []
    for row in rows:
        wall = row["sampleWallMs"] / 1000
        if row["pid"] != pid or not begin <= wall <= end:
            continue
        stack, stop, qualified = [], None, False
        if row["native"]:
            header, *lines = row["native"].splitlines()
            mono, target, stop = map(int, header.split())
            # Project the signal-time monotonic clock onto the paired device wall clock.
            wall -= (row["sampleEndMonoNs"] - mono) / 1e9
            qualified = (row["sampleStartMonoNs"] <= mono <= row["sampleEndMonoNs"]
                         and target == dart_tid == pid and row["pendingAfterNative"] and begin <= wall <= end)
            stack = symbols.decode(lines)
        observed.append({"wall": wall, "probeAgeMs": row["ageMs"], "stopCode": stop,
                         "hit": qualified and subsequence(stack, expected), "stack": stack})
    times = [float(e.split()[0]) for e in case["am_anr"]
             if re.search(r"\[\d+," + str(pid) + "," + re.escape(PACKAGE) + ",", e)
             and "Input dispatching timed out" in e and begin <= float(e.split()[0]) <= end]
    hits = [s for s in observed if s["hit"]]
    delay = min((abs(s["wall"] - t) * 1000 for s in hits for t in times), default=None)
    system = system_stack(raw, case, symbols, begin, end)
    system_hit = bool(system and subsequence(system["stack"], expected))
    if name == "Short stall":
        passed = bool(hits) and not case["am_anr"] and end - begin < 5
    else:
        passed = len(hits) >= 2 and delay is not None and delay <= 1500 and system_hit
    return {"case": name, "pid": pid, "dartTid": dart_tid, "passed": passed, "expected": expected,
            "samples": len(observed), "hits": len(hits), "durationMs": round(1000*(end-begin)),
            "closestHitToSystemEventMs": round(delay) if delay is not None else None,
            "am_anr": case["am_anr"], "observations": observed, "systemTrace": system}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["run", "llvm", "dart", "native", "report"]:
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    raw = args.run / "raw"
    symbols = Symbols(args.llvm, {"libapp.so": args.dart, "libstalltrace_fixture.so": args.native,
                                 "libc.so": args.run / "libc.so"})
    rows = []
    for file in raw.glob("*.jsonl"):
        for line in file.read_text().splitlines():
            row = json.loads(line)
            if row["type"] == "stall_sample":
                rows.append(row)
    cases = [verify_case(case, rows, symbols, raw)
             for case in json.loads((args.run / "cases.json").read_text())]
    passed = sorted(c["case"] for c in cases) == sorted(["Dart CPU", "Native FFI", "Short stall"]) and all(
        c["passed"] for c in cases)
    report = {"device": json.loads((args.run / "device.json").read_text()),
              "buildIds": symbols.ids, "cases": cases, "passed": passed}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    for case in cases:
        print(case["case"], "PASS" if case["passed"] else "FAIL", case["hits"], "/", case["samples"],
              "closest ms:", case["closestHitToSystemEventMs"], "system trace:", bool(case["systemTrace"]))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
