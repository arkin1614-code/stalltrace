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
    ap.add_argument("--measure-idle", action="store_true",
                    help="run a 10-minute paused and 10-minute enabled idle comparison after export")
    ap.add_argument("--resume", action="store_true",
                    help="continue an unexported run on the same device and installed example build")
    ap.add_argument("--settle-anr-dialogs", action="store_true",
                    help="close the recovered example's own ANR dialog before launching another process")
    args = ap.parse_args()
    if not 0 <= args.ffi_input_delay <= 8:
        ap.error("--ffi-input-delay must be between 0 and 8")
    args.out.mkdir(parents=True, exist_ok=args.resume)

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

    def tap_node(node):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
        if x2 <= x1 or y2 <= y1:
            return False
        adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
        return True

    def tap_label(label):
        for attempt in range(8):
            xml = adb("exec-out", "uiautomator", "dump", "/dev/tty")
            if "<hierarchy" in xml:
                root = ET.fromstring(xml[xml.index("<hierarchy"):xml.index("</hierarchy>") + 12])
                nodes = list(root.iter("node"))
                # OEMs can keep a recovered ANR dialog above the next activity.
                wait = next((n for n in nodes if n.get("resource-id") == "android:id/aerr_wait"), None)
                if wait is not None:
                    focus = adb("shell", "dumpsys", "window")
                    if any("mCurrentFocus=" in line and "Application Not Responding: " + PKG in line
                           for line in focus.splitlines()):
                        tap_node(wait)
                        time.sleep(0.5)
                        continue
                    raise RuntimeError("unrelated system dialog is in front of the example")
                for n in nodes:
                    if n.get("package") == PKG and label in (n.get("text"), n.get("content-desc")) and tap_node(n):
                        return
                scroll = next((n for n in nodes if n.get("package") == PKG and
                               n.get("scrollable") == "true"), None)
                if scroll is not None and attempt > 0:
                    x1,y1,x2,y2 = map(int,re.findall(r"\d+",scroll.get("bounds")))
                    x = str((x1+x2)//2)
                    adb("shell","input","swipe",x,str(y2-(y2-y1)//4),x,str(y1+(y2-y1)//4),"350")
            time.sleep(0.4)
        raise RuntimeError("missing Flutter control: " + label)

    def settle_dialog(pid):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            focus = adb("shell","dumpsys","window")
            if any("mCurrentFocus=" in line and "Application Not Responding: " + PKG in line
                   for line in focus.splitlines()):
                xml = adb("exec-out","uiautomator","dump","/dev/tty")
                if "<hierarchy" not in xml: continue
                root = ET.fromstring(xml[xml.index("<hierarchy"):xml.index("</hierarchy>")+12])
                close = next((n for n in root.iter("node") if n.get("resource-id")=="android:id/aerr_close"),None)
                if close is not None and tap_node(close):
                    time.sleep(1)
                    return
            state = adb("shell","sh","-c","'pidof " + PKG + " || true'")
            if str(pid) not in state.split(): return
            time.sleep(.6)

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

    def trigger(label, pid, phrase, since, seconds=5):
        # A recovered ANR dialog can appear between reading the hierarchy and tapping.
        # Retry only if the sample app has not logged that it received the action.
        for attempt in range(3):
            existing = mark(pid, phrase, since)
            if existing: return existing
            tap_label(label)
            try:
                return wait_mark(pid, phrase, since, seconds)
            except RuntimeError:
                if attempt == 2 or adb("shell", "pidof", PKG) != str(pid): raise

    def require_fixture_focus():
        windows = adb("shell", "dumpsys", "window")
        if not any("mCurrentFocus=Window{" in line and PKG + "/" in line and
                   "Application Not Responding:" not in line for line in windows.splitlines()):
            raise RuntimeError("example lost input focus; no test input sent")

    def now():
        return float(adb("shell", "date", "+%s.%N"))

    def events(pid, since, until, tag="am_anr"):
        matches = []
        for line in logs("events", tag).splitlines():
            if re.search(r"\[\d+," + str(pid) + "," + re.escape(PKG) + r",", line):
                if since <= float(line.split()[0]) <= until:
                    matches.append(line)
        return matches

    def case_end(pid, name, since):
        deadline = time.monotonic() + 48
        while time.monotonic() < deadline:
            end = mark(pid, "end " + name, since)
            if end:
                return end, None
            deaths = events(pid, since, float("inf"), "am_proc_died")
            if deaths:
                until = float(deaths[0].split()[0])
                kills = events(pid, since, until, "am_kill")
                if name != "Short stall" and kills and events(pid, since, until):
                    return deaths[0], kills[-1]
                raise RuntimeError("example died before completing " + name + " without an ANR kill record")
            time.sleep(0.5)
        raise RuntimeError("missing end marker or recorded ANR death: " + name)

    device = {
        key: adb("shell", "getprop", prop) for key, prop in (
            ("fingerprint", "ro.build.fingerprint"), ("api", "ro.build.version.sdk"),
            ("abi", "ro.product.cpu.abi"))}
    device["suiteVersion"] = 3
    workloads = ("Short stall", "Dart CPU", "Native FFI", "Native only", "Switch early", "Switch late")
    device_file = args.out / "device.json"
    cases_file = args.out / "cases.json"
    cases = []
    if args.resume:
        if not device_file.is_file() or json.loads(device_file.read_text()) != device or (args.out/"raw").exists():
            ap.error("resume requires an unexported run from this same device")
        cases = json.loads(cases_file.read_text()) if cases_file.is_file() else []
        if [c["name"] for c in cases] != list(workloads[:len(cases)]):
            ap.error("saved cases do not form a complete prefix of this suite")
        if len(cases) == 1:
            cases = []  # Repeat the pair in one process.
    else:
        device_file.write_text(json.dumps(device, indent=2))
    for name in workloads[len(cases):]:
        # The first two stalls deliberately share a process. Only the second gets an ANR.
        if name != "Dart CPU":
            adb("shell", "am", "force-stop", PKG)
            pid = launch()
        start = now()
        begin = trigger(name, pid, "begin " + name, start)
        since = float(begin.split()[0])
        # A second touch is delivered to the already blocked, focused window.
        if name == "Native FFI":
            time.sleep(args.ffi_input_delay)
        if name == "Switch late":
            wait_mark(pid, "switch to native", since, 35)
        require_fixture_focus()
        injected = now()
        if int(device["api"]) < 30:
            # Older "input" waits for delivery. Queue another focused event
            # while the first injector is still waiting on the frozen window.
            with subprocess.Popen([args.adb, "-s", args.serial, "shell", "input",
                                   "tap", "100", "240"]) as pending_touch:
                time.sleep(0.8)
                require_fixture_focus()
                adb("shell", "input", "keyevent", "KEYCODE_Q")
                pending_touch.wait(timeout=25)
        else:
            adb("shell", "input", "tap", "100", "240", timeout=55)
        end, kill = case_end(pid, name, since)
        until = float(end.split()[0])
        evidence = events(pid, since, until)
        case = {"name": name, "pid": pid, "begin": begin, "end": end, "am_anr": evidence, "injectedWall": injected}
        if kill:
            case["anrKill"] = kill
        if name.startswith("Switch "):
            switch = mark(pid, "switch to native", since)
            if switch:
                case["switched"] = switch
            elif name != "Switch early" or not kill:
                raise RuntimeError("missing switch to native")
        cases.append(case)
        args.out.joinpath("cases.json").write_text(json.dumps(cases, indent=2) + "\n")
        print(name, pid, "system ANRs", len(evidence), flush=True)
        if args.settle_anr_dialogs and evidence and not kill:
            settle_dialog(pid)
    if args.check_interrupted:
        adb("shell", "am", "force-stop", PKG)
        pid = launch()
        start = now()
        begin = trigger("Dart CPU", pid, "begin Dart CPU", start)
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
        trigger("Slow load", pid, "begin Slow load", start)
        begin = wait_mark(pid, "entered slow constructor", start, 12)
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
    exported = trigger("Export", pid, "exported /", start, 8).split("exported ", 1)[1].strip()
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
    if args.measure_idle:
        tap_label("Measure overhead")
        for phase in ("paused","enabled"):
            prefix = "idle " + phase + " "
            deadline = time.monotonic() + 650
            while time.monotonic() < deadline:
                line = mark(pid,prefix,start)
                if line:
                    data = json.loads(line.split(prefix,1)[1])
                    args.out.joinpath("idle-"+phase+".json").write_text(json.dumps(data,indent=2)+"\n")
                    if not data["valid"]:
                        raise RuntimeError("idle measurement lost its foreground window")
                    print("Completed idle",phase,"measurement",flush=True)
                    break
                if mark(pid,"idle failed",start):
                    raise RuntimeError("device reported idle measurement failure")
                time.sleep(10)
            else:
                raise RuntimeError("missing idle benchmark result for " + phase)


if __name__ == "__main__":
    main()
