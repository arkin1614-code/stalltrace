"""Explain one exported stall using the selected live sample and its primary Android dump."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess

from symbolize import Symbols, complete_records, main_thread_dumps, samples_with_raw

STALL = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-\d+")
HEADER = re.compile(r"^----- pid (\d+) at (.+?) -----\nCmd line: ([^\n]+)\n", re.M)
THREAD = re.compile(
    r'^"([^"\n]*)"[^\n]*?\btid=(\d+)\b([^\n]*)\n(.*?)(?=^"|^----- |\Z)', re.M | re.S
)
WAIT = re.compile(r"- waiting to lock <(0x[0-9a-fA-F]+)>[^\n]*? held by thread (\d+)\b")


def java_frames(body):
    return [
        line.strip()[3:] for line in body.splitlines() if line.strip().startswith("at ")
    ]


def primary_dump(text, pid, process, stamp, symbols):
    first = re.search(r"^----- pid ", text, re.M)
    header = HEADER.match(text, first.start()) if first else None
    if not header or int(header[1]) != pid or header[3] != process:
        return None
    if not re.search(
        r"^Subject: Input dispatching timed out\b", text[: header.start()], re.M
    ):
        return None
    moment = re.sub(r"(\.\d{6})\d+", r"\1", header[2])
    try:
        observed = datetime.strptime(moment, "%Y-%m-%d %H:%M:%S.%f%z")
    except ValueError:
        return None
    observed_ms = int(observed.timestamp()) * 1000 + observed.microsecond // 1000
    if observed_ms != stamp:
        return None
    body = re.split(
        r"^----- (?:end|pid) ", text[header.end() :], maxsplit=1, flags=re.M
    )[0]
    threads = list(THREAD.finditer(body))
    main = [
        t
        for t in threads
        if t[1] == "main" and re.search(r"\bsysTid=" + str(pid) + r"\b", t[4])
    ]
    if len(main) != 1:
        return None
    main = main[0]
    dump = next(main_thread_dumps(header[0] + body, symbols), None)
    if dump is None:
        return None
    owner = None
    wait = WAIT.search(main[4])
    if wait:
        candidates = [t for t in threads if t[2] == wait[2] and t[2] != main[2]]
        if len(candidates) == 1:
            t = candidates[0]
            sys_tid = re.search(r"\bsysTid=(\d+)\b", t[4])
            owner = dict(
                thread=t[1],
                artTid=int(t[2]),
                sysTid=int(sys_tid[1]) if sys_tid else None,
                javaFrames=java_frames(t[4]),
                sameMonitorListed=bool(
                    re.search(r"- locked <" + re.escape(wait[1]) + ">", t[4])
                ),
            )
    return dict(
        pid=pid,
        process=process,
        wallMs=stamp,
        mainJava=java_frames(main[4]),
        nativeStack=dump["stack"],
        mainThreadState=main[3].strip(),
        monitorWait=bool(wait),
        owner=owner,
    )


def capture_candidates(raw, report_id):
    # Java sampling follows the native acquisition on the same probe. Never cross a segment boundary.
    for name in [
        report_id + ".jsonl",
        report_id + "-tail0.jsonl",
        report_id + "-tail1.jsonl",
    ]:
        path = raw / name
        if not path.is_file():
            continue
        pending = None
        for row in samples_with_raw(complete_records(path)):
            if row.get("type") == "java_sample" and pending is not None:
                if (
                    row.get("pendingAfterJava")
                    and row.get("monoNs", -1) >= pending["sampleEndMonoNs"]
                ):
                    yield pending, row
                else:
                    yield pending, None
                pending = None
            else:
                if pending is not None:
                    yield pending, None
                pending = None
                if (
                    row.get("type") == "stall_sample"
                    and row.get("pendingAfterNative")
                    and str(row.get("session")) + "-" + str(row.get("probe"))
                    == report_id
                ):
                    pending = row
        if pending is not None:
            yield pending, None


def header_of(row):
    payload = row.get("native") or row.get("rawNative")
    if not payload:
        return None
    mono, tid, stop = map(int, payload.splitlines()[0].split())
    if (
        tid != row["pid"]
        or not row["sampleStartMonoNs"] <= mono <= row["sampleEndMonoNs"]
    ):
        return None
    return mono, tid, stop


def retrace(groups, mapping, command):
    ids = {
        id_
        for group in groups
        for f in group
        for id_ in re.findall(r"\(r8-map-id-([\w.-]+)(?=[:)])", f)
    }
    with mapping.open() as stream:
        map_id = None
        for line in stream:
            if not line.startswith("#"):
                break
            if line.startswith("# pg_map_id:"):
                map_id = line.partition(":")[2].strip()
    if ids and ids != {map_id}:
        raise ValueError(
            "R8 mapping ID mismatch; use the mapping.txt archived with this APK"
        )
    markers = [f"STALLTRACE_GROUP_{i}" for i in range(len(groups))]
    lines = []
    for marker, frames in zip(markers, groups):
        lines.append(marker)
        lines.extend("    at " + f for f in frames)
    output = subprocess.run(
        [*command, "--quiet", str(mapping)],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    index = -1
    restored = [[] for _ in groups]
    for line in output.splitlines():
        s = line.strip()
        if s in markers:
            index = markers.index(s)
        elif index >= 0 and s:
            restored[index].append(s.removeprefix("at "))
    if not all(marker in output.splitlines() for marker in markers):
        raise ValueError("R8 retrace did not preserve stack boundaries")
    for group, result in zip(groups, restored):
        if group and not result:
            raise ValueError("R8 retrace returned an empty stack")
        group[:] = result
    return "idMatched" if ids else "suppliedWithoutFrameId"


def classify(java, native, monitor):
    if monitor:
        return "javaMonitorContention"
    if any(
        f.startswith("android.database.sqlite.SQLiteConnectionPool.waitForConnection(")
        for f in java
    ):
        return "sqliteConnectionWait"
    if any(f.startswith("android.os.BinderProxy.transact") for f in java):
        return "binderTransaction"
    if any(
        f.get("module") == "libapp.so"
        and re.search(
            r"(?:^|/)(?:convert/json|convert_patch)\.dart:\d+", f.get("location", "")
        )
        and re.search(r"JsonParser|JsonDecoder|_parseJson|jsonDecode", f["function"])
        for f in native
    ):
        return "dartJsonDecode"
    if any(
        f.get("module") == "libapp.so"
        and re.search(r"\.dart:\d+", f.get("location", ""))
        for f in native
    ):
        return "dartAotExecution"
    return "undetermined"


def application_frames(java, native, java_package, dart_files):
    java_frame = next(
        (f for f in java if java_package and f.startswith(java_package + ".")), None
    )
    for frame in native:
        source = re.fullmatch(r"(.+?):(\d+)(?::\d+)?", frame.get("location", ""))
        if frame.get("module") != "libapp.so" or not source or int(source[2]) == 0:
            continue
        for path in dart_files:
            if source[1] == path or source[1].endswith("/" + path):
                return {
                    "java": java_frame,
                    "dart": {
                        "function": frame["function"],
                        "file": path,
                        "line": int(source[2]),
                    },
                }
    return {"java": java_frame, "dart": None}


def diagnose(
    raw,
    report_id,
    symbols,
    mapping=None,
    command=None,
    java_package=None,
    dart_files=(),
):
    if not STALL.fullmatch(report_id):
        raise ValueError("invalid stall ID")
    catalog = json.loads((raw / "catalog.json").read_text())
    entries = [
        r for r in catalog if r.get("id") == report_id and r.get("kind") == "stall"
    ]
    if len(entries) != 1:
        raise ValueError("expected exactly one catalog entry for the stall")
    report = entries[0]
    association = report.get("association", report.get("traceAssociation", "none"))
    candidates = sorted(
        capture_candidates(raw, report_id),
        key=lambda pair: pair[0]["sampleStartMonoNs"],
    )
    if not candidates:
        raise ValueError("no pending live sample in the exported segments")
    if association == "matched":
        target = report.get("sampleMonoNs")
        eligible = [
            p for p in candidates if header_of(p[0]) and header_of(p[0])[0] == target
        ]
        if len(eligible) != 1:
            raise ValueError("selected capture is missing or nonunique in the export")
        sample, java = eligible[0]
    else:
        # The middle available capture is an observation while the probe was pending.
        sample, java = candidates[len(candidates) // 2]
    header = header_of(sample)
    mono = header[0] if header else None
    live = dict(
        captureMonoNs=mono,
        sampleBasis=report.get("sampleSelection")
        if association == "matched"
        else "middleObservedSample",
        javaDelayMs=(java["monoNs"] - mono) / 1e6
        if java and mono is not None
        else None,
        javaFrames=list(java["frames"]) if java else [],
        nativeStack=[],
    )
    if header and sample.get("native"):
        live["nativeStack"] = symbols.decode(sample["native"].splitlines()[1:])
    system = None
    if association == "matched":
        pid = sample["pid"]
        exit_id = report.get("exitReportId")
        if not isinstance(exit_id, str) or not re.fullmatch(
            r"exit-\d+-" + str(pid), exit_id
        ):
            raise ValueError("invalid associated exit ID")
        meta = json.loads((raw / (exit_id + ".json")).read_text())
        if (
            meta.get("pid") != pid
            or meta.get("process") != sample.get("process")
            or meta.get("traceFile") != exit_id + ".trace"
        ):
            raise ValueError("system exit identity does not match the live capture")
        system = primary_dump(
            (raw / meta["traceFile"]).read_text(errors="replace"),
            pid,
            meta["process"],
            report.get("traceWallMs"),
            symbols,
        )
        if system is None:
            raise ValueError(
                "primary system dump does not match the catalog and live capture"
            )
    groups = [live["javaFrames"]]
    if system:
        groups.append(system["mainJava"])
        if system["owner"]:
            groups.append(system["owner"]["javaFrames"])
    status = retrace(groups, mapping, command) if mapping else "notSupplied"
    if java_package or dart_files:
        live["applicationFrames"] = application_frames(
            live["javaFrames"], live["nativeStack"], java_package, dart_files
        )
        if system:
            system["applicationFrames"] = application_frames(
                system["mainJava"], system["nativeStack"], java_package, dart_files
            )
            if system["owner"]:
                system["owner"]["applicationFrames"] = application_frames(
                    system["owner"]["javaFrames"], [], java_package, ()
                )
    live["mechanism"] = classify(live["javaFrames"], live["nativeStack"], False)
    if system:
        system["mechanism"] = classify(
            system["mainJava"], system["nativeStack"], system["monitorWait"]
        )
    use_system = system is not None and system["mechanism"] != "undetermined"
    return dict(
        reportId=report_id,
        association=association,
        sampleDeltaMs=report.get("sampleDeltaMs"),
        mechanism=system["mechanism"] if use_system else live["mechanism"],
        mechanismSource="primarySystemDump" if use_system else "liveObservation",
        r8Mapping=status,
        live=live,
        system=system,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("export", type=Path)
    p.add_argument("stall")
    p.add_argument("--llvm", type=Path)
    p.add_argument("--elf", action="append", default=[], metavar="MODULE=PATH")
    p.add_argument("--mapping", type=Path)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--retrace", help="Android SDK retrace executable")
    g.add_argument(
        "--r8-jar", type=Path, help="JAR containing the official R8 Retrace CLI"
    )
    p.add_argument("--java", default="java")
    p.add_argument(
        "--java-package",
        help="surface the first Java frame in this application package",
    )
    p.add_argument(
        "--dart-file",
        action="append",
        default=[],
        help="surface the first Dart frame in these source files (exact path suffix, repeatable)",
    )
    args = p.parse_args()
    if args.elf and not args.llvm:
        p.error("--elf requires --llvm")
    if bool(args.mapping) != bool(args.retrace or args.r8_jar):
        p.error("pair --mapping with --retrace or --r8-jar")
    modules = {}
    for spec in args.elf:
        name, sep, path = spec.partition("=")
        if not name or not sep or not path or name in modules:
            p.error("invalid or duplicate MODULE=PATH")
        modules[name] = Path(path)
    command = (
        [args.java, "-cp", str(args.r8_jar), "com.android.tools.r8.retrace.Retrace"]
        if args.r8_jar
        else [args.retrace]
    )
    result = diagnose(
        args.export,
        args.stall,
        Symbols(args.llvm, modules),
        args.mapping,
        command,
        args.java_package,
        args.dart_file,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
