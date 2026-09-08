"""Layout what-ifs on a recorded expert-read trace (the operator's question, 2026-09-06):
internal full set (archive moved off), Green full, White+Yellow as RAID-0 third copy,
each expert read in thirds — versus alternatives. Reuses k3-topology-sim's fluid queue
model and calibration. Re-homing rule for a scenario: each read (or slice) goes to the
allowed holder with the least bytes/rate assigned so far in the same pass (a proportional
balancer, i.e. what the ETA router does in the limit). RAID-0 is modelled as ONE device
whose rate is White+Yellow — optimistic, because a striped file also waits for the
slower member. Usage: python3 k3-layout-sim-0906.py <trace.csv> <measured tok/s>"""
import sys, collections, importlib.util
spec = importlib.util.spec_from_file_location("tsim", "k3-topology-sim.py"); tsim = importlib.util.module_from_spec(spec); spec.loader.exec_module(tsim)
load, simulate_barrier, RATE, pct = tsim.load, tsim.simulate_barrier, tsim.RATE, tsim.pct

def rehome(reads, holders_of, slices=1, rate=None):
    """reads: (start,end,dev,bytes). holders_of(dev) -> list of allowed devices. slices: split each read
    into equal parts, one per holder (all holders used); 1 = choose one holder by least load."""
    load_ = collections.defaultdict(float); out = []
    for start, end, dev, nbytes in sorted(reads):
        holders = holders_of(dev)
        if slices > 1:
            for h in holders:
                out.append((start, end, h, nbytes / len(holders))); load_[h] += nbytes / len(holders)
        else:
            h = min(holders, key=lambda d: load_[d] / rate[d]); out.append((start, end, h, nbytes)); load_[h] += nbytes
    return out

def main():
    path, toks = sys.argv[1], float(sys.argv[2]); step = 0.5
    barriers = load(path)
    measured = [max(e for _s, e, _d, _b in rs) - min(s for s, _e, _d, _b in rs) for rs in barriers.values()]
    tokens = 200.0; tok_time = 1000.0 / toks; span_per_tok = sum(measured) / tokens
    base = dict(RATE); lo, hi = 0.3, 3.0; total_meas = sum(measured)
    for _ in range(18):
        mid = (lo + hi) / 2
        for d in base: RATE[d] = base[d] * mid
        if sum(simulate_barrier(rs, {}, 99.0, step)[0] for rs in barriers.values()) > total_meas: lo = mid
        else: hi = mid
    scale = (lo + hi) / 2
    for d in base: RATE[d] = base[d] * scale
    RATE["RAID"] = (base["White"] + base["Yellow"]) * scale
    print(f"trace {path}: {len(barriers)} passes, measured span p50/p90 {pct(measured,0.5):.1f}/{pct(measured,0.9):.1f} ms, {span_per_tok:.0f} ms/token; calibration x{scale:.2f}")
    cur = {"internal", "Green", "White", "Yellow"}
    scenarios = [
        ("current layout (validation)", lambda d: [d], 1),
        ("A. internal full + Green full, enclosures idle (router picks least load)", lambda d: ["internal", "Green"], 1),
        ("B. internal full + Green full + White/Yellow as today (archive move only)", lambda d: sorted({d, "internal", "Green"}), 1),
        ("C. the operator: internal full + Green full + RAID0(White+Yellow) full, THIRDS", lambda d: ["internal", "Green", "RAID"], 3),
        ("D. same three full copies, router picks ONE least-loaded holder", lambda d: ["internal", "Green", "RAID"], 1),
        ("E. internal + Green full, each read in HALVES", lambda d: ["internal", "Green"], 2),
    ]
    for name, holders_of, slices in scenarios:
        sim = []
        for rs in barriers.values():
            sim.append(simulate_barrier(rehome(rs, holders_of, slices, RATE), {}, 99.0, step)[0])
        sim_per_tok = sum(sim) / tokens; pred = tok_time - span_per_tok + sim_per_tok
        print(f"  {name}: span p50/p90 {pct(sim,0.5):.1f}/{pct(sim,0.9):.1f} ms, {sim_per_tok:.0f} ms/token -> predicted {1000.0/pred:.3f} tok/s ({(toks/(1000.0/pred)-1)*-100:+.1f}%)")
main()
