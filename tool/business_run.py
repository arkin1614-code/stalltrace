#!/usr/bin/env python3
"""Run paired opt-in business scenarios on a dedicated Android test device."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

PKG = "dev.stalltrace.stalltrace_example"
ACTIVITY = PKG + ".MainActivity"
CASES = (
    "SQLite worker",
    "SQLite UI",
    "Monitor worker",
    "Monitor UI",
    "JSON isolate",
    "JSON UI",
)


class Device:
    def __init__(self, adb, serial, package):
        self.command = [adb, "-s", serial]
        self.package = package

    def adb(self, *args, timeout=40):
        return subprocess.check_output(
            self.command + list(args), text=True, errors="replace", timeout=timeout
        ).strip()

    def now(self):
        return float(self.adb("shell", "date", "+%s.%N"))

    def launch(self):
        self.adb("shell", "am", "force-stop", self.package)
        self.adb("shell", "am", "start", "-W", "-n", self.package + "/" + ACTIVITY)
        return int(self.adb("shell", "pidof", self.package))

    def logs(self, *tags, buffer="main"):
        return self.adb(
            "logcat",
            "-d",
            "-b",
            buffer,
            "-v",
            "epoch",
            "-s",
            *(t + ":I" for t in tags),
            "*:S",
        ).splitlines()

    def marker(self, pid, label, since, timeout=15, allow_death=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.logs("AnrLab", "ScenarioLab"):
                fields = line.split()
                if (
                    len(fields) > 5
                    and fields[1] == str(pid)
                    and label in line
                    and float(fields[0]) >= since
                ):
                    return line
            if allow_death:
                died = self.events(pid, since, float("inf"), "am_proc_died")
                if died:
                    return died[0]
            time.sleep(0.25)
        raise RuntimeError("Missing fixture marker " + label)

    def events(self, pid, start, end, *tags):
        return [
            l
            for l in self.logs(*tags, buffer="events")
            if re.search(r"\[\d+," + str(pid) + "," + re.escape(self.package) + ",", l)
            and start <= float(l.split()[0]) <= end
        ]

    def focus(self):
        return next(
            (
                l
                for l in self.adb("shell", "dumpsys", "window").splitlines()
                if "mCurrentFocus=" in l
            ),
            "",
        )

    def controls(self, labels):
        for _ in range(8):
            data = self.adb("exec-out", "uiautomator", "dump", "/dev/tty")
            if "<hierarchy" not in data:
                continue
            nodes = ET.fromstring(
                data[data.index("<hierarchy") : data.index("</hierarchy>") + 12]
            ).iter("node")
            found = {}
            for n in nodes:
                bounds = list(map(int, re.findall(r"\d+", n.get("bounds", ""))))
                if len(bounds) != 4:
                    continue
                a, b, c, d = bounds
                if c <= a or d <= b:
                    continue
                pos = (str((a + c) // 2), str((b + d) // 2))
                if (
                    n.get("resource-id") == "android:id/aerr_wait"
                    and "Application Not Responding: " + self.package in self.focus()
                ):
                    self.adb("shell", "input", "tap", *pos)
                    break
                if n.get("package") == self.package:
                    for label in labels:
                        if label in (n.get("content-desc"), n.get("text")):
                            found[label] = pos
            if all(k in found for k in labels):
                return found
            time.sleep(0.4)
        raise RuntimeError("Example controls unavailable: " + str(labels))

    def tap(self, pos):
        focus = self.focus()
        if self.package + "/" not in focus or "Application Not Responding:" in focus:
            raise RuntimeError("Example lost input focus before touch")
        self.adb("shell", "input", "tap", *pos, timeout=90)

    def run(self, name):
        pid = self.launch()
        controls = self.controls((name, "Probe input"))
        start = self.now()
        self.tap(controls[name])
        begin = self.marker(pid, "begin " + name, start)
        since = float(begin.split()[0])
        work = self.marker(
            pid, "work " + name if name.startswith("JSON") else "writer tid=", since
        )
        injected = self.now()
        self.tap(controls["Probe input"])
        end = self.marker(pid, "end " + name, since, timeout=100, allow_death=True)
        until = float(end.split()[0])
        events = self.events(pid, since, until, "am_anr", "am_kill", "am_proc_died")
        markers = [
            l
            for l in self.logs("AnrLab", "ScenarioLab")
            if len(l.split()) > 5
            and l.split()[1] == str(pid)
            and since <= float(l.split()[0]) <= until
            and any(m in l for m in ("holder tid=", "probe received", "writer tid="))
        ]
        return dict(
            name=name,
            pid=pid,
            begin=begin,
            work=work,
            injected=injected,
            end=end,
            events=events,
            markers=markers,
        )

    def export(self, out):
        pid = self.launch()
        controls = self.controls(("Export",))
        start = self.now()
        self.tap(controls["Export"])
        path = self.marker(pid, "exported /", start).split("exported ", 1)[1]
        if not re.fullmatch(
            r"/storage/emulated/\d+/Android/data/"
            + re.escape(self.package)
            + "/files/export/[0-9a-f-]{36}",
            path,
        ):
            raise RuntimeError("Unexpected export path")
        self.adb("pull", path, str(out / "raw"))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adb", default="adb")
    p.add_argument("--serial", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--package", default=PKG)
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--install", type=Path)
    args = p.parse_args()
    if not 1 <= args.rounds <= 10:
        p.error("rounds must be 1..10")
    if not re.fullmatch(r"[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+", args.package):
        p.error("invalid package")
    args.out.mkdir(parents=True, exist_ok=False)
    device = Device(args.adb, args.serial, args.package)
    if args.install:
        install = device.adb("install", "-r", str(args.install), timeout=150)
        if "Success" not in install:
            raise RuntimeError(install)
    facts = {
        key: device.adb("shell", "getprop", prop)
        for key, prop in (
            ("model", "ro.product.model"),
            ("api", "ro.build.version.sdk"),
            ("abi", "ro.product.cpu.abi"),
        )
    }
    facts["package"] = args.package
    (args.out / "device.json").write_text(json.dumps(facts, indent=2) + "\n")
    device.adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    pid = device.launch()
    try:
        control = device.controls(("Prepare cache",))
        start = device.now()
        device.tap(control["Prepare cache"])
        print(device.marker(pid, "end Prepare cache", start, timeout=30), flush=True)
        for n in range(1, args.rounds + 1):
            folder = args.out / f"round-{n}"
            folder.mkdir()
            cases = []
            for name in CASES:
                case = device.run(name)
                cases.append(case)
                (folder / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
                print(
                    n,
                    name,
                    "system ANR",
                    sum("am_anr" in l for l in case["events"]),
                    flush=True,
                )
            device.export(folder)
        device.adb(
            "pull",
            "/apex/com.android.runtime/lib64/bionic/libc.so",
            str(args.out / "libc.so"),
        )
    finally:
        device.adb("shell", "am", "force-stop", args.package)


if __name__ == "__main__":
    main()
