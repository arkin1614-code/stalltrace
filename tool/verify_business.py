"""Verify a business_run export against independent input events, trace stacks and fixture markers."""

import argparse
import json
from pathlib import Path
import re

from business_run import CASES, PKG
from diagnose import diagnose, STALL
from symbolize import Symbols, complete_records

STORE = PKG + ".InventoryStore."
DART_FILE = "lib/business_scenarios.dart"
CHECKSUM_PER_PAGE = sum((i % 31) * (200 + i) for i in range(256))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp(line):
    return float(line.split()[0])


def field(line, name):
    match = re.search(r"\b" + name + r"=(\d+)\b", line)
    require(match, "missing " + name)
    return int(match[1])


def reports_for(raw, case, package):
    begin, end = stamp(case["begin"]), stamp(case["end"])
    reports = []
    for report in json.loads((raw / "catalog.json").read_text()):
        if report["kind"] != "stall":
            continue
        report_id = report["id"]
        require(STALL.fullmatch(report_id), "invalid report ID")
        for file in raw.glob(report_id + "*.jsonl"):
            if file.stem not in (report_id, report_id + "-tail0", report_id + "-tail1"):
                continue
            if any(
                row.get("pid") == case["pid"]
                and row.get("process") == package
                and begin <= row.get("sampleWallMs", 0) / 1000 <= end
                for row in complete_records(file)
            ):
                reports.append(report)
                break
    return reports


def check_case(case, package, raw, symbols, mapping, retrace):
    name, pid = case["name"], case["pid"]
    positive = name.endswith(" UI")
    family = name.split()[0]
    start, end = stamp(case["begin"]), stamp(case["end"])
    injected = case["injected"]
    require(
        start <= stamp(case["work"]) <= injected < end, "input did not overlap work"
    )
    require(
        field(case["begin"], "dartTid") == pid,
        "root isolate was not on the platform thread",
    )
    owner_tid = None
    row = {"name": name}
    died = "am_proc_died" in case["end"]
    if family == "JSON":
        pages = field(case["work"], "pages")
        row["pages"] = pages
        if not died:
            require(
                field(case["end"], "pages") == pages
                and field(case["end"], "checksum") == pages * CHECKSUM_PER_PAGE,
                "JSON checksum mismatch",
            )
    else:
        holder = [l for l in case["markers"] if "holder tid=" in l]
        require(len(holder) == 1, "missing independent holder marker")
        owner_tid = field(holder[0], "tid")
        writer_tid = field(case["work"], "tid")
        require(
            owner_tid != writer_tid
            and owner_tid != pid
            and (writer_tid == pid) == positive,
            "wrong holder or writer thread",
        )
        if not died:
            require(field(case["end"], "saved") == 1, "edit did not complete")
    reports = reports_for(raw, case, package)
    events = [line for line in case["events"] if "am_anr " in line or "am_anr:" in line]
    if not positive:
        probes = [stamp(l) for l in case["markers"] if "probe received" in l]
        require(
            not died and not events and not reports,
            "ANR, process death or stall during control",
        )
        require(
            len(probes) == 1 and injected - 0.001 <= probes[0] < end,
            "input did not reach UI during work",
        )
        row["probeMs"] = round((probes[0] - injected) * 1000)
        return row
    require(
        len(events) == 1
        and injected <= stamp(events[0]) <= end
        and re.search(r"\[\d+," + str(pid) + "," + re.escape(package) + ",", events[0])
        and "Input dispatching timed out" in events[0]
        and "MotionEvent" in events[0],
        "missing input ANR event for the tested PID",
    )
    require(
        len(reports) == 1 and reports[0]["association"] == "matched",
        "missing unique correlated stall",
    )
    report = reports[0]
    result = diagnose(raw, report["id"], symbols, mapping, retrace, PKG, [DART_FILE])
    system, live = result["system"], result["live"]
    require(
        system["pid"] == pid
        and system["process"] == package
        and injected <= system["wallMs"] / 1000 <= end,
        "system dump outside workload",
    )
    meta = json.loads((raw / (report["exitReportId"] + ".json")).read_text())
    subject = re.search(
        r"^Subject: (Input dispatching timed out .+)$",
        (raw / meta["traceFile"]).read_text(errors="replace"),
        re.M,
    )
    require(
        subject and subject[1] in events[0], "system subject differs from input event"
    )
    if died:
        require(
            meta["reasonAnr"]
            and any(
                "am_kill" in l
                and subject[1] in l
                and stamp(events[0]) <= stamp(l) <= end
                for l in case["events"]
            ),
            "missing system ANR death evidence",
        )
    row.update(
        reportId=report["id"],
        selection=live["sampleBasis"],
        deltaMs=result["sampleDeltaMs"],
        mechanism=result["mechanism"],
        mechanismSource=result["mechanismSource"],
        liveMechanism=live["mechanism"],
        systemMechanism=system["mechanism"],
        outcome="killed" if died else "recovered",
        liveApplication=live["applicationFrames"],
        systemApplication=system["applicationFrames"],
    )
    if family == "JSON":
        require(
            live["mechanism"] in ("dartJsonDecode", "dartAotExecution"),
            "missing Dart live stack",
        )
        for capture in (live, system):
            app = capture["applicationFrames"]["dart"]
            if capture is system and not app:
                # Missing system unwind is counted separately. The live application frame is required.
                continue
            require(
                app
                and app["function"] == "decodeStockPage"
                and 24 <= app["line"] <= 28,
                "missing Dart application source frame",
            )
            require(
                any(
                    f["function"] == "restoreInventoryCache"
                    and DART_FILE + ":35" in f["location"]
                    for f in capture["nativeStack"]
                ),
                "missing cache restore caller",
            )
    else:
        expected, method = (
            ("sqliteConnectionWait", "saveStockQuantity")
            if family == "SQLite"
            else ("javaMonitorContention", "applyPriceUpdate")
        )
        require(
            result["mechanism"] == expected and result["r8Mapping"] == "idMatched",
            "wrong mechanism or R8 mapping identity",
        )
        for capture in (live, system):
            frame = capture["applicationFrames"]["java"] or ""
            require(
                re.fullmatch(
                    re.escape(STORE + method)
                    + (
                        r"\(InventoryStore\.java:73\)"
                        if family == "SQLite"
                        else r"\(InventoryStore\.java:67\)"
                    ),
                    frame,
                ),
                "missing retraced application call",
            )
        if family == "Monitor":
            owner = system["owner"]
            require(
                owner
                and owner["thread"] == "inventory-sync"
                and owner["sysTid"] == owner_tid
                and owner["sameMonitorListed"]
                and any(
                    f.startswith(STORE + "syncInventory(") for f in owner["javaFrames"]
                ),
                "ART monitor owner, monitor address or execution site differs from fixture",
            )
        else:
            require(system["owner"] is None, "unsupported SQLite owner inference")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--dart-symbols", type=Path, required=True)
    parser.add_argument("--llvm", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--r8-jar", type=Path, required=True)
    parser.add_argument("--java", default="java")
    args = parser.parse_args()
    device = json.loads((args.run / "device.json").read_text())
    symbols = Symbols(
        args.llvm, {"libapp.so": args.dart_symbols, "libc.so": args.run / "libc.so"}
    )
    retrace = [
        args.java,
        "-cp",
        str(args.r8_jar),
        "com.android.tools.r8.retrace.Retrace",
    ]
    rounds = []
    for folder in sorted(args.run.glob("round-*")):
        cases = json.loads((folder / "cases.json").read_text())
        require(
            [c["name"] for c in cases] == list(CASES),
            "missing or unordered cases in " + folder.name,
        )
        rows = []
        for case in cases:
            try:
                rows.append(
                    check_case(
                        case,
                        device["package"],
                        folder / "raw",
                        symbols,
                        args.mapping,
                        retrace,
                    )
                )
            except ValueError as exc:
                raise ValueError(f"{folder.name}, {case['name']}: {exc}") from exc
        require(
            rows[-2]["pages"] == rows[-1]["pages"],
            "UI and isolate used different workloads",
        )
        rounds.append(rows)
    require(rounds, "no complete rounds")
    print(
        json.dumps(
            {"device": device["model"], "api": device["api"], "rounds": rounds},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
