#!/usr/bin/env python3
"""Replicate a hot expert band to all four drives, with a rollback manifest.

WHY REPLICATION AND NOT RELOCATION. Simulated on four recorded finance-style router traces
(k3-placesim.py), moving the hot band to one drive LOSES: internal -3.6%,
round-robin spread -2.9%, and at the >20x band relocating to internal costs
-34.5% because that drive's read share goes to 81%. Copying the band to all
four GAINS +14.0% at >30x, because it does not move load — it gives each
barrier a CHOICE, so a barrier that would have drawn 8 reads on one drive can
spread them. The per-drive shares barely change; the per-BARRIER balance does.

REQUIRES the four-way scheduler (K3_MIRROR_SCHED=1 with per-device clocks).
Under the previous two-way clock this staging would make things WORSE — every
enclosure-side candidate resolves on the first probe, so K3C absorbs
everything: simulated -12.5% at this band, -20.1% at >20x.

Rollback: every created file is recorded, so `--undo <manifest>` removes
exactly what was added and nothing else.

Usage:
  k3-stage-replicas.py <band-file> [--dry-run]
  k3-stage-replicas.py --undo <manifest>
"""
from __future__ import annotations
import hashlib
import os
import shutil
import sys
import time

DIRS = {
    "internal": "$K3_DIR/k3-experts-even",
    "K3C": "/Volumes/K3C/nonhot",
    "K3B": "/Volumes/K3B/deltafin-root-b/k3-experts",
    "K3A": "/Volumes/K3A/deltafin-root/k3-experts",
}
MANIFEST = "$K3_DIR/k3-replica-manifest.txt"


def undo(path: str) -> int:
    removed = bytes_freed = 0
    with open(path) as fh:
        for line in fh:
            target = line.strip()
            if not target or not os.path.isabs(target):
                continue
            try:
                bytes_freed += os.path.getsize(target)
                os.remove(target)
                removed += 1
            except OSError:
                pass
    print(f"removed {removed:,} replica files, freed {bytes_freed / 2**30:.1f} GiB")
    return 0


def _execute(plan) -> int:
    """Copy every (src, dst) in `plan`, recording each created file so
    `--undo` removes exactly what was added and nothing else."""
    created = []
    started = time.time()
    done = 0
    with open(MANIFEST, "w") as manifest:
        for src, dst, _name in plan:
            tmp = dst + ".staging"
            try:
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
                manifest.write(dst + "\n")
                manifest.flush()
                created.append(dst)
            except OSError as error:
                print(f"  copy failed {dst}: {error}")
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                # Out of space is fatal: a half-staged band is neither the old
                # layout nor the new one, and would silently skew the A/B.
                if getattr(error, "errno", None) == 28:
                    print("FATAL: out of space — run --undo to roll back")
                    return 1
            done += 1
            if done % 250 == 0:
                rate = done / max(1e-6, time.time() - started)
                print(f"  {done:,}/{len(plan):,}  {rate:.0f} files/s", flush=True)
    print(f"staged {len(created):,} replicas in {time.time() - started:.0f}s")
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--undo":
        return undo(sys.argv[2])
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dry = "--dry-run" in sys.argv
    band = [l.strip() for l in open(sys.argv[1]) if l.strip()]

    # --only internal,K3C : restrict the DESTINATION set. Default stays all four
    # so existing callers are unchanged. Needed because the audit showed the four
    # locations are a DISJOINT partition (no expert exists in two places), so to
    # give the scheduler a genuine two-way choice on the fast pair we stage to
    # internal+K3C only — and the slow enclosures must NOT gain copies, or the
    # scheduler can steer work back onto them and the contrast is lost.
    # --split internal:1,K3C:1  -> PARTITION the band across drives by weight,
    # rather than copying all of it to each. Replication gives the scheduler a
    # choice; a split does not — each expert gains exactly one extra copy, on one
    # named drive. That distinction is the whole point of the placement matrix:
    # replication has now lost four times, and every arm that moved reads OFF
    # internal lost, so the untested direction is concentrating a band on the
    # fast drives WITHOUT giving anything a second candidate.
    #
    # Assignment is by md5 of the filename, so it is deterministic and stable
    # across runs — the same expert lands on the same drive every time, which
    # makes an A/B of two split ratios a comparison of ratios and not of two
    # different random assignments.
    if "--split" in sys.argv:
        spec = sys.argv[sys.argv.index("--split") + 1]
        weights = []
        for part in spec.split(","):
            name, _, w = part.partition(":")
            if name not in DIRS:
                print(f"FATAL: unknown drive {name!r}; known: {list(DIRS)}")
                return 1
            weights.append((name, int(w or 1)))
        total_w = sum(w for _, w in weights)
        buckets = []
        for name, w in weights:
            buckets.extend([name] * w)
        assign = {name: set() for name, _ in weights}
        for filename in band:
            h = int(hashlib.md5(filename.encode()).hexdigest(), 16)
            assign[buckets[h % total_w]].add(filename)
        have = {}
        for name, path in DIRS.items():
            try:
                have[name] = {f for f in os.listdir(path) if f.endswith(".bin")}
            except OSError as error:
                print(f"FATAL: cannot list {name} ({path}): {error}")
                return 1
        plan = []
        for name, want in assign.items():
            for filename in sorted(want - have[name]):
                src_name = min((n for n in DIRS if filename in have[n]),
                               key=lambda n: ["internal", "K3C", "K3A", "K3B"].index(n),
                               default=None)
                if src_name is None:
                    continue
                plan.append((os.path.join(DIRS[src_name], filename),
                             os.path.join(DIRS[name], filename), name))
        print(f"{len(plan):,} copies (split {spec})")
        for name, _ in weights:
            n = sum(1 for _, _, d in plan if d == name)
            print(f"  {name:>9}: {n:>6,} files  {n * 17_547_264 / 2**30:>7.1f} GiB")
        if dry:
            return 0
        return _execute(plan)

    targets = list(DIRS)
    if "--only" in sys.argv:
        want = sys.argv[sys.argv.index("--only") + 1].split(",")
        unknown = [w for w in want if w not in DIRS]
        if unknown:
            print(f"FATAL: unknown target(s) {unknown}; known: {list(DIRS)}")
            return 1
        targets = want

    have = {}
    for name, path in DIRS.items():
        try:
            have[name] = {f for f in os.listdir(path) if f.endswith(".bin")}
        except OSError as error:
            print(f"FATAL: cannot list {name} ({path}): {error}")
            return 1

    plan = []
    for filename in band:
        sources = [n for n in DIRS if filename in have[n]]
        if not sources:
            continue
        # Read from the FASTEST drive that holds it, so staging itself is quick
        # and the slow enclosures are write-only during the copy.
        src_name = min(sources, key=lambda n: ["internal", "K3C", "K3A", "K3B"].index(n))
        src = os.path.join(DIRS[src_name], filename)
        for name in targets:
            if filename not in have[name]:
                plan.append((src, os.path.join(DIRS[name], filename), name))

    total = sum(os.path.getsize(s) for s, _, _ in plan[:1]) * len(plan) if plan else 0
    print(f"{len(plan):,} copies, ~{total / 2**30:.1f} GiB")
    if dry:
        for name in targets:
            n = sum(1 for _, _, d in plan if d == name)
            free = shutil.disk_usage(DIRS[name]).free / 2**30
            need = n * 17_547_264 / 2**30
            flag = "  <-- WOULD NOT FIT" if need > free - 20 else ""
            print(f"  {name:>9}: {n:>6,} files  {need:>7.1f} GiB  "
                  f"(free {free:.1f} GiB -> {free - need:.1f} GiB){flag}")
        return 0

    return _execute(plan)
    print(f"manifest: {MANIFEST}  (undo with --undo {MANIFEST})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
