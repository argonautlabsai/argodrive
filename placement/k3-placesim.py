#!/usr/bin/env python3
"""Offline PLACEMENT simulator — scored on layer-barrier max latency.

Answers, without staging a byte: does putting hot experts somewhere different
across the four drives make the engine faster?

WHY A BARRIER MODEL AND NOT A BANDWIDTH ONE. A layer cannot run its kernel
until all top-16 routed experts have arrived. They are read concurrently, so
the layer's cost is not the SUM of the reads but the MAX over drives of the
work each drive was handed:

    barrier_cost = max_over_drives( reads_on_drive * RECORD / bandwidth_drive )

That is why concentrating hot experts on the fastest drive loses: it hands one
drive many reads while three sit idle, and a maximum is set by the busiest.
Measured 2026-08-31, no drive is near saturation (internal 60%, K3A 52%,
K3B 54%, K3C 60%, all at 98% duty), so this is a latency/queueing problem,
not a bandwidth one.

Policies compared:
  index      current four-way index split, capability-proportional (BASELINE)
  internal   hot band relocated to the internal SSD, rest index-split
  replicate2 hot band copied to all four, but scheduled by the CURRENT two-way
             mirror clock (internal vs enclosures-as-one, K3C first) — this is
             what the engine can exploit today, without scheduler changes
  replicate  hot band copied to ALL FOUR drives; each read goes to whichever
             drive has the fewest reads so far in that barrier — requires a
             four-way scheduler that does not yet exist
  spread     hot band round-robined across the four (relocation, not copies)
  oracle     every read placed to minimise this barrier's max — unreachable
             lower bound on ANY placement policy

Usage: k3-placesim.py <trace.jsonl> [trace2.jsonl ...] [--hot N]
"""
from __future__ import annotations
import collections
import json
import sys

REC = 17_547_264                      # one expert record, bytes
TOP_K = 16
# Measured capability, GB/s (K3-BRIEF-mirror-constants.md, 2026-08-29).
BW = {"internal": 11.68, "K3C": 7.08, "K3B": 5.62, "K3A": 5.80}
DRIVES = list(BW)
TOTAL_BW = sum(BW.values())


def read_seconds(drive: str, n: int) -> float:
    """Time for `n` concurrent records on one drive."""
    return n * REC / 1e9 / BW[drive]


def load(paths: list[str]) -> tuple[list, collections.Counter]:
    """-> ([(step, layer, [barrier...])], per-(layer,expert) route counts)."""
    out: list = []
    counts: collections.Counter = collections.Counter()
    for path in paths:
        for line in open(path, errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ids = row["ids"]
            layer = int(row["layer"])
            bars = [tuple(ids[i:i + TOP_K]) for i in range(0, len(ids), TOP_K)]
            out.append((row.get("step", 0), layer, [b for b in bars if b]))
            for e in ids:
                counts[(layer, int(e))] += 1
    return out, counts


def index_home(layer: int, expert: int) -> str:
    """Baseline: deterministic split weighted by capability.

    Reproduces the live layout's read shares — measured internal 40.5%, K3C
    24.6%, K3B 17.5%, K3A 17.4% against capability shares 38.7 / 23.5 / 18.6 /
    19.2, i.e. within ~1pp at every heat percentile.
    """
    h = (layer * 9176 + expert * 31337) % 1000
    acc = 0.0
    for d in DRIVES:
        acc += 1000 * BW[d] / TOTAL_BW
        if h < acc:
            return d
    return DRIVES[-1]


def simulate(events, hot: set, policy: str) -> dict:
    total = 0.0
    per_drive = collections.Counter()
    barriers = 0
    # GLOBAL cumulative clocks — what storage.rs actually implements. They
    # fetch_add on dispatch and NEVER drain, so sorting by them is weighted
    # round-robin over lifetime cost, not least-loaded dispatch. Modelling this
    # is the correction: the earlier per-barrier "replicate" policy predicted
    # +14.0% where the real engine measured -7.4%.
    clocks = {d: 0.0 for d in DRIVES}
    cost = {d: REC / 1e9 / BW[d] for d in DRIVES}
    for _step, layer, bars in events:
        for bar in bars:
            load_ = collections.Counter()
            for expert in bar:
                key = (layer, expert)
                if policy == "internal" and key in hot:
                    d = "internal"
                elif policy == "spread" and key in hot:
                    d = DRIVES[(layer + expert) % len(DRIVES)]
                elif policy == "replicate" and key in hot:
                    # Copy on every drive: send it wherever is least loaded so
                    # far in THIS barrier, tie-broken by capability.
                    d = min(DRIVES, key=lambda x: (load_[x] / BW[x], -BW[x]))
                elif policy == "fast2" and key in hot:
                    # Band replicated to internal + K3C ONLY (the two fastest),
                    # least-cumulative-cost between them. Mechanism differs from
                    # 4-way replication: it RELIEVES K3B/K3A, shortening the
                    # queues on the drives that set the barrier maximum, and
                    # raises internal's queue depth (it delivers only 64% of its
                    # B-infinity at the measured QD of ~39).
                    d = "internal" if clocks["internal"] <= clocks["K3C"] else "K3C"
                    clocks[d] += cost[d]
                elif policy in ("rr", "rr_full") and (policy == "rr_full" or key in hot):
                    # Real scheduler: pick the drive with the smallest
                    # CUMULATIVE charge, then charge it. No notion of what is
                    # in flight right now.
                    d = min(DRIVES, key=lambda x: clocks[x])
                    clocks[d] += cost[d]
                elif policy == "oracle_full":
                    d = min(DRIVES, key=lambda x: ((load_[x] + 1) * REC / 1e9 / BW[x]))
                elif policy == "replicate2" and key in hot:
                    # What the CURRENT mirror scheduler can actually do: a
                    # TWO-WAY clock, internal vs all-enclosures as one bucket
                    # (K3-BRIEF-mirror-constants.md). Within the enclosure
                    # bucket the resolve chain's probe order decides, and DIR_C
                    # is probed first — so every enclosure-side hot read lands
                    # on K3C regardless of how busy it is.
                    int_t = (load_["internal"] + 1) * REC / 1e9 / BW["internal"]
                    enc_n = load_["K3C"] + load_["K3B"] + load_["K3A"] + 1
                    enc_t = enc_n * REC / 1e9 / (BW["K3C"] + BW["K3B"] + BW["K3A"])
                    d = "internal" if int_t <= enc_t else "K3C"
                elif policy == "oracle":
                    d = min(DRIVES, key=lambda x: ((load_[x] + 1) * REC / 1e9 / BW[x]))
                else:
                    d = index_home(layer, expert)
                    clocks[d] += cost[d]
                load_[d] += 1
            barriers += 1
            per_drive.update(load_)
            total += max((read_seconds(d, n) for d, n in load_.items()), default=0.0)
    return {"seconds": total, "barriers": barriers, "per_drive": per_drive}


def main() -> int:
    argv = sys.argv[1:]
    hot_n = 50
    args = []
    skip = False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a == "--hot":
            hot_n = int(argv[i + 1])
            skip = True          # consume the VALUE too, or it is read as a path
        elif not a.startswith("--"):
            args.append(a)
    if not args:
        print(__doc__)
        return 2
    events, counts = load(args)
    hot = {k for k, v in counts.items() if v > hot_n}
    hot_gib = len(hot) * REC / 2**30
    routes = sum(counts.values())
    hot_routes = sum(v for k, v in counts.items() if k in hot)

    print(f"traces      : {', '.join(args)}")
    print(f"barriers    : {sum(len(b) for _, _, b in events):,}")
    print(f"hot band    : routed >{hot_n}x -> {len(hot):,} experts, "
          f"{hot_gib:.1f} GiB, {100*hot_routes/routes:.1f}% of routes")
    print()
    base = simulate(events, hot, "index")["seconds"]
    print(f"  {'policy':<11}{'barrier seconds':>17}{'vs index':>11}   per-drive read share")
    print("  " + "-" * 78)
    for policy in ("index", "fast2", "replicate2", "rr", "replicate",
                   "rr_full", "oracle_full", "oracle"):
        r = simulate(events, hot, policy)
        pd = r["per_drive"]
        tot = sum(pd.values()) or 1
        share = " ".join(f"{d}:{100*pd[d]/tot:.0f}%" for d in DRIVES)
        delta = 100 * (base - r["seconds"]) / base
        tag = "  <- BASELINE" if policy == "index" else ("  <- unreachable" if policy == "oracle" else "")
        print(f"  {policy:<11}{r['seconds']:>17.2f}{delta:>10.2f}%   {share}{tag}")
    print()
    print("  Positive vs index = faster. The oracle is the hard ceiling for ANY")
    print("  placement policy; a real policy landing near index means placement")
    print("  is not where the time is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
