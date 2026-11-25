"""Exercise the fixtures with real input on a selected, dedicated Android test device."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

PKG = "dev.stalltrace.stalltrace_example"
COMPONENT = PKG + "/.MainActivity"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--adb", default="adb")
    ap.add_argument("--serial", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ffi-input-delay", type=float, default=0,
                    help="seconds before the second touch (0..8) in the FFI fixture")
    ap.add_argument("--check-interrupted", action="store_true",
                    help="also terminate one example process during a recorded Dart stall")
    args = ap.parse_args()
    if not 0 <= args.ffi_input_delay <= 8:
        ap.error("--ffi-input-delay must be between 0 and 8")
    args.out.mkdir(parents=True, exist_ok=False)

    def adb(*cmd, timeout=30):
        return subprocess.check_output([args.adb, "-s", args.serial, *cmd],
                                       timeout=timeout, text=True, errors="replace").strip()

    def logs(buffer, tag):
        return adb("logcat", "-d", "-b", buffer, "-v", "epoch", "-s", tag + ":I", "*:S")

    def launch():
        status = adb("shell", "am", "start", "-W", "-n", COMPONENT)
        for _ in range(30):
            try:
                return int(adb("shell", "pidof", PKG))
            except (ValueError, subprocess.CalledProcessError):
                time.sleep(0.2)
        raise RuntimeError("app did not start: " + status)

    def tap_label(label):
        for attempt in range(5):
            xml = adb("exec-out", "uiautomator", "dump", "/dev/tty")
            if "<hierarchy" in xml:
                root = ET.fromstring(xml[xml.index("<hierarchy"):xml.index("</hierarchy>") + 12])
                for n in root.iter("node"):
                    if n.get("package") == PKG and label in (n.get("text"), n.get("content-desc")):
                        x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds")))
                        if x2 > x1 and y2 > y1:
                            adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
                            return
                scroll = next((n for n in root.iter("node") if n.get("package") == PKG and
                               n.get("scrollable") == "true"), None)
                if scroll is not None and attempt > 0:
                    x1,y1,x2,y2 = map(int,re.findall(r"\d+",scroll.get("bounds")))
                    x = str((x1+x2)//2)
                    adb("shell","input","swipe",x,str(y2-(y2-y1)//4),x,str(y1+(y2-y1)//4),"350")
            time.sleep(0.4)
        raise RuntimeError("missing Flutter control: " + label)

    def mark(pid, phrase, after):
        for line in logs("main", "AnrLab").splitlines():
            fields = line.split()
            if (len(fields) > 5 and fields[1] == str(pid) and phrase in line
                    and float(fields[0]) >= after):
                return line
        return None

    def wait_mark(pid, phrase, after, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            line = mark(pid, phrase, after)
            if line:
                return line
            time.sleep(0.25)
        raise RuntimeError("missing app marker: " + phrase)

    def now():
        return float(adb("shell", "date", "+%s.%N"))

    def events(pid, since, until):
        matches = []
        for line in logs("events", "am_anr").splitlines():
            if re.search(r"\[\d+," + str(pid) + "," + re.escape(PKG) + r",", line):
                if since <= float(line.split()[0]) <= until:
                    matches.append(line)
        return matches

    device = {
        key: adb("shell", "getprop", prop) for key, prop in (
            ("fingerprint", "ro.build.fingerprint"), ("api", "ro.build.version.sdk"),
            ("abi", "ro.product.cpu.abi"))}
    device["suiteVersion"] = 3
    args.out.joinpath("device.json").write_text(json.dumps(device, indent=2))
    cases = []
    for name in ("Short stall", "Dart CPU", "Native FFI", "Native only", "Switch early", "Switch late"):
        # The first two stalls deliberately share a process. Only the second gets an ANR.
        if name != "Dart CPU":
            adb("shell", "am", "force-stop", PKG)
            pid = launch()
        start = now()
        tap_label(name)
        begin = wait_mark(pid, "begin " + name, start, 6)
        since = float(begin.split()[0])
        # A second touch is delivered to the already blocked, focused window.
        if name == "Native FFI":
            time.sleep(args.ffi_input_delay)
        if name == "Switch late":
            wait_mark(pid, "switch to native", since, 16)
        if int(device["api"]) < 30:
            # Older "input" waits for delivery. Queue another focused event
            # while the first injector is still waiting on the frozen window.
            with subprocess.Popen([args.adb, "-s", args.serial, "shell", "input",
                                   "tap", "100", "240"]) as pending_touch:
                time.sleep(0.8)
                adb("shell", "input", "keyevent", "KEYCODE_Q")
                pending_touch.wait(timeout=25)
        else:
            adb("shell", "input", "tap", "100", "240")
        end = wait_mark(pid, "end " + name, since, 28)
        until = float(end.split()[0])
        evidence = events(pid, since, until)
        case = {"name": name, "pid": pid, "begin": begin, "end": end, "am_anr": evidence}
        if name.startswith("Switch "):
            case["switched"] = wait_mark(pid, "switch to native", since, 3)
        cases.append(case)
        args.out.joinpath("cases.json").write_text(json.dumps(cases, indent=2) + "\n")
        print(name, pid, "system ANRs", len(evidence), flush=True)
    if args.check_interrupted:
        adb("shell", "am", "force-stop", PKG)
        pid = launch()
        start = now()
        tap_label("Dart CPU")
        begin = wait_mark(pid, "begin Dart CPU", start, 6)
        # Allow two watchdog samples to sync, then stop while the Dart loop is active.
        time.sleep(4.5)
        stopped = now()
        adb("shell", "am", "force-stop", PKG)
        if mark(pid, "end Dart CPU", start):
            raise RuntimeError("interruption fixture already returned")
        args.out.joinpath("interrupted.json").write_text(json.dumps({
            "pid": pid, "begin": begin, "stoppedWall": stopped}, indent=2))
        adb("shell", "am", "force-stop", PKG)
        pid = launch()
        start = now()
        tap_label("Slow load")
        begin = wait_mark(pid, "entered slow constructor", start, 8)
        # Cross the first 16-sample segment while the linker still holds its lock.
        time.sleep(36)
        stopped = now()
        adb("shell", "am", "force-stop", PKG)
        if mark(pid, "left slow constructor", start):
            raise RuntimeError("loader fixture already returned")
        args.out.joinpath("slow.json").write_text(json.dumps({
            "pid": pid, "begin": begin, "stoppedWall": stopped}, indent=2))
    # The probes were allowed to recover. A subsequent explicit stop is not
    # assumed to be an ANR exit. Collect the real historical reasons on restart.
    adb("shell", "am", "force-stop", PKG)
    pid = launch()
    start = now()
    tap_label("Export")
    exported = wait_mark(pid, "exported /", start, 6).split("exported ", 1)[1].strip()
    if not re.fullmatch(r"/storage/emulated/\d+/Android/data/" + re.escape(PKG) +
                        r"/files/export/[0-9a-f-]{36}", exported):
        raise RuntimeError("unexpected export destination")
    adb("pull", exported, str(args.out / "raw"))
    candidates = ("/apex/com.android.runtime/lib64/bionic/libc.so", "/system/lib64/libc.so")
    for path in candidates:
        if adb("shell", "sh", "-c", "'test -r " + path + " && echo yes || echo no'") == "yes":
            adb("pull", path, str(args.out / "libc.so"))
            break
    else:
        raise RuntimeError("No readable ARM64 device libc")


if __name__ == "__main__":
    main()
