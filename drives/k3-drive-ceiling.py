#!/usr/bin/env python3
"""Standalone read ceiling of one expert directory, engine idle.

Operator question, 2026-09-08: "internal is often near its cap and Yellow is under-utilised".
Under the engine every drive runs at ~84% of its own observed peak, so the
question is whether Yellow's observed peak (5.0 GB/s) IS the drive, or the
router under-driving it. This reads expert files the way the engine does
(whole ~17.5 MB files, F_NOCACHE, many in flight) with nothing else running.

Two numbers per run: application bytes/s (what the reader saw) and the
DEVICE bytes/s from the dashboard sampler's counters over the same window
(what the disk actually delivered; immune to page-cache hits).

Usage: python3 k3-drive-ceiling.py <dir> <seconds> <threads> [label]
"""
import fcntl
import os
import random
import subprocess
import sys
import threading
import time

SCOPE = "/tmp/k3_live_scope_8130.csv"
F_NOCACHE = 48  # macOS fcntl
CHUNK = 1 << 20


def whole_disk(path):
    out = subprocess.run(["diskutil", "info", path], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "Part of Whole" in line:
            return line.split(":")[1].strip()
    return None


def device_bytes(dev):
    """Cumulative bytes moved by dev. Prefers the dashboard sampler's bytes_read
    counter; when the sampler is not running, falls back to `iostat -I`, whose
    MB column is total transfer since boot (read+write — fine for a read test)."""
    if not os.path.exists(SCOPE) or time.time() - os.path.getmtime(SCOPE) > 5:
        try:
            out = subprocess.run(["iostat", "-I", "-d", dev], capture_output=True, text=True).stdout
            last = [l for l in out.splitlines() if l.strip()][-1].split()
            return int(float(last[2]) * 1_048_576)
        except Exception:
            return None
    try:
        with open(SCOPE, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 200_000))
            tail = fh.read().decode(errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        parts = line.split(",")
        if len(parts) >= 3 and parts[1] == dev:
            return int(parts[2])
    return None


def main():
    d, seconds, threads = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
    label = sys.argv[4] if len(sys.argv) > 4 else os.path.basename(d.rstrip("/"))
    files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".bin")]
    random.Random(20260908).shuffle(files)
    if not files:
        print(f"{label}: no .bin files in {d}", file=sys.stderr)
        return 1
    mount = subprocess.run(["df", d], capture_output=True, text=True).stdout.splitlines()[-1].split()[0]
    dev = whole_disk(mount)

    total = [0] * threads
    ops = [0] * threads
    stop = time.monotonic() + seconds
    idx = [0]
    lock = threading.Lock()

    def worker(t):
        buf = bytearray(CHUNK)
        while time.monotonic() < stop:
            with lock:
                i = idx[0]; idx[0] += 1
            path = files[i % len(files)]
            try:
                fd = os.open(path, os.O_RDONLY)
            except OSError:
                continue
            try:
                fcntl.fcntl(fd, F_NOCACHE, 1)
                while time.monotonic() < stop:
                    n = os.readv(fd, [buf])
                    if n <= 0:
                        break
                    total[t] += n
                ops[t] += 1
            finally:
                os.close(fd)

    dev_before = device_bytes(dev) if dev else None
    t0 = time.monotonic()
    ths = [threading.Thread(target=worker, args=(t,), daemon=True) for t in range(threads)]
    for th in ths: th.start()
    for th in ths: th.join()
    dt = time.monotonic() - t0
    time.sleep(0.3)
    dev_after = device_bytes(dev) if dev else None

    app = sum(total) / dt / 1e9
    devr = (dev_after - dev_before) / dt / 1e9 if dev_before is not None and dev_after is not None else float("nan")
    print(f"{label:<10} {threads:>3} threads {dt:5.1f} s  app {app:5.2f} GB/s  device({dev}) {devr:5.2f} GB/s  "
          f"{sum(ops)/dt:6.1f} files/s  {sum(total)/1e9:6.1f} GB read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
