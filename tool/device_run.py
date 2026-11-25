"""Exercise the three fixtures with real input on a dedicated Android emulator."""
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
    args = ap.parse_args()
    if not 0 <= args.ffi_input_delay <= 8:
        ap.error("--ffi-input-delay must be between 0 and 8")
    if not re.fullmatch(r"emulator-\d+", args.serial):
        ap.error("select a dedicated emulator for the input-injection experiment")
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
        for _ in range(5):
            xml = adb("exec-out", "uiautomator", "dump", "/dev/tty")
            if "<hierarchy" in xml:
                root = ET.fromstring(xml[xml.index("<hierarchy"):xml.index("</hierarchy>") + 12])
                for n in root.iter("node"):
                    if n.get("package") == PKG and label in (n.get("text"), n.get("content-desc")):
                        x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds")))
                        adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
                        return
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

    args.out.joinpath("device.json").write_text(json.dumps({
        key: adb("shell", "getprop", prop) for key, prop in (
            ("fingerprint", "ro.build.fingerprint"), ("api", "ro.build.version.sdk"),
            ("abi", "ro.product.cpu.abi"))}, indent=2))
    cases = []
    for name in ("Short stall", "Dart CPU", "Native FFI"):
        adb("shell", "am", "force-stop", PKG)
        pid = launch()
        start = now()
        tap_label(name)
        begin = wait_mark(pid, "begin " + name, start, 6)
        since = float(begin.split()[0])
        # A second touch is delivered to the already blocked, focused window.
        if name == "Native FFI":
            time.sleep(args.ffi_input_delay)
        adb("shell", "input", "tap", "100", "240")
        end = wait_mark(pid, "end " + name, since, 28)
        until = float(end.split()[0])
        evidence = events(pid, since, until)
        case = {"name": name, "pid": pid, "begin": begin, "end": end, "am_anr": evidence}
        cases.append(case)
        args.out.joinpath("cases.json").write_text(json.dumps(cases, indent=2) + "\n")
        print(name, pid, "system ANRs", len(evidence), flush=True)
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
    adb("pull", "/apex/com.android.runtime/lib64/bionic/libc.so", str(args.out / "libc.so"))


if __name__ == "__main__":
    main()
