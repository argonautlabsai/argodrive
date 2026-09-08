#!/usr/bin/env python3
"""Build the publishable table from the 2026-09-08 standard benchmark set.

Reads every arm log in k3-soak-logs/2026-09-08-stdbench and reports, per arm:

  inclusive tok/s   generated / elapsed of the final [stats] line — what the
                    engine prints, and what the campaign ledger has always used
  steady tok/s      (generated - 1) / (elapsed_final - elapsed_first), i.e. the
                    decode rate once the first-token phase is excluded
  TTFT              elapsed of the first [stats] generated=1 line: model load,
                    prompt processing and the first forward pass
  accepted drafts   accepted / proposed from the final [stats] drafts field
  mem rejections    verify-memory-rejections from the [native] Qwen line

Prefill throughput is derived across arms of equal drafter setting:
    (512 - 6) / (TTFT of the 512-token prompt - TTFT of the 6-token prompt)
"""
import re
import statistics
import sys
from pathlib import Path

BENCH = Path("$K3_DIR/k3-soak-logs/2026-09-08-stdbench")
SHORT_TOKENS, LONG_TOKENS = 6, 512

STATS = re.compile(
    r"^\[stats\] generated=(\d+) elapsed=([\d.]+)s speed=([\d.]+) token/s.*?drafts=(\d+)/(\d+)"
)
QWEN = re.compile(r"verify-memory-rejections=(\d+)")


def parse(path):
    first = final = None
    rejections = None
    for line in path.read_text(errors="replace").splitlines():
        m = STATS.match(line)
        if m:
            rec = {
                "generated": int(m.group(1)),
                "elapsed": float(m.group(2)),
                "speed": float(m.group(3)),
                "accepted": int(m.group(4)),
                "proposed": int(m.group(5)),
            }
            if first is None and rec["generated"] == 1:
                first = rec
            final = rec
        q = QWEN.search(line)
        if q:
            rejections = int(q.group(1))
    if final is None:
        return None
    steady = None
    if first and final["generated"] > first["generated"]:
        dt = final["elapsed"] - first["elapsed"]
        if dt > 0:
            steady = (final["generated"] - first["generated"]) / dt
    return {
        "generated": final["generated"],
        "elapsed": final["elapsed"],
        "inclusive": final["speed"],
        "steady": steady,
        "ttft": first["elapsed"] if first else None,
        "accepted": final["accepted"],
        "proposed": final["proposed"],
        "rejections": rejections,
    }


def main():
    arms = {}
    for log in sorted(BENCH.glob("*.log")):
        if log.name == "CHAIN.log":
            continue
        r = parse(log)
        if r:
            arms[log.stem] = r
    if not arms:
        print("no completed arms yet", file=sys.stderr)
        return 1

    print(f"{'arm':<14} {'gen':>5} {'incl':>7} {'steady':>7} {'TTFT s':>7} "
          f"{'drafts':>9} {'memrej':>6}")
    print("-" * 62)
    for name, r in arms.items():
        steady = f"{r['steady']:.4f}" if r["steady"] else "  n/a "
        ttft = f"{r['ttft']:.2f}" if r["ttft"] else " n/a "
        rej = r["rejections"] if r["rejections"] is not None else "-"
        print(f"{name:<14} {r['generated']:>5} {r['inclusive']:>7.4f} {steady:>7} "
              f"{ttft:>7} {r['accepted']:>4}/{r['proposed']:<4} {rej:>6}")

    def group(prefix):
        vals = [(k, v) for k, v in arms.items() if k.startswith(prefix)]
        return vals

    print()
    print("== publishable summary (median of repeats) ==")
    for label, prefix, kind in (
        ("tg128 drafter on",  "TG128_ON",  "gen"),
        ("tg128 drafter off", "TG128_OFF", "gen"),
        ("tg512 drafter on",  "TG512_ON",  "gen"),
        ("tg512 drafter off", "TG512_OFF", "gen"),
    ):
        g = group(prefix)
        if not g:
            continue
        incl = statistics.median([v["inclusive"] for _, v in g])
        steadies = [v["steady"] for _, v in g if v["steady"]]
        steady = statistics.median(steadies) if steadies else None
        s = f"{steady:.4f}" if steady else "n/a"
        print(f"{label:<20} n={len(g)}  inclusive {incl:.4f} tok/s   steady {s} tok/s")

    print()
    print("== prefill (pp512), derived ==")
    for mode in ("ON", "OFF"):
        longs = [v["ttft"] for k, v in arms.items() if k.startswith(f"PP512_{mode}") and v["ttft"]]
        shorts = [v["ttft"] for k, v in arms.items()
                  if (k.startswith(f"TG128_{mode}") or k.startswith(f"TG512_{mode}")) and v["ttft"]]
        if not longs or not shorts:
            continue
        dl, ds = statistics.median(longs), statistics.median(shorts)
        if dl <= ds:
            print(f"drafter {mode.lower():<3}  TTFT(512)={dl:.2f}s is not above TTFT(6)={ds:.2f}s — "
                  f"prompt processing is below the noise of model load; report TTFT instead")
            continue
        rate = (LONG_TOKENS - SHORT_TOKENS) / (dl - ds)
        print(f"drafter {mode.lower():<3}  TTFT(512)={dl:.2f}s  TTFT(6)={ds:.2f}s  "
              f"=> prefill {rate:.1f} tok/s over {LONG_TOKENS - SHORT_TOKENS} added tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
