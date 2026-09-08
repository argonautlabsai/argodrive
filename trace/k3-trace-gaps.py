#!/usr/bin/env python3
"""Barrier-gap attribution from a per-read trace (review026-09-08 asks).

For every barrier: completion time of each device's last read; the LAST device,
its gap behind the next-to-last device, and the last read's priority (D demand /
P prefetch), bytes and expert. Then:
  1. gap sum by device x priority, and by barrier width (reads in the barrier)
  2. the largest individual gaps, with layer / device / prio / bytes / width
  3. Yellow-last barriers on layer 1 in detail

Usage: python3 k3-trace-gaps.py <readtrace.csv> [top]
"""
import collections
import csv
import statistics as st
import sys

# Trace source codes are role names, verified against this trace's per-device
# read counts (by-layer summary: Green 87,997 = K3B, White 83,967 = K3C,
# Yellow 60,025 = K3A). Do NOT assume from the drive-name history.
DEV = {"K3A": "Yellow", "K3B": "Green", "K3C": "White", "internal": "internal",
       "primary": "internal", "hot": "White", "dir_b": "Green", "dir_c": "Yellow"}


def main():
    path = sys.argv[1]
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    bars = collections.defaultdict(list)  # barrier -> [(end_us, dev, prio, bytes, layer, expert)]
    for r in csv.DictReader(open(path)):
        src = r["source"]
        if src == "read":
            continue  # issue marker, not a device completion
        dur = int(r["dur_ns"])
        if dur <= 0:
            continue
        end = int(r["t_us"]) + dur / 1000.0
        bars[int(r["barrier"])].append((end, DEV.get(src, src), r["prio"], int(r["bytes"]),
                                        int(r["layer"]), int(r["expert"])))
    gaps = []  # (gap_ms, barrier, layer, last_dev, prio, bytes, width, expert)
    by_dev_prio = collections.defaultdict(float)
    by_dev_width = collections.defaultdict(float)
    last_count = collections.Counter()
    for b, reads in bars.items():
        if len(reads) < 2:
            continue
        per_dev = {}
        for end, dev, prio, nbytes, layer, expert in reads:
            if dev not in per_dev or end > per_dev[dev][0]:
                per_dev[dev] = (end, prio, nbytes, layer, expert)
        if len(per_dev) < 2:
            continue
        order = sorted(per_dev.items(), key=lambda kv: kv[1][0])
        (ldev, (lend, lprio, lbytes, llayer, lexp)), (_, (nend, *_)) = order[-1], order[-2]
        gap = (lend - nend) / 1000.0
        width = len(reads)
        wb = "<=16" if width <= 16 else "17-48" if width <= 48 else "49-96" if width <= 96 else ">96"
        gaps.append((gap, b, llayer, ldev, lprio, lbytes, width, lexp))
        by_dev_prio[(ldev, lprio)] += gap
        by_dev_width[(ldev, wb)] += gap
        last_count[ldev] += 1

    total = sum(g[0] for g in gaps)
    print(f"barriers analysed {len(gaps):,}   sum of last-device gaps {total/1000:.2f} s")
    print("\n== gap sum (s) by last device x read priority (D=demand, P=prefetch) ==")
    devs = ["internal", "Green", "White", "Yellow"]
    prios = sorted({p for _, p in by_dev_prio})
    print(f"{'device':<10}" + "".join(f"{p:>9}" for p in prios) + f"{'total':>9}{'last %':>8}")
    for d in devs:
        row = [by_dev_prio.get((d, p), 0.0) / 1000 for p in prios]
        print(f"{d:<10}" + "".join(f"{v:>9.2f}" for v in row) + f"{sum(row):>9.2f}{100*last_count[d]/len(gaps):>7.1f}%")
    print("\n== gap sum (s) by last device x barrier width (reads in barrier) ==")
    wbs = ["<=16", "17-48", "49-96", ">96"]
    print(f"{'device':<10}" + "".join(f"{w:>9}" for w in wbs))
    for d in devs:
        print(f"{d:<10}" + "".join(f"{by_dev_width.get((d, w), 0.0)/1000:>9.2f}" for w in wbs))
    print(f"\n== {top} largest gaps ==")
    print(f"{'gap ms':>7} {'barrier':>7} {'layer':>5} {'last':<9}{'prio':>4} {'bytes':>9} {'width':>5} {'expert':>6}")
    for g in sorted(gaps, reverse=True)[:top]:
        print(f"{g[0]:>7.1f} {g[1]:>7} {g[2]:>5} {g[3]:<9}{g[4]:>4} {g[5]:>9,} {g[6]:>5} {g[7]:>6}")
    y1 = [g for g in gaps if g[3] == "Yellow" and g[2] == 1]
    yall = [g for g in gaps if g[3] == "Yellow"]
    print(f"\n== Yellow-last barriers: {len(yall)} total, {len(y1)} on layer 1 ==")
    if y1:
        print(f"layer-1 Yellow-last gaps: sum {sum(g[0] for g in y1)/1000:.2f} s, median {st.median(g[0] for g in y1):.2f} ms, "
              f"max {max(g[0] for g in y1):.1f} ms; prio mix {collections.Counter(g[4] for g in y1)}")
    if yall:
        print(f"all Yellow-last gaps:     sum {sum(g[0] for g in yall)/1000:.2f} s, median {st.median(g[0] for g in yall):.2f} ms")
    ly = collections.defaultdict(float)
    for g in gaps:
        ly[g[2]] += g[0]
    print("\n== layers with the largest accumulated gaps (all devices) ==")
    for layer, s in sorted(ly.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  layer {layer:>3}  {s/1000:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
