"""Decode application samples or Android ANR main-thread traces with build-matched ELFs."""
import argparse
import bisect
import functools
import json
from pathlib import Path
import re
import subprocess


def llvm_output(tool, *args):
    return subprocess.check_output([tool, *args], text=True)


class Symbols:
    def __init__(self, llvm, objects):
        self.llvm = llvm
        self.objects = objects
        self.ids = {}
        for module, path in self.objects.items():
            notes = llvm_output(llvm / "llvm-readelf", "-n", path)
            build_id = re.search(r"Build ID: ([a-fA-F0-9]+)", notes)
            if not build_id:
                raise ValueError(f"no ELF build ID in {path}")
            self.ids[module] = build_id[1].lower()

    @functools.lru_cache(maxsize=8)
    def frameless_ranges(self, module):
        # Accept only the simple AArch64 leaf recipe for the entire FDE.
        # More complex CFI, absent CFI and non-platform modules skip the LR.
        dump = llvm_output(self.llvm / "llvm-dwarfdump", "--eh-frame", self.objects[module])
        entries = re.split(r"\n(?=[0-9a-f]+ [0-9a-f]+ [0-9a-f]+ (?:CIE|FDE))",
                           dump.partition(".eh_frame contents:")[2])
        cies = set()
        ranges = []
        for entry in entries:
            head = entry.split("\n", 1)[0]
            cie = re.match(r"([0-9a-f]+) \S+ \S+ CIE$", head)
            if cie and re.search(r"Return address column:\s+30\b", entry):
                cies.add(cie[1])
            fde = re.search(r"FDE cie=(\w+) pc=([\da-f]+)\.\.\.([\da-f]+)", head)
            rows = re.findall(r"^\s+0x[0-9a-f]+: (.+)$", entry, re.M)
            if fde and fde[1] in cies and rows and all(r.strip() == "CFA=WSP" for r in rows):
                ranges.append((int(fde[2], 16), int(fde[3], 16)))
        return sorted(ranges)

    def lr_is_caller(self, pc):
        _, module, identity, vaddr = pc.split()
        if module != "libc.so" or identity != self.ids.get(module):
            return False
        ranges = self.frameless_ranges(module)
        addr = int(vaddr, 16)
        i = bisect.bisect_right(ranges, (addr, float("inf"))) - 1
        return i >= 0 and addr < ranges[i][1]

    @functools.lru_cache(maxsize=8192)
    def resolve(self, frame):
        kind, module, identity, vaddr = frame.split()
        if module not in self.objects:
            return [{"module": module, "pcKind": kind, "vaddr": "0x"+vaddr, "function": "??", "location": "??"}]
        if identity != self.ids[module]:
            raise ValueError(f"{module} build ID mismatch: sample {identity}, symbols {self.ids[module]}")
        lines = llvm_output(self.llvm / "llvm-addr2line", "-f", "-C", "-i", "-e",
                            self.objects[module], "0x" + vaddr).splitlines()
        return [{"module": module, "pcKind": kind, "vaddr": "0x" + vaddr,
                 "function": function, "location": location}
                for function, location in zip(lines[::2], lines[1::2])]

    def decode(self, lines):
        stack = []
        for line in lines:
            if line.startswith("lr "):
                if not self.lr_is_caller(lines[0]):
                    continue
                line = "cfi-lr " + line.split(" ", 1)[1]
            stack.extend(self.resolve(line))
        return stack


def main_thread_dumps(trace, symbols):
    ids = {v: k for k, v in symbols.ids.items()}
    sections = re.finditer(r"^----- pid (\d+) at ([^\n]+) -----\nCmd line: ([^\n]+)\n"
                           r"(.*?)(?=^----- end |^----- pid |\Z)", trace, re.M | re.S)
    for section in sections:
        pid, stamp, process, threads = section.groups()
        main = re.search(r'^"main"[^\n]*\n(.*?)(?:\n\s*\n|\Z)', threads, re.M | re.S)
        if not main or not re.search(rf"\bsysTid={pid}\b", main[1]):
            continue
        stack = []
        for line in main[1].splitlines():
            frame = re.search(r"native: #(\d+) pc ([\da-fA-F]+)\s+(\S+)", line)
            if not frame:
                continue
            identity = re.search(r"\(BuildId: ([0-9a-fA-F]+)\)", line)
            key = identity[1].lower() if identity else "-"
            module = ids.get(key, Path(frame[3]).name)
            kind = "system-pc" if int(frame[1]) == 0 else "system-call"
            # Android already adjusted saved return addresses to caller PCs.
            stack.extend(symbols.resolve(f"{kind} {module} {key} {frame[2]}"))
        yield {"pid": int(pid), "process": process, "stamp": stamp, "stack": stack}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="an exported .jsonl or .trace")
    ap.add_argument("--llvm", type=Path, required=True, help="NDK LLVM bin directory")
    ap.add_argument("--elf", action="append", required=True, metavar="MODULE=PATH")
    args = ap.parse_args()
    objects = {}
    for spec in args.elf:
        name, sep, path = spec.partition("=")
        if not name or not sep or not path or name in objects:
            ap.error("supply one MODULE=PATH per module")
        objects[name] = Path(path)
    symbols = Symbols(args.llvm, objects)
    if args.input.suffix == ".trace":
        for dump in main_thread_dumps(args.input.read_text(errors="replace"), symbols):
            print(json.dumps(dump))
        return
    with args.input.open() as stream:
        for line in stream:
            if not line.endswith("\n"):  # a process may die during its last append
                break
            row = json.loads(line)
            if row.get("type") == "stall_sample" and row.get("native"):
                header, *pcs = row["native"].splitlines()
                mono, tid, stop = map(int, header.split())
                row.update(captureMonoNs=mono, tid=tid, stopCode=stop, stack=symbols.decode(pcs))
            print(json.dumps(row))


if __name__ == "__main__":
    main()
