#!/usr/bin/env python3
"""k3-live — live per-device SSD draw + CPU/GPU/RAM scorecards.

    python3 k3-live.py          ->  http://localhost:8130

Spawns k3-diskscope at 200 ms against all four devices and tails its CSV, so
it uses the same counter source as every measurement in this project. Keeps a
rolling 120 s window. Sampling cost is real but small; the page is meant to be
watched DURING runs, which is exactly when it perturbs them — treat anything
measured with this open as indicative, not as a promotable number.
"""
from __future__ import annotations
import sys
if '--ssd-tuner-worker' in sys.argv:
    from ssd_tuner import worker_main as ssd_tuner_worker
    raise SystemExit(ssd_tuner_worker(sys.argv[-1]))
if '--test-worker' in sys.argv or '--test-drive-check' in sys.argv:
    from test_runner import worker_main, disk_identity
    if '--test-drive-check' in sys.argv:
        import json
        from pathlib import Path
        r = json.loads(Path(sys.argv[-1]).read_text())
        if [disk_identity(d['path']) for d in r['drives']] != r['identities']:
            raise SystemExit('Selected drive identity drift')
        print(json.dumps(r['identities']))
        raise SystemExit(0)
    raise SystemExit(worker_main(sys.argv[-1]))
from cluster_state import cluster_snapshot
from harness_monitor import HarnessMonitor, task_record
from campaign_monitor import CampaignMonitor
from spotlight import Spotlight
import secrets
from pathlib import Path
from datetime import datetime
import argparse, sys, atexit, collections, csv, http.server, io, json, os, re, signal, subprocess, threading, time, urllib.parse, uuid
from hardware_topology import TopologyInventory
from argodrive_core import ReadOperations

topology_inventory = TopologyInventory()
read_operations = ReadOperations()
harness_monitor = HarnessMonitor()
campaign_monitor = CampaignMonitor()

# --- diskscope child lifecycle -------------------------------------------
# One sampler should exist per running server. Without explicit teardown the
# child survives the parent (999999 s lifetime, Popen does not reap on exit)
# and every restart leaves another behind, all appending to one csv.
_SCOPE: list = []


def _track_scope(proc) -> None:
    _SCOPE.append(proc)


def _reap_scopes(*_args) -> None:
    for proc in _SCOPE:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception:
            pass
    _SCOPE.clear()


def _shutdown_children(*_args) -> None:
    runner = globals().get('test_runner')
    if runner is not None: runner.shutdown()
    tuner = globals().get('ssd_tuner')
    if tuner is not None: tuner.shutdown()
    _reap_scopes()


atexit.register(_shutdown_children)
for _sig in (signal.SIGTERM, signal.SIGINT):
    try:
        signal.signal(_sig, lambda s, f: (_shutdown_children(), os._exit(0)))
    except (ValueError, OSError):
        pass    # not on the main thread; atexit still covers the normal path

from argodrive_core import (VERSION, load_config, discover_drives, CounterBuckets,
                            engine_processes, chunk_progress)
from streaming_profile import streaming_profile, engine_header, arm_artifacts


def arguments():
    parser = argparse.ArgumentParser(description="ARGODRIVE local inference monitor and saved-run reports")
    parser.add_argument('--port', type=int, default=8130, help='Local port; 0 selects a free port')
    parser.add_argument('--state-dir', help='Writable application settings directory')
    parser.add_argument('--ready-file', help=argparse.SUPPRESS)
    parser.add_argument('--parent-pid', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--config', help='Optional JSON hardware configuration')
    parser.add_argument('--runs', help='Directory containing benchmark block directories')
    parser.add_argument('--marker', help='Live harness marker JSON (optional)')
    parser.add_argument('--reports-only', action='store_true', help='Read saved runs without hardware collection')
    parser.add_argument('--doctor', action='store_true', help='Print setup diagnostics and exit')
    parser.add_argument('--sample-ms', type=int, choices=(10, 100, 200), default=100)
    opts = parser.parse_args() if __name__ == '__main__' else parser.parse_args(['--reports-only'])
    if opts.port != 0 and not 1024 <= opts.port <= 65535:
        parser.error('--port must be 0 or between 1024 and 65535')
    try:
        config = load_config(opts.config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return opts, config


OPTIONS, CONFIG = arguments()
PORT = OPTIONS.port
DRIVE_INFO = []
DISCOVERY_ERRORS = []


def _derive_devs(quiet: bool = False) -> dict[str, str]:
    """Map physical device -> volume name, live (at startup and every few
    seconds after — see _remap_check).

    Device numbers are NOT stable across reconnects. This was a hardcoded
    literal and went stale on the 2026-08-31 replug (K3A disk6->disk5,
    K3B disk4->disk9, K3C disk14->disk4), which would have rendered K3A and
    K3C permanently idle and relabelled every K3C read as K3B — the exact
    "four idle drives with no error" failure this project has already hit
    once. Derive it instead, and fail loudly rather than draw a wrong page.
    """
    global DRIVE_INFO, DISCOVERY_ERRORS
    if OPTIONS.reports_only:
        return {}
    found, DRIVE_INFO, DISCOVERY_ERRORS = discover_drives(CONFIG)
    if not quiet:
        print(f"ARGODRIVE devices: {found}")
        for warning in DISCOVERY_ERRORS:
            print(f"ARGODRIVE discovery: {warning}")
    return found


DEVS = _derive_devs()
_scope_retries = [0]
_next_remap = 0.0
_REMAP_EVERY_S = 5.0


def _ensure_name(name: str) -> None:
    """Per-volume state for a volume that was not mounted at startup."""
    burst.setdefault(name, collections.deque(maxlen=BURST_RING))
    rates.setdefault(name, collections.deque(maxlen=RING))
    read_windows.setdefault(name, collections.deque(maxlen=RING))
    peaks.setdefault(name, 0.0)
    _bucket.setdefault(name, [0.0, 0.0, 0])
    _jitter.setdefault(name, 0)
    _jticks.setdefault(name, 0)
    _peaktick.setdefault(name, (0, 0.0, 0.0))


def _remap_check() -> bool:
    """Re-derive the mount -> device map every few seconds. Device numbers
    move on every replug (2026-08-31: K3A disk6->disk5; 2026-09-03:
    K3C disk4, K3A disk6, K3B disk8; 2026-09-03: K3B disk5, K3A
    disk7, K3C disk9), and a once-at-startup map then labels one drive's
    reads as another's or drops a drive entirely. Labels are keyed by the
    VOLUME name, which is the stable identity; only the device keys change.
    Returns True when the map changed (the sampler must be respawned with the
    new device names)."""
    global _next_remap
    now = time.monotonic()
    if now < _next_remap:
        return False
    _next_remap = now + _REMAP_EVERY_S
    fresh = _derive_devs(quiet=True)
    if fresh == DEVS:
        return False
    print(f"k3-live  device map CHANGED: {DEVS} -> {fresh}", flush=True)
    DEVS.clear()
    DEVS.update(fresh)
    CAP.clear()
    CAP.update({d['id']: d['ceiling_gbps'] for d in DRIVE_INFO if d['ceiling_gbps'] is not None})
    with lock:
        for series in rates.values():
            series.clear()
        total_rates.clear()
        for series in read_windows.values():
            series.clear()
        for name in peaks:
            peaks[name] = 0.0
    for name in fresh.values():
        _ensure_name(name)
    return True
# Capability per device. Updated 2026-08-29 from OBSERVED maxima across 130+
# timed arms: the original figures came from a four-way concurrent cold-read
# benchmark and all three enclosures have since been exceeded in real runs
# (K3A by 16%), which made the "% utilised" readout show >100%.
CAP = {d["id"]: d["ceiling_gbps"] for d in DRIVE_INFO if d["ceiling_gbps"] is not None}   # internal: 13.5 = measured 200 ms peak on the 2026-09-05 champion arms (was 11.68)
# Network-fed pseudo-devices: the M1 Max expert tier arrives over the
    # Thunderbolt bridge management traffic is separate from IOKit disk counters. Bytes
# RECEIVED on the bridge member port are the M1's contribution; capacity is
# the measured one-cable payload ceiling (4.7 GB/s, 2026-09-04).
NET_DEVS = {}  # Network traffic is not physical SSD traffic.
WINDOW = 300.0  # Matches the product timeline (longest chart window is 5 min); samples keep their actual intervals.
RING = 3200     # 100 ms samples to cover WINDOW with margin; was 1200 (2 min) before the 5-minute window existed.
ROOT = os.path.dirname(os.path.abspath(__file__))
SCOPE_CSV_BASE = os.environ.get("K3_SCOPE_CSV", f"/tmp/argodrive_scope_{PORT}_{os.getpid()}.csv")
CSV = SCOPE_CSV_BASE

rates: dict[str, collections.deque] = {v: collections.deque(maxlen=RING) for v in DEVS.values()}
read_windows = {v: collections.deque(maxlen=RING) for v in DEVS.values()}
read_windows['TOTAL'] = collections.deque(maxlen=RING)
sysv = {"cpu": 0.0, "ram": 0.0, "ram_avail": 128.0, "ram_mps": 0.0,
        "ram_engine": 0.0, "ram_system": 0.0, "swap": 0.0,
        "gpu": 0.0, "gmem": 0.0}
try:
    RAM_TOTAL = int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'], stderr=subprocess.DEVNULL))/2**30 if not OPTIONS.reports_only else None
except (OSError, ValueError, subprocess.SubprocessError):
    RAM_TOTAL = None
health = {"scope": False, "sample_at": None, "system_at": None, "engine_detection": "unknown", "engines": []}

# Rolling memory history for the Storage+Memory split view. Memory previously
# existed on this page ONLY as instantaneous cards, with no series at all —
# which is why every hard failure this project has had (the uncapped-retain
# OOM, the 2026-08-29 jetsam event, both 2026-08-30/31 panics) was a
# memory-pressure story that could not be seen coming on this page.
# Timestamped on the SCOPE's clock, not wall time, so the memory rows share an
# x-axis with the SSD rows and vertical alignment is meaningful.
memhist: collections.deque = collections.deque(maxlen=1200)

# Compute-side telemetry. The engine emits a CUMULATIVE [phases] line per
# chunk; differencing consecutive lines turns it into per-chunk rates. Plotting
# expert_plan as a cumulative is what hid its linear growth for a fortnight --
# always difference before plotting.
PHASE_KEYS = ("attention_resident", "expert_demand_read", "expert_plan",
              "bind_upload", "read_wait", "expert_read_prefetch",
              # 2026-09-02 attention-timer split (additive k=v on the same
              # line): gross = the old whole-call demand read; plan_* split
              # expert_plan; attn_* split attention_resident by the
              # provider's own clock (encode = host CPU, wait = host blocked
              # on the GPU for THIS layer, drain = blocked behind EARLIER
              # GPU work).
              "expert_read_gross", "plan_hint", "plan_sched",
              "attn_total", "attn_encode", "attn_wait", "attn_drain",
              "kernel_total", "kernel_host", "kernel_wait", "kernel_drain")
phases = {"active": False, "arm": "", "chunks": 0, "per_tok": {},
          # Spine placement of the CURRENT arm, from its "[native] host=... resident=N/93 (X GiB)" line:
          # GiB resident in unified memory and GiB left on SSD (streamed per pass).
          "spine_ram_gib": None, "spine_ssd_gib": None, "spine_resident": "",
          "hist": collections.deque(maxlen=600), "spt": 0.0, "gb_tok": 0.0,
          "tok_hist": collections.deque(maxlen=400), "tok10": 0.0, "tok_run": 0.0,
          # Wall-clock when this arm emitted its FIRST chunk. Everything before
          # it is spine load-in, which reads ~50 GiB from DELTAFIN_ROOT on K3A
          # and inflates that drive's share (24.5% at 10 tokens vs 18.1% at
          # 200). Exposed so the page can exclude the burst from peak/avg.
          "decode_start": 0.0,
          # From the k3-measure marker: block dir, tokens, overrides, start.
          "meta": {}}

# Live [summer] pool state, tailed from the same arm log as [phases]. The
# engine prints the line per chunk with stats on, so hist gives the in-run
# ramp (hits/suppressed are cumulative counters; the page differences them).
summer_live = {"seen": False, "enabled": False, "cap_gb": 0, "hits": 0,
               "wanted": 0, "hit_rate": 0.0, "inserts": 0, "evictions": 0,
               "gated": 0, "suppressed": 0, "wide_skips": 0, "served_mb": 0.0,
               "copied_mb": 0.0, "avoided_gb": 0.0, "live_slots": "",
               "live_gb": 0.0, "bar": 0,
               "hist": collections.deque(maxlen=600)}

MARKER_PATH = OPTIONS.marker or CONFIG.get("marker") or os.path.join(ROOT, "k3-live-marker.json")


def _read_marker() -> dict | None:
    """k3-measure.sh rewrites this JSON at arm start and end."""
    try:
        with open(MARKER_PATH, "r", errors="ignore") as fh:
            m = json.load(fh)
    except Exception:
        return None
    log = m.get("log") or ""
    if log and not os.path.isabs(log):
        m["log"] = os.path.join(ROOT, log)
    return m


def _latest_ds4_engine_log():
    """Find the newest explicit ds4-bench engine log when no K3 marker exists.

    The DeepSeek reproduction runner writes ``baseline.engine.txt`` and a
    ``run.json`` record, rather than the legacy k3-measure marker.  Previously
    the live page consequently had no phase source and stayed IDLE while a
    ds4-bench process was consuming the drives.
    """
    try:
        root = Path(SOAK)
        candidates = [p for p in root.glob("*/baseline.engine.txt") if p.is_file()]
        # Also accept a directly selected benchmark folder for manual runs.
        direct = root / "baseline.engine.txt"
        if direct.is_file():
            candidates.append(direct)
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
    except (OSError, ValueError):
        return None


def phases_reader() -> None:
    """Tail the current arm's log and difference its [phases] lines.

    Source of truth is the k3-measure marker (arm name, log path, run state).
    Falls back to the pre-2026-09 rung glob so the old harness still renders.
    """
    import glob
    cur, fh, prev, seen = None, None, None, 0
    while True:
        try:
            marker = _read_marker()
            if marker and marker.get("log"):
                newest = marker["log"] if os.path.exists(marker["log"]) else None
            else:
                # DeepSeek's reproduction runner has no k3 marker. Prefer its
                # engine log, then retain the legacy rung fallback.
                ds4_log = _latest_ds4_engine_log()
                if ds4_log:
                    newest = str(ds4_log)
                else:
                    logs = glob.glob("/tmp/k3_fusion_rung_*.log")
                    newest = max(logs, key=os.path.getmtime) if logs else None
            if newest != cur:
                cur, prev, seen = newest, None, 0
                if fh:
                    fh.close()
                fh = open(cur, "r", errors="ignore") if cur else None
                arm_name = ((marker or {}).get("arm") or
                            (Path(cur).parent.name if cur and cur.endswith("baseline.engine.txt")
                             else os.path.basename(cur or "")[19:-4]))
                with lock:
                    phases.update(arm=arm_name, chunks=0, decode_start=0.0,
                                  spine_ram_gib=None, spine_ssd_gib=None, spine_resident="",
                                  meta={k: marker.get(k) for k in
                                        ("out", "tokens", "chat", "overrides",
                                         "start", "state", "rc")} if marker else
                                      {"engine": "ds4", "state": "reading"})
                    phases["hist"].clear()
                    phases["tok_hist"].clear()
                    phases["tok10"] = 0.0
                    phases["tok_run"] = 0.0
                    for key in summer_live:
                        if key == "hist":
                            summer_live["hist"].clear()
                        elif isinstance(summer_live[key], (int, float, bool)):
                            summer_live[key] = type(summer_live[key])(0)
                        else:
                            summer_live[key] = ""
            if not fh:
                time.sleep(1.0)
                continue
            line = fh.readline()
            if not line:
                # Marker state is authoritative; mtime gap is the fallback for
                # the old harness, which has no marker.
                with lock:
                    if marker and marker.get("log") == cur:
                        phases["active"] = marker.get("state") == "running"
                        phases["meta"]["state"] = marker.get("state")
                        phases["meta"]["rc"] = marker.get("rc")
                    else:
                        ds4_running = any(e.get("engine") in ("ds4", "ds4-bench", "ds4-server")
                                          for e in health.get("engines", []))
                        if phases["meta"].get("engine") == "ds4" and ds4_running:
                            # A long prefill can leave the engine log quiet for
                            # longer than the file-age fallback. Process
                            # detection is authoritative while ds4 is alive.
                            phases["active"] = True
                            phases["meta"]["state"] = "running"
                            phases["meta"].setdefault("phase", "prefill")
                        else:
                            phases["active"] = (time.time() - os.path.getmtime(cur)) < 8 if cur else False
                            if not phases["active"] and phases["meta"].get("engine") == "ds4":
                                phases["meta"]["state"] = "complete"
                time.sleep(0.5)
                continue
            if line.startswith("[native] host="):
                m = re.search(r"resident=(\d+)/(\d+) \(([0-9.]+) GiB\)", line)
                if m:
                    n, total, ram = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    per_layer = ram / n if n else 50.74 / 93
                    with lock:
                        phases["spine_ram_gib"] = round(ram, 2)
                        phases["spine_ssd_gib"] = round(per_layer * (total - n), 2)
                        phases["spine_resident"] = f"{n}/{total}"
            # ds4-bench emits monotonic phase boundaries instead of the
            # cumulative K3 ``[phases]`` records.  The boundary itself is
            # enough to drive the stage badge and keep the physical SSD bars
            # visibly associated with the active run.
            elif line.startswith("ARGODRIVE_PHASE "):
                fields = line.split()
                if len(fields) >= 4 and fields[1] in ("prefill", "decode"):
                    phase, start, end = fields[1:4]
                    try:
                        start, end = float(start), float(end)
                    except ValueError:
                        start = end = None
                    with lock:
                        phases["active"] = True
                        phases["meta"].update(engine="ds4", phase=phase, state="running")
                        # A decode boundary means the engine has begun token
                        # generation even before a response counter exists.
                        if phase == "decode":
                            phases["chunks"] = max(1, phases["chunks"])
                        if start is not None and end is not None:
                            phases["meta"]["phase_start"] = start
                            phases["meta"]["phase_end"] = end
            elif line.startswith("ds4: Argodrive Engram readers="):
                with lock:
                    phases["meta"]["engram_readers"] = line.split("=", 1)[1].strip()
            if line.startswith("[phases]"):
                f = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)
                cu = {k: float(f[k].rstrip("s")) for k in PHASE_KEYS if k in f}
                ch = int(f.get("chunks", 0))
                if prev and ch > seen:
                    d = {k: max(0.0, cu[k] - prev.get(k, 0.0)) for k in cu}
                    with lock:
                        if not phases["decode_start"]:
                            # Latest sample time from the scope, so the value is
                            # comparable with trace timestamps on the page.
                            latest = max((r[-1][0] for r in rates.values() if r),
                                         default=0.0)
                            phases["decode_start"] = latest
                        phases.update(active=True, chunks=ch, per_tok=d)
                        phases["hist"].append([round(time.time(), 2),
                                               {k: round(v * 1000, 1) for k, v in d.items()}])
                prev, seen = cu, ch
            elif line.startswith("[summer]"):
                f = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)

                def _num(key, default=0):
                    try:
                        return int(f.get(key, default))
                    except ValueError:
                        return default

                try:
                    live_field = f.get("live", "0/0.0GB")
                    live_slots, live_gb = live_field.split("/", 1)
                    with lock:
                        summer_live.update(
                            seen=True,
                            enabled=f.get("enabled") == "true",
                            cap_gb=_num("cap_gb"),
                            hits=_num("hits"), wanted=_num("wanted"),
                            hit_rate=float(f.get("hit_rate", "0%").rstrip("%")),
                            inserts=_num("inserts"), evictions=_num("evictions"),
                            gated=_num("gated"), suppressed=_num("suppressed"),
                            wide_skips=_num("wide_skips"), bar=_num("bar"),
                            served_mb=float(f.get("served", "0MB").rstrip("MB") or 0),
                            copied_mb=float(f.get("copied", "0MB").rstrip("MB") or 0),
                            avoided_gb=float(f.get("ssd_avoided", "0GB").rstrip("GB") or 0),
                            live_slots=live_slots,
                            live_gb=float(live_gb.rstrip("GB") or 0))
                        summer_live["hist"].append(
                            [round(time.time(), 2), _num("hits"),
                             _num("suppressed"), _num("inserts")])
                except Exception:
                    pass    # a malformed line must never kill the tailer
            elif "s/token)" in line:
                m = re.search(r"\(([\d.]+) s/token\)", line)
                if m:
                    with lock:
                        phases["spt"] = float(m.group(1))
                # Rolling token speed for the live tab (operator): the
                # [stats] line is cumulative per chunk (generated, engine
                # elapsed); the 10 s figure is generated-tokens over engine
                # elapsed between the newest sample and the oldest sample at
                # least 10 s behind it (the whole history if shorter).
                ms = re.search(r"generated=(\d+)\s+elapsed=([\d.]+)s\s+speed=([\d.]+)", line)
                if ms:
                    g, e, sp = int(ms.group(1)), float(ms.group(2)), float(ms.group(3))
                    with lock:
                        hist = phases["tok_hist"]
                        hist.append((e, g))
                        old = None
                        for sample in hist:
                            if e - sample[0] >= 10.0:
                                old = sample
                            else:
                                break
                        if old is None and len(hist) >= 2:
                            old = hist[0]
                        if old and e > old[0]:
                            phases["tok10"] = round((g - old[1]) / (e - old[0]), 3)
                        phases["tok_run"] = sp
        except Exception:
            time.sleep(1.0)
peaks = {v: 0.0 for v in DEVS.values()}
peaks["TOTAL"] = 0.0
total_rates = collections.deque(maxlen=RING)
lock = threading.Lock()

# 10 ms burst ring (K3-MONITOR-10MS-UPGRADE-SPEC). The sampler now ticks at
# 10 ms; the 200 ms trend view is derived as mean-in-bucket so every existing
# consumer is unchanged, while this ring keeps raw ticks for the burst panel.
# Peaks are per-tick from here on — a 200 ms mean mathematically cannot show
# a 13-17 ms read burst, which is the whole reason the spec exists.
BURST_RING = 6000                     # 60 s per device at 100 Hz
burst = {v: collections.deque(maxlen=BURST_RING) for v in DEVS.values()}
_bucket = {v: [0.0, 0.0, 0] for v in DEVS.values()}   # [start_t, sum, n]
_jitter = {v: 0 for v in DEVS.values()}               # ticks with |dt-10ms|>2ms
_jticks = {v: 0 for v in DEVS.values()}
_peaktick = {v: (0, 0.0, 0.0) for v in DEVS.values()} # (max bytes, t, dt_ms) per device

def net_reader() -> None:
    """Feed a network pseudo-device (M1 over the Thunderbolt bridge) into the
    same rate/burst/peak structures the disk tailer fills, from the interface's
    cumulative received-byte counter (netstat -ibn), polled every 100 ms."""
    for name in NET_DEVS:
        _ensure_name(name)
    prev = {}
    while True:
        t = time.time()
        for name, ifname in NET_DEVS.items():
            try:
                out = subprocess.run(["netstat", "-ibn", "-I", ifname], capture_output=True, text=True, timeout=1.0).stdout
                ibytes = None
                for line in out.splitlines()[1:]:
                    cols = line.split()
                    if len(cols) >= 10 and cols[0] == ifname and cols[2].startswith("<Link"):
                        ibytes = int(cols[6]); break
                if ibytes is None:
                    continue
                pt, pv = prev.get(name, (None, None))
                prev[name] = (t, ibytes)
                if pt is None:
                    continue
                dt = t - pt
                if dt <= 0:
                    continue
                tick_bytes = max(0, ibytes - pv); r = tick_bytes / dt / 1e9
                with lock:
                    burst[name].append((round(t, 3), round(r, 3), tick_bytes))
                    _jticks[name] += 1
                    if tick_bytes > _peaktick[name][0]:
                        _peaktick[name] = (tick_bytes, round(t, 3), round(dt * 1000, 2))
                    acc = _bucket[name]
                    if acc[2] == 0:
                        acc[0] = t
                    acc[1] += r; acc[2] += 1
                    if t - acc[0] >= 0.2:
                        mean = acc[1] / acc[2]
                        rates[name].append((round(t, 3), round(mean, 3)))
                        peaks[name] = max(peaks[name], mean)
                        _bucket[name] = [t, 0.0, 0]
            except Exception:
                pass
        time.sleep(max(0.0, 0.1 - (time.time() - t)))


def scope_reader() -> None:
    """Tail k3-diskscope, differencing cumulative byte counters into rates.

    Supervised: if the child dies the loop respawns it and keeps serving. It
    died silently once (2026-08-29) because an over-broad `pkill -f
    k3-diskscope` aimed at a benchmark's sampler also matched this one, and
    the page then showed zeros with no indication the source was gone.
    """
    global CSV
    prev: dict[str, tuple[float, int]] = {}
    while True:
        # Retain earlier captures; the sampler exclusively creates a fresh file.
        CSV = SCOPE_CSV_BASE + "." + uuid.uuid4().hex + ".csv"
        # SECOND LEAK SITE. This loop respawns the sampler whenever the csv goes
        # stale, and originally did so without retiring the previous child — so a
        # single long-lived server accumulated samplers on its own, independent of
        # the restart leak fixed by _reap_scopes. Observed 2026-08-31: two live
        # children of one dashboard pid, spawned 40 minutes apart, both appending
        # to the same csv, which makes the live rates meaningless. Retire first,
        # then spawn.
        _reap_scopes()
        buckets = CounterBuckets(DEVS)
        proc = subprocess.Popen(
            [os.path.join(ROOT, "k3-diskscope"), str(OPTIONS.sample_ms), "999999", CSV, *DEVS],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # The child outlives us unless we say otherwise: it is spawned with a
        # 999999 s lifetime and Popen does not reap on interpreter exit. Every
        # restart of this server therefore stranded one sampler — 24 of them had
        # accumulated on 2026-08-31, all polling every
        # 200 ms and all appending to the SAME csv, which makes the live view's
        # rates meaningless once more than one is running. (Per-arm benchmark
        # csvs are unaffected: k3-measure.sh spawns its own diskscope per arm to
        # its own path, and both arms checked showed zero duplicate (t,dev)
        # pairs.) Registered here rather than at startup so the handle is the
        # live one after each respawn in this loop.
        _track_scope(proc)
        # The sampler stamps rows on its own monotonic clock (seconds since it
        # started); the M1 network series stamps time.time(). payload() takes
        # ONE window across all series, so mixing the clocks discards every
        # disk sample as "old" and the page shows zeros with health green
        # (2026-09-05 23:5x). Offset the sampler clock onto the epoch here.
        spawn_epoch = time.time()
        for v in DEVS.values():
            _bucket[v] = [0.0, 0.0, 0]
        while not os.path.exists(CSV) and proc.poll() is None:
            time.sleep(0.2)
        if proc.poll() is not None:
            with lock:
                health["scope"] = False
            time.sleep(2.0)
            continue
        # COVERAGE CHECK (2026-09-05): k3-diskscope resolves each device
        # through an IOKit lookup at start and silently drops any device the
        # lookup misses ("no Statistics provider found"), which happened to
        # disk0 on one spawn and to disk0+disk4 on the next: the page then shows
        # the surviving drives and zeros for the rest with health still green.
        # Read the first second of the csv; if any mapped device is absent,
        # retire this sampler and spawn again (bounded retries, then serve
        # what there is and flag it).
        time.sleep(1.0)
        try:
            seen = set()
            with open(CSV, "r", errors="ignore") as probe:
                for line in probe:
                    parts = line.split(",")
                    if len(parts) > 1:
                        seen.add(parts[1])
            missing = [d for d in DEVS if d not in seen]
        except OSError:
            missing = list(DEVS)
        if missing and _scope_retries[0] < 5:
            _scope_retries[0] += 1
            print(f"k3-live  sampler missed {missing} at spawn; respawning ({_scope_retries[0]}/5)")
            try:
                proc.terminate()
            except OSError:
                pass
            time.sleep(0.5)
            continue
        if missing:
            print(f"k3-live  WARNING: sampler still missing {missing} after retries; those drives will read as zero")
        _scope_retries[0] = 0
        with lock:
            health["tail_stage"] = "reading"; health["tail_lines"] = 0; health["tail_skipped"] = 0
        prev.clear()
        with lock:
            read_operations.previous.clear()
            read_operations.windows.clear()
        with lock:
            health["scope"] = True
        with open(CSV, "r", errors="ignore") as fh:
            fh.readline()
            while True:
                line = fh.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    if _remap_check():
                        break   # respawn the sampler with the new device names
                    time.sleep(0.15)
                    continue
                if _remap_check():
                    break
                parts = line.replace("\x00", "").strip().split(",")
                if len(parts) < 3 or parts[1] not in DEVS:
                    with lock:
                        health["tail_skipped"] = health.get("tail_skipped", 0) + 1
                    continue
                with lock:
                    health["tail_lines"] = health.get("tail_lines", 0) + 1
                try:
                    t, dev, v1 = float(parts[0]) + spawn_epoch, parts[1], int(parts[2])
                except ValueError:
                    continue
                name = DEVS[dev]
                result = buckets.add(dev, t, v1)
                with lock:
                    health['sample_at'] = time.time()
                    if len(parts) >= 5:
                        try:
                            read_operations.add(name, t, v1, int(parts[3]), int(parts[4]))
                        except ValueError:
                            pass
                    if result:
                        rates[name].append((result['end'], result['rate']))
                        read_windows[name].append((result['end'], result['rate'], result['seconds']))
                        # Delayed samples remain visible, but do not count as 200 ms peaks.
                        if result['seconds'] <= .35:
                            peaks[name] = max(peaks.get(name, 0), result['rate'])
                        if result['total'] is not None:
                            total_rates.append((result['end'], result['total']))
                            read_windows['TOTAL'].append((result['end'], result['total'], result['seconds']))
                            if result['seconds'] <= .35:
                                peaks['TOTAL'] = max(peaks['TOTAL'], result['total'])
                if dev in prev:
                    pt, pv = prev[dev]
                    dt = t - pt
                    if dt > 0 and v1 >= pv:
                        r = (v1 - pv) / 1e9 / dt
                        with lock:
                            burst[name].append((round(t, 3), round(r, 3), v1 - pv))
                            _jticks[name] += 1
                            if abs(dt - OPTIONS.sample_ms/1000) > OPTIONS.sample_ms/5000:
                                _jitter[name] += 1
                            # Peak over a 100 ms rolling window, NOT per tick:
                            # IOKit credits completions in clusters, so a
                            # single 10 ms tick can show 3-5x the drive's real
                            # rate (36 GB/s on a 7 GB/s device, observed
                            # 2026-09-02). The burst panel keeps raw ticks —
                            # clustering is real structure there — but the
                            # session-peak scorecard reports sustained rate.
                            # Session peak moved to the 200 ms bucket mean below
                            # (2026-09-06): a 100 ms window of >=5 ticks could be a
                            # 50 ms cluster of IOKit completion credits and reported
                            # Green at 16 GB/s against a 7.1 GB/s enclosure ceiling.
                            # The bucket mean is the same 200 ms method as the per-arm
                            # csv, so the scorecard and the charts now agree.
                            # Largest single 10 ms tick ever seen, in bytes —
                            # the "did a 20 MB burst happen?" answer. dt varies,
                            # so store raw bytes not rate; sub-10ms structure is
                            # unresolvable (IOKit counter granularity).
                            tick_bytes = v1 - pv
                            if tick_bytes > _peaktick[name][0]:
                                _peaktick[name] = (tick_bytes, round(t, 3), round(dt * 1000, 2))
                prev[dev] = (t, v1)
        with lock:
            health["scope"] = False


def sys_reader() -> None:
    while True:
        try:
            cpu = subprocess.run(["ps", "-A", "-o", "%cpu"], capture_output=True, text=True).stdout
            cpu = sum(float(x) for x in cpu.split()[1:] if x.replace(".", "", 1).isdigit())
            process = subprocess.run(['ps', '-A', '-o', 'pid=,rss=,comm='], capture_output=True, text=True, timeout=3)
            engines = engine_processes(process.stdout) if process.returncode == 0 else []
            with lock:
                health.update(engines=engines, engine_detection='ok' if process.returncode == 0 else 'unavailable')
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
            if not vm.strip() or RAM_TOTAL is None:
                raise ValueError('System memory counters unavailable')
            page_size = int(re.search(r'page size of (\d+) bytes', vm).group(1))
            q = lambda k, d=0: (int(m.group(1)) if (m := re.search(k, vm)) else d)
            a = q(r"Pages active:\s+(\d+)")
            w = q(r"Pages wired down:\s+(\d+)")
            comp = q(r"Pages occupied by compressor:\s+(\d+)")
            fr = q(r"Pages free:\s+(\d+)")   # raw free, NOT headroom
            ina = q(r"Pages inactive:\s+(\d+)")
            spec = q(r"Pages speculative:\s+(\d+)")
            swap = float(subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True,
                                        text=True).stdout.split()[5].rstrip("M") or 0)
            io = subprocess.run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
                                capture_output=True, text=True).stdout
            g = re.search(r'"Device Utilization %"=(\d+)', io)
            gm = re.search(r'"In use system memory"=(\d+)', io)
            # RAM accounting (2026-08-29, measured). USED = active+wired+
            # compressor; AVAIL = free+inactive+speculative. macOS pins `free`
            # near zero and keeps reclaimable cache in `inactive`, so `free`
            # alone is not headroom. The IOAccelerator "In use system memory"
            # value is a SUBSET of these pages, never an addition -- vm_stat's
            # categories already sum to physical (126.5 of 128 GiB measured);
            # adding it yielded impossible >128 GiB totals. Machine is 128 GiB.
            used = (a + w + comp) * page_size / 1073741824
            avail = (fr + ina + spec) * page_size / 1073741824
            # Split "used" into the engine's own footprint and everything else
            # (OS, GUI, other processes). vm_stat's categories fall ~1.5 GiB
            # short of physical, so the remainder is folded into system rather
            # than silently dropped -- used + avail now sums to 128 exactly.
            eng = sum(e['rss_gib'] for e in engines)
            used = RAM_TOTAL - avail          # fold the unaccounted remainder in
            # Attribution note: a process's RSS does NOT include the GPU-wired
            # Metal allocations it owns, so `used - rss` is not "the OS". The
            # engine's real footprint is at least rss + its MPS pool; the
            # remainder is everything else INCLUDING file cache. Reported as
            # three measured quantities rather than a fake clean partition.
            mps = (float(gm.group(1)) / 1073741824) if gm else 0.0
            system = max(0.0, used - eng - mps)
            gpct = float(g.group(1)) if g else 0.0
            with lock:
                health['system_at'] = time.time()
                sysv.update(cpu=cpu, ram=used, ram_avail=avail, ram_mps=mps,
                            ram_engine=eng, ram_system=system, swap=swap,
                            gpu=gpct, gmem=mps)
                # Share the scope's clock so memory rows align vertically with
                # the SSD rows; falls back to 0 before the scope has produced a
                # sample, which the page filters out rather than plotting at t=0.
                tscope = max((r[-1][0] for r in rates.values() if r), default=0.0)
                if tscope:
                    memhist.append([round(tscope, 2), round(used, 2), round(avail, 2),
                                    round(mps, 2), round(gpct, 1), round(cpu, 1),
                                    round(fr * page_size / 1073741824, 2)])
        except Exception:
            pass
        time.sleep(1.0)



# ---------------------------------------------------------------- weights map
_dist_cache: dict = {"key": None, "val": None}


def active_config() -> dict:
    """Read the live engine's overlay config straight from its environment."""
    try:
        pid = subprocess.run(["pgrep", "-f", "[d]eltafin run"], capture_output=True,
                             text=True).stdout.split()
        if not pid:
            return {}
        env = subprocess.run(["ps", "eww", "-p", pid[0]], capture_output=True, text=True).stdout
        out = {}
        for tok in env.split():
            for k in ("K3_EXPERT_HOT_DIR", "K3_EXPERT_DIR_B", "K3_EXPERT_DIR_C", "DELTAFIN_ROOT"):
                if tok.startswith(k + "="):
                    out[k] = tok[len(k) + 1:]
        return out
    except Exception:
        return {}


def _names(d: str) -> set:
    if not d or not os.path.isdir(d):
        return set()
    try:
        return {f for f in os.listdir(d) if f.endswith(".bin")}
    except OSError:
        return set()


def weights_map() -> dict:
    """Resolve which drive actually SERVES each expert, given the live chain
    DIR_C -> HOT -> DIR_B -> primary(K3A). Cached on the config tuple because
    the set differences run over ~80k filenames."""
    cfg = active_config()
    key = (cfg.get("K3_EXPERT_HOT_DIR", ""), cfg.get("K3_EXPERT_DIR_B", ""),
           cfg.get("K3_EXPERT_DIR_C", ""))
    if _dist_cache["key"] == key and _dist_cache["val"]:
        return _dist_cache["val"]
    hot, dib, dic = key
    root = cfg.get("DELTAFIN_ROOT", "")
    prim = os.path.join(root, "k3-experts") if root else ""
    c, h, b, a = _names(dic), _names(hot), _names(dib), _names(prim)
    served_c = c
    served_h = h - served_c
    served_b = b - served_c - served_h
    served_a = a - served_c - served_h - served_b
    tot = len(served_c) + len(served_h) + len(served_b) + len(served_a)
    val = {
        "cfg": {"hot": hot or "OFF", "dir_b": dib or "OFF", "dir_c": dic or "OFF"},
        "files": {"internal": len(served_h), "K3C": len(served_c),
                  "K3B": len(served_b), "K3A": len(served_a)},
        "total": tot,
        "held": {"internal": len(h), "K3C": len(c), "K3B": len(b), "K3A": len(a)},
    }
    _dist_cache.update(key=key, val=val)
    return val



STAGING = os.environ.get("K3_STAGING", f"/tmp/k3_staging_{PORT}.json")


def staging_status() -> dict:
    """Progress of any weight redistribution currently copying or linking.

    Staging scripts write {"active":1,"op":"copy K3C","done":N,"total":M,
    "bytes":B,"started":epoch} here; absent or active=0 means nothing is
    moving. NOTE most 'redistribution' in this project is HARDLINKS, which
    are instant and never show progress — only genuine byte copies do.
    """
    try:
        with open(STAGING) as fh:
            d = json.load(fh)
        if not d.get("active"):
            return {"active": 0}
        done, total = d.get("done", 0), max(1, d.get("total", 1))
        el = max(0.001, time.time() - d.get("started", time.time()))
        rate = done / el
        d["pct"] = round(100 * done / total, 1)
        d["eta_s"] = round((total - done) / rate) if rate > 0 else None
        d["rate_per_s"] = round(rate, 1)
        d["gb_done"] = round(done * 17547264 / 1e9, 1)
        d["gb_total"] = round(total * 17547264 / 1e9, 1)
        return d
    except Exception:
        return {"active": 0}


# ------------------------------------------------------------------ stats tab
STATE_DIR = Path(OPTIONS.state_dir).expanduser() if OPTIONS.state_dir else (Path.home() / 'Library/Application Support/ARGODRIVE' if getattr(sys, 'frozen', False) else Path(ROOT))
LOCAL_SETTINGS = STATE_DIR / 'argodrive.local.json'
spotlight = Spotlight(STATE_DIR)
try:
    SAVED_SETTINGS = json.loads(LOCAL_SETTINGS.read_text())
    if not isinstance(SAVED_SETTINGS, dict) or not isinstance(SAVED_SETTINGS.get('runs', ''), str):
        SAVED_SETTINGS = {}
except (OSError, ValueError):
    SAVED_SETTINGS = {}
SOURCE_LOCK = threading.RLock()
SETTINGS_TOKEN = secrets.token_urlsafe(32)
SOAK = OPTIONS.runs or CONFIG.get("runs") or SAVED_SETTINGS.get("runs") or os.environ.get("K3_SOAK_DIR") or (
    os.path.join(ROOT, "k3-soak-logs") if os.path.isdir(os.path.join(ROOT, "k3-soak-logs"))
    else str(STATE_DIR / "runs") if OPTIONS.state_dir or getattr(sys, "frozen", False) else os.path.abspath(os.path.join(ROOT, "..", "runs")))   # GLM53/arms (ds4 harness)
_stats_cache: dict = {"key": None, "val": None}
# Per-log parse memo: a tree rebuild (any new arm) used to re-parse every log
# (1,445 logs = 18 s per /stats request, which the page times out on). Rows
# are re-derived fields on top of a cached parse, so hand out copies.
_arm_cache: dict = {}

def _parse_arm_cached(path: str):
    try:
        st = os.stat(path)
        siblings = [Path(path).with_suffix(ext) for ext in ('.csv', '.sys', '.map', '.md5', '.readtrace.csv', '.hotlist', '.expert.json', '.err', '.engine.txt', '.task.json', '.jsonl')]
        key = (st.st_mtime_ns, st.st_size, tuple((f.stat().st_mtime_ns, f.stat().st_size) if f.exists() else None for f in siblings))
    except OSError:
        return None
    hit = _arm_cache.get(path)
    if hit and hit[0] == key:
        return dict(hit[1]) if hit[1] else None
    row = _parse_arm(path)
    _arm_cache[path] = (key, dict(row) if row else None)
    return dict(row) if row else None

# Banner written by k3-arm.sh; the older harness wrote "RUNG <label> START".
_RE_ARM = re.compile(r"^(?:ARM|RUNG)\s+(\S+)\s+START\s+(\S+)\s+overrides:\s*(.*)$")
_RE_ARM_DS4 = re.compile(r"^ARM\s+(\S+)\s+START\s+(\S+ \S+)\s+(.*)$")   # ds4 harness (GLM project)
_RE_DS4_SUMMARY = re.compile(r'^\{"tag":.*"decode_tok_s".*\}\s*$', re.M)
_RE_PROMPT = re.compile(r"prompt=\[(.*)\]\s*$")
_RE_TOKENS = re.compile(r"\btokens=(\d+)")
# A run the shell had to signal is not a measurement, whatever its last [stats]
# line says. Matches the job-control text sh/zsh writes on SIGTERM/SIGKILL.
_RE_SIGNAL = re.compile(r"\b(Terminated: 15|Killed: 9|Terminated|Killed)\b")
_RE_STATS = re.compile(
    r"generated=(\d+)\s+elapsed=([\d.]+)s\s+speed=([\d.]+) token/s \(([\d.]+) s/token\)"
    r"\s+chunks=(\d+).*?drafts=(\d+)/(\d+)\s+layer_passes=(\d+)")
_RE_MIRROR = re.compile(r"^\[native\] mirror: enabled=(\w+)")
# A control arm is the block's baseline. Matched on the tag so a block does not
# need to declare one; falls back to the first arm when nothing matches.
_RE_CTL = re.compile(r"(?i)(^|_)(ctl|control|a1|ca1|x_a1|off)($|_)")


def _is_arm_log(name: str) -> bool:
    """A <TAG>.log written by k3-measure for one arm. CHAIN.log is a chain's
    bookkeeping (review026-09-08: it was being counted as an arm and raised
    incomplete-run alerts); *VOID* logs are arms killed on purpose."""
    return name.endswith(".log") and name != "CHAIN.log" and "VOID" not in name


def _arm_mode(row: dict) -> str:
    """'off' when the harness passed K3_UAG_DRAFT=off (a plain-decode control),
    else 'on'. Read from the [arm] header overrides, not from engine output."""
    ov = row.get("overrides") or ""
    if row.get("ds4"):
        return "on" if re.search(r"\bmtp=1\b", ov) else "off"
    return "off" if "K3_UAG_DRAFT=off" in ov else "on"


def _arm_length(row: dict):
    return row.get("tokens") or row.get("tokens_inferred") or row.get("generated")


# Composite values in `[config] resolved:` carry spaces and nested '=' —
# expert_overlays=[hot=...:33580files dir_b=...] and quality=QualityPolicy { ... }.
# Splitting the line on whitespace shreds them into fake columns, so scalars are
# matched explicitly and composites are handled separately below.
_RE_CFG_SCALAR = re.compile(r"\b([a-z][a-z0-9_]*)=([^\s\[{]+)")
_RE_OVERLAY = re.compile(r"(hot|dir_b|dir_c)=([^\s:\]]+):(\d+)files")


def _arm_settings(text: str) -> dict:
    """Flat settings for ONE arm, in three families that must not be merged.

    req_*  what the harness PASSED (the [arm] header)
    cfg_*  what the engine RESOLVED ([config] resolved:)
    nat_*  what deltafin DECLARED at startup ([native] lines)

    Kept separate on purpose. This project has repeatedly lost days to knobs that
    were passed and never resolved — K3_MIRROR_SCHED and K3_EXPERT_RETAIN do not
    appear in the resolved line at all, and K3_QWEN_ADAPTIVE was assumed live for
    a whole block. A CSV that showed only one family would reproduce exactly that
    error: req_X=1 with no cfg_X and nat_X=false is the signature of a knob that
    did nothing, and it is only visible when all three are side by side.
    """
    s: dict = {}
    first = text.split("\n", 1)[0]
    if m := _RE_ARM.match(first):
        ov = m.group(3)
        if p := _RE_PROMPT.search(ov):
            ov = ov[: p.start()]
        for tok in ov.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                s["req_" + k] = v
    for source, prefix in (('DS4_ENV', 'req_'), ('CONFIG', 'req_config_')):
        if m := re.search(r'^'+source+r' (.*)$', text, re.M):
            for k,v in re.findall(r'(\w+)=(\S+)', m.group(1)):
                s[prefix+k] = v
    if m := re.search(r"\[config\] resolved:(.*)", text):
        line = m.group(1)
        for ov_m in _RE_OVERLAY.finditer(line):
            s[f"cfg_overlay_{ov_m.group(1)}_files"] = int(ov_m.group(3))
        # Drop the overlay/quality blobs before scanning scalars so their inner
        # tokens do not become columns of their own.
        line = re.sub(r"expert_overlays=\[[^\]]*\]", "", line)
        line = re.sub(r"quality=QualityPolicy \{[^}]*\}", "", line)
        for k, v in _RE_CFG_SCALAR.findall(line):
            s["cfg_" + k] = v
    for pat, keys in (
        (r"\[native\] mirror: enabled=(\w+) internal_cost_us=(\d+) "
         r"enclosure_cost_us=(\d+) bias=(\S+)",
         ("nat_mirror_enabled", "nat_mirror_internal_cost_us",
          "nat_mirror_enclosure_cost_us", "nat_mirror_bias")),
        (r"\[native\] retain: enabled=(\w+) cap_gb=(\d+) layers=(\S+)",
         ("nat_retain_enabled", "nat_retain_cap_gb", "nat_retain_layers")),
        (r"\[native\] summer: enabled=(\w+) cap_gb=(\d+) policy=(\S+) sample=(\d+)",
         ("nat_summer_enabled", "nat_summer_cap_gb", "nat_summer_policy",
          "nat_summer_sample")),
        (r"representation=(\S+) layers=(\d+) globals=(\d+) resident=(\S+) ",
         ("nat_representation", "nat_layers", "nat_globals", "nat_resident")),
        (r"readers=(\S+)", ("nat_readers",)),
        (r"experts=(\S+) root=", ("nat_expert_backend",)),
        (r"\[native\] optional Qwen: state=(\S+)", ("nat_qwen_state",)),
        (r"expert heat: weight=(\d+)", ("nat_heat_weight",)),
    ):
        if m := re.search(pat, text):
            for i, key in enumerate(keys):
                s[key] = m.group(i + 1)
    # Same three-state rule as the pivot columns: absent != off. A binary that
    # predates a feature cannot speak to it, and recording that as "false" would
    # invent a measurement the arm never made.
    for feat in ("mirror", "retain", "summer"):
        s.setdefault(f"nat_{feat}_enabled", "absent")
    return s


def _enrich_ds41_row(row: dict, arm_dir: str, j: dict) -> None:
    """The columns a reviewer needs to read a DeepSeek arm without opening it.

    Every DeepSeek arm is named `baseline` by the harness, so identity comes
    from the block directory: `variant` strips the repeat suffix (p3-up-1 ->
    p3-up, w1077-A1 -> w1077-A) so repeats group themselves. Settings come from
    plan.json (the BENCH_RESULT record never carried them, which is why
    `overrides` read as a dash on every row). Anything the engine does not
    report -- V4.1 prints no cache hit-rate, and these arms write no memory
    log -- stays None rather than a misleading 0.000.
    """
    block = os.path.basename(arm_dir)
    for rx in (r'^(.*-[AB])([0-9]+)$', r'^(.*)-([0-9]+)$', r'^(.*)-([ab])$'):
        if (m := re.match(rx, block)):
            row['variant'], row['rep'] = m.group(1), m.group(2)
            break
    else:
        row['variant'], row['rep'] = block, '1'
    row['prefill_tok_s'] = j.get('prefill_tok_s')
    row['output_sha'] = (j.get('output_sha256') or '')[:16] or None
    try:
        with open(os.path.join(arm_dir, 'plan.json')) as fh:
            plan = json.load(fh)
    except (OSError, ValueError):
        plan = {}
    env = plan.get('experimental_environment') or {}
    argv = plan.get('argv') or []
    idxs = [i for i, a in enumerate(argv) if a == '--ssd-streaming-cache-experts']
    cache_req = argv[idxs[-1] + 1] if idxs and idxs[-1] + 1 < len(argv) else None   # last wins in the engine
    reps = plan.get('replicas') or []
    rw = plan.get('replica_weights') or ([1] * len(reps) if reps else [])
    pw = plan.get('primary_weight')
    weights = ':'.join(str(w) for w in [pw, *rw]) if reps and pw is not None else ('single' if plan else None)
    knobs = ' '.join(f"{k.removeprefix('DS4_ARGODRIVE_')}={v}" for k, v in sorted(env.items()) if k.startswith('DS4_ARGODRIVE_'))
    parts = [p for p in (f'weights={weights}' if weights else '', f'cache={cache_req}' if cache_req else '',
                         'sampler=on' if plan.get('sampler') else 'sampler=off', knobs) if p]
    row['overrides'] = ' '.join(parts) if parts else (row.get('overrides') or '—')
    row['weights'], row['cache_experts'] = weights, cache_req
    row['notes'] = '; '.join(plan.get('notes') or []) or None
    # Per-source bytes, named by volume: the aggregate hides a missing drive or a
    # split that did not hold. /Volumes/Green/<model>/<file> -> "green".
    names = ['primary'] + [os.path.basename(os.path.dirname(os.path.dirname(r))) for r in reps]
    for k, b in (j.get('expert_application_bytes_by_source') or {}).items():
        i = int(k)
        row['gb_' + (names[i] if i < len(names) else f'src{i}').lower()] = round(int(b) / 1e9, 1)
    ba = j.get('barrier_attribution') or {}
    if ba.get('sources'):
        w = str(ba.get('worst_source')); s = ba['sources'].get(w, {})
        nm = names[int(w)] if w.isdigit() and int(w) < len(names) else f'src{w}'
        row['lands_last'] = f"{nm} {round(100 * (s.get('share') or 0))}%"
    try:
        with open(os.path.join(arm_dir, 'baseline.engine.txt'), 'rb') as fh:
            if (m := re.search(rb'expert cache ([0-9.]+) GiB', fh.read())):
                row['cache_gib'] = float(m.group(1))
    except OSError:
        pass
    try:
        with open(os.path.join(arm_dir, 'swap-samples.json')) as fh:
            vals = [s.get('used_mb') for s in json.load(fh) if isinstance(s, dict) and s.get('used_mb') is not None]
        row['swap_mb'] = round(max(vals)) if vals else None
    except (OSError, ValueError):
        row['swap_mb'] = None
    try:
        with open(os.path.join(arm_dir, 'run.json')) as fh:
            state = json.load(fh)
        ok = state.get('status') == 'complete' and bool(state.get('result'))
        if state.get('dead_knobs'):
            row['dead_knobs'] = ' '.join(state['dead_knobs'])
    except (OSError, ValueError):
        ok = True
    row['valid'] = bool(ok and not row.get('incomplete'))
    if not ok:
        row['incomplete'] = True


def _parse_arm(path: str) -> dict | None:
    """One arm log -> one pivot row. Never raises: a half-written log from a
    killed block must degrade to a partial row, not blank the whole tab."""
    try:
        with open(path, errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return None
    if not text:
        return None
    row: dict = {"arm": os.path.splitext(os.path.basename(path))[0]}
    first = text.split("\n", 1)[0]
    if m := _RE_ARM.match(first):
        row["arm"] = m.group(1)
        row["started"] = m.group(2)
        ov = m.group(3)
        if p := _RE_PROMPT.search(ov):
            row["prompt"] = p.group(1)[:60]
            ov = ov[:p.start()]
        if t := _RE_TOKENS.search(ov):
            row["tokens"] = int(t.group(1))
        row["overrides"] = re.sub(r"\b(tokens|chat)=\S+", "", ov).strip() or "—"
    # Last [stats] line: the cumulative summary at end of run.
    last = first = None
    for m in _RE_STATS.finditer(text):
        if first is None:
            first = m
        last = m
    # ENCODE vs DECODE, recovered retroactively. `[stats]` is cumulative and
    # emitted per chunk, so the FIRST line carries the time to produce token 1 —
    # prefill plus the cold spine/expert load — and the last carries the total.
    # The reported speed fuses them: measured 2026-08-31, first-token is 22.6s of
    # a 101s 40-token run (22.5%), stable to 1.8% across arms. That fixed cost
    # dilutes every delta by roughly a fifth without randomising it, so a knob
    # worth 5% of steady decode has been reading as ~4%, and one that gained
    # decode while costing load read as null. Both are surfaced so the corpus can
    # be re-read without re-running anything.
    if first is not None and last is not None:
        t1 = float(first.group(2))
        tn = float(last.group(2))
        n = int(last.group(1))
        row["first_token_s"] = round(t1, 2)
        if n > 1 and tn > t1:
            row["tok_s_steady"] = round((n - 1) / (tn - t1), 4)
            row["encode_share_pct"] = round(100 * t1 / tn, 1)
    try:
        row["wall_s"] = round(os.path.getmtime(path) - os.path.getmtime(path[:-4] + ".map"), 0)
    except OSError:
        pass
    if last:
        row.update(generated=int(last.group(1)), elapsed=float(last.group(2)),
                   tok_s=float(last.group(3)), s_tok=float(last.group(4)),
                   chunks=int(last.group(5)),
                   drafts=f"{last.group(6)}/{last.group(7)}",
                   accept=(round(100 * int(last.group(6)) / int(last.group(7)))
                           if int(last.group(7)) else None),
                   layer_passes=int(last.group(8)))
        # `[stats]` is CUMULATIVE, emitted per chunk, so a killed arm leaves a
        # perfectly well-formed line partway through the run. Without this
        # check p2_ADP (stopped mid-arm on 2026-08-31) reported 0.3399 tok/s as
        # if it were a result. An arm that did not reach its token target is
        # not a measurement.
        if row.get("tokens") and row["generated"] < row["tokens"]:
            row["incomplete"] = True
    elif (bm := re.search(r'^ARGODRIVE_BENCH_RESULT (.+)$', text, re.M)):
        try:
            j = json.loads(bm.group(1))
            n = j['generated_tokens']
            if type(n) is not int or n <= 0 or j['steady_tokens'] != n-1:
                raise ValueError('Invalid engine counts')
            rate = float(j['steady_tok_s'])
            import math
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError('Invalid engine timing')
            # Response-shaped figures from the engine's own timers. ds4-bench does
            # not MEASURE time to first token (the GLM harness does, as
            # first_byte_s), so `first_token_s` stays None by policy -- see
            # test_benchmark_import_retains_actual_counts_without_inventing_ttft.
            # But prefill wall = prompt tokens / reported prefill rate and the
            # first decode step is measured, so their sum is an identity on the
            # engine's numbers, not an estimate. It goes out under a name that
            # says so: ttft_s_derived. It is the number a prefill claim rests on
            # and every DeepSeek row exported it as 0.000.
            pre_s = j['prompt_tokens'] / float(j['prefill_tok_s'])
            gen_s = n / float(j['generation_tok_s'])
            first_s = pre_s + float(j.get('first_decode_step_ms') or 0) / 1000
            elapsed = pre_s + gen_s
            if not all(math.isfinite(v) and v > 0 for v in (pre_s, gen_s, elapsed)):
                raise ValueError('Invalid engine timing')
            row.update(started=j.get('started_at', row.get('started')), model=os.path.basename(j['model_path']).removesuffix('.gguf'),
                       tokens=n, generated=n, ds4=True, tok_s_steady=rate,
                       tok_s=round(n / elapsed, 4), elapsed=round(elapsed, 2), first_token_s=None,
                       ttft_s_derived=round(first_s, 2), encode_share_pct=round(100 * pre_s / elapsed, 1),
                       rate_source=j['rate_source'], prompt_hash=j['prompt_sha256'],
                       comparison_context=(str(j['context']), 'raw-bench', 'greedy-non-eos', 'none'),
                       summary={'ds4_gen_tps':j['generation_tok_s'], 'ds4_prefill_tps':j['prefill_tok_s'],
                                'prompt_tokens':j['prompt_tokens'], 'valid':'ok', 'rc':0},
                       publication_ready=False)
            _enrich_ds41_row(row, os.path.dirname(path), j)
        except (KeyError, ValueError, TypeError):
            row['incomplete'] = True
    elif re.search(r'^ARGODRIVE_BENCH_START ', text, re.M):
        # A DeepSeek arm that started but never wrote a result: the harness
        # stopped it (swap guard, engine exit, extraction failure). Carry the
        # reason so a reviewer sees "stopped: swap growth exceeded" instead of
        # a row of zeros they have to spot by eye.
        row['incomplete'] = True
        row['valid'] = False
        row['ds4'] = True
        try:
            with open(os.path.join(os.path.dirname(path), 'run.json')) as fh:
                st = json.load(fh)
            row['notes'] = ('stopped: ' + str(st.get('error') or st.get('status') or 'no result'))[:160]
        except (OSError, ValueError):
            row['notes'] = 'stopped: no run.json'
        block = os.path.basename(os.path.dirname(path))
        for rx in (r'^(.*-[AB])([0-9]+)$', r'^(.*)-([0-9]+)$', r'^(.*)-([ab])$'):
            if (m := re.match(rx, block)):
                row['variant'], row['rep'] = m.group(1), m.group(2)
                break
        else:
            row['variant'], row['rep'] = block, '1'
    elif (dm := _RE_DS4_SUMMARY.search(text)):
        # ds4 harness (GLM project): one JSON summary line per arm.
        try:
            import json as _json
            j = _json.loads(dm.group(0))
            hm = _RE_ARM_DS4.match(text.split("\n", 1)[0])
            if hm and not row.get("started"):
                row["arm"] = hm.group(1); row["started"] = hm.group(2)
                if t := _RE_TOKENS.search(hm.group(3)): row["tokens"] = int(t.group(1))
                row["overrides"] = re.sub(r"\btokens=\S+", "", hm.group(3)).strip() or "—"
            if cm := re.search(r"^CONFIG (.*)$", text, re.M):
                row["overrides"] = (row.get("overrides", "") + " " + re.sub(r"model=\S+", "", cm.group(1))).strip()
            env_line = re.search(r'^DS4_ENV (.*)$', text, re.M)
            if env_line:
                row['overrides'] = (row.get('overrides', '')+' '+env_line.group(1).strip()).strip()
            if not row.get('tokens') and isinstance(j.get('tokens_req'), int) and not isinstance(j['tokens_req'], bool) and j['tokens_req'] > 0:
                row['tokens'] = j['tokens_req']
            n = int(j.get("chunks") or 0); gen = float(j.get("gen_s") or 0); t1 = float(j.get("first_byte_s") or 0)
            row.update(generated=n, elapsed=round(t1 + gen, 2), tok_s=round(n / (t1 + gen), 4) if t1 + gen > 0 else None,
                       s_tok=(round(gen / (n-1), 3) if n > 1 else None), chunks=n, drafts="—", accept=None,
                       layer_passes=None, first_token_s=round(t1, 2), ds4=True)
            if n > 1 and gen > 0:
                row["tok_s_steady"] = round(float(j.get("decode_tok_s") or (n - 1) / gen), 4)
                row["encode_share_pct"] = round(100 * t1 / (t1 + gen), 1) if (t1 + gen) else None
            row['summary'] = {k: j[k] for k in ('cache_hits', 'cache_misses', 'hit_rate', 'miss_gib', 'pread_s', 'gen_window_gb_per_tok', 'gen_window_gb_by_drive', 'scope_align', 'swap_max_mb', 'swap_growth_mb', 'avail_min_gib', 'ds4_prefill_tps', 'ds4_gen_tps', 'valid', 'dashboard', 'rc') if k in j}
            row['rate_source'] = 'ds4 harness; response chunks used as generated-token count'
            if j.get('valid') not in (None, 'ok'): row['incomplete'] = True
            if j.get("hit_rate") is not None: row["cache_hit_pct"] = round(100 * float(j["hit_rate"]), 1)
            if row.get("tokens") and n < row["tokens"]: row["incomplete"] = True
            if int(j.get("rc") or 0) != 0: row["incomplete"] = True
        except Exception:
            row["incomplete"] = True
    else:
        row["incomplete"] = True
    # The check above is gated on a `tokens=` header that 729 of 836 logs in the
    # corpus DO NOT HAVE, so for 87% of arms it silently evaluated to False and
    # passed truncated runs through as measurements. That is how r24_b (killed at
    # generated=3 of 40) sat in the ledger at 0.0145 tok/s. Two independent
    # fallbacks, because the header is not coming back for arms already written:
    #   (a) the signal — the shell's own "Terminated: 15" / "Killed" line;
    #   (b) peer inference against the block, applied by the caller below.
    if _RE_SIGNAL.search(text):
        row["incomplete"] = True
    # Which model this arm ran: ds4 arms name the file on their CONFIG line; deltafin arms are K3.
    if mm := re.search(r"^CONFIG .*?model=(\S+)", text, re.M):
        row["model"] = os.path.basename(mm.group(1)).replace(".gguf", "")
    elif "[stats]" in text or "deltafin" in text[:2000]:
        row["model"] = "Kimi K3"
    if mm := re.search(r"routed expert size: ([\d.]+) MiB", text):
        row["expert_mib"] = float(mm.group(1))
    for line in text.split("\n"):
        if m := _RE_MIRROR.match(line):
            row["mirror"] = m.group(1)
            break
    try:
        end = re.search(r'^ARM \S+ END (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', text, re.M)
        row['ran'] = end.group(1) if end else time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(path)))
        row['time_source'] = 'harness completion' if end else 'file modification time'
        prompt = re.search(r'^PROMPT \[(.*)\]$', text, re.M) or _RE_PROMPT.search(text.split('\n', 1)[0])
        if prompt:
            import hashlib
            row['prompt_hash'] = hashlib.sha256(prompt.group(1).encode()).hexdigest()
            row['prompt'] = prompt.group(1)[:160]
        config_line = re.search(r'^CONFIG (.*)$', text, re.M)
        config_fields = dict(re.findall(r'(\w+)=(\S+)', config_line.group(1))) if config_line else {}
        row['comparison_context'] = tuple(config_fields.get(k) for k in ('ctx', 'raw', 'temp', 'think'))
    except OSError:
        pass
    # Engine-declared memory facts, parsed from its own startup lines. These are
    # DECLARED, not sampled — kept separate from measured values so the two are
    # never silently added into one invented partition. `unattributed` below is
    # the honest remainder, not an attribution.
    for pat, key, scale in (
        (r"resident=\d+/\d+ \(([\d.]+) GiB\)", "mem_spine_gib", 1.0),
        (r"model\+KV-reserve=([\d.]+) GiB", "mem_qwen_kv_gib", 1.0),
        (r"startup reserve=\d+ tokens/([\d.]+) MiB", "mem_ctx_reserve_gib", 1 / 1024),
        (r"verify snapshots: ([\d.]+) MiB", "mem_verify_gib", 1 / 1024),
    ):
        if m := re.search(pat, text):
            row[key] = round(float(m.group(1)) * scale, 2)
    if m := re.search(r"\[retain\] enabled=true cap_gb=(\d+)", text):
        row["mem_retain_cap_gib"] = int(m.group(1))
    # Features that do NOT appear in `[config] resolved:` and therefore cannot
    # be diffed from it: K3_MIRROR_SCHED, K3_EXPERT_RETAIN, K3_EXPERT_RAM_CACHE.
    # Promoted to first-class columns because a comparison built on the resolved
    # line makes them invisible, and a feature that is OFF in both arms silently
    # drops out of any diff — which is how an entire feature went unmentioned in
    # a settings comparison on 2026-08-31.
    for pat, key in (
        (r"\[native\] mirror: enabled=(\w+)", "feat_mirror"),
        (r"\[native\] retain: enabled=(\w+)", "feat_retain"),
        (r"\[native\] summer: enabled=(\w+)", "feat_summer"),
    ):
        m = re.search(pat, text)
        # "absent" is NOT the same as "off": it means the binary predates the
        # feature, so the arm cannot speak to it either way.
        row[key] = m.group(1) if m else "absent"
    row.update(_parse_sys(path[:-4] + ".sys"))
    # NESTED, not additive. The IOAccelerator "In use system memory" figure
    # ALREADY CONTAINS the resident spine — the spine lives as Metal buffers.
    # Adding spine + metal double counts and yielded an impossible
    # 50.74 + 56.3 = 107 GiB against a measured 76.8 GiB peak. The correct
    # decomposition is a nesting:
    #
    #   ram_peak
    #     +-- metal pool (measured)
    #     |     +-- spine (declared)
    #     |     +-- metal_other  = metal_peak - spine   (arenas/KV/verify on GPU)
    #     +-- host_other = ram_peak - metal_peak        (RSS, cache, OS, retain slabs)
    #
    # Each term is a subtraction of two measured or declared quantities, never
    # an assumed split.
    mp, sp = row.get("gpu_mem_peak"), row.get("mem_spine_gib")
    if mp is not None and sp is not None:
        row["mem_metal_other_gib"] = round(max(0.0, mp - sp), 1)
    if row.get("ram_peak") is not None and mp is not None:
        row["mem_host_other_gib"] = round(max(0.0, row["ram_peak"] - mp), 1)
    row.update(_parse_csv(path[:-4] + ".csv", path[:-4] + ".map"))
    # Kept under one key so the /stats JSON can drop it wholesale — the browser
    # pivot does not need ~120 settings columns per arm, but the CSV export does.
    row['engine'] = 'ds4' if row.get('ds4') or re.search(r'^DS4_ENV ', text, re.M) else 'deltafin' if row.get('model') == 'Kimi K3' else 'unknown'
    row['model_identity'] = model_identity(row.get('model'), row['engine'])
    row['artifacts'] = {label: Path(path).with_suffix(ext).is_file() for label, ext in
                        (('log', '.log'), ('storage', '.csv'), ('memory', '.sys'), ('device_map', '.map'), ('output_hash', '.md5'), ('read_trace', '.readtrace.csv'), ('expert_profile', '.hotlist'))}
    row['artifacts']['expert_profile'] = any(Path(path).with_suffix(ext).is_file() for ext in ('.hotlist', '.expert.json', '.jsonl'))
    try:
        digest = Path(path).with_suffix('.md5').read_text().strip()
        match = re.search(r'\b[0-9a-fA-F]{32}\b', digest)
        if match: row['output_hash'] = match.group(0).lower()
    except OSError:
        pass
    row["set"] = _arm_settings(text)
    profile = streaming_profile(text, engine_header(path))
    row['streaming_method'] = profile['method']
    row['streaming_status'] = profile['status']
    if task := task_record(Path(path).with_suffix('')):
        row['task'] = task
        row['incomplete'] = task['status'] in ('running','unavailable')
        row['engine'] = 'task'
        for key in ('tok_s','tok_s_steady','generated','tokens','chunks','first_token_s'):
            row[key] = None
    return row


def _map_devices(mapfile: str) -> dict:
    """{device: name} for THIS arm, from the .map k3_measure wrote alongside it.

    Never fall back to a hardcoded literal: device numbers change on every
    replug (2026-08-31: K3A disk6->disk5, K3B disk4->disk9, K3C disk14->disk4),
    and a stale literal reports healthy drives as idle and relabels one drive's
    reads as another's. No .map means no per-device attribution, reported as
    absent rather than guessed.
    """
    try:
        with open(mapfile, errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return {}
    dev = {}
    for tok in text.split():
        if "=" in tok:
            name, d = tok.split("=", 1)
            # Volumes were renamed to colours on 2026-09-05 (Yellow = K3A
            # role, Green = K3B role, White = K3C role); the CSV keeps the role
            # columns, so alias the colour names onto them. 33 of 39 rows of
            # the 2026-09-05 export had every enclosure at zero because of this.
            # Exports are keyed by VOLUME name (review, 2026-09-06): the
            # role names K3A/K3B/K3C were read as the wrong drives by a review
            # (K3C taken for Green). Old .map files carrying role names are
            # translated: K3A = Yellow, K3B = Green, K3C = White.
            name = {"K3A": "Yellow", "K3B": "Green", "K3C": "White"}.get(name, name)
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) and re.fullmatch(r"disk\d+", d):
                dev[d] = name
    return dev


def _parse_csv(path: str, mapfile: str) -> dict:
    """Per-device SSD draw for one arm, from its own diskscope counters.

    Same method as k3-table.py, deliberately: ACTIVE rate x duty, where the
    active mean is taken only over samples above a 0.5 GB/s idle floor. A mean
    over a window that includes idle measures the sampler, not the engine — the
    same 846 GB of reads reported 5.66 GB/s over 150 s and 8.95 GB/s over 95 s,
    and that single error propagated into ~45 configs before it was caught.
    Peak is the true maximum; duty is the share of samples above the floor.
    """
    dev = _map_devices(mapfile)
    if not dev:
        return {}
    try:
        cum: dict = {}
        with open(path, errors="ignore") as fh:
            for line in fh:
                r = line.rstrip("\n").split(",")
                if len(r) < 3 or not r[2].isdigit():
                    continue
                try:
                    cum.setdefault(r[1], []).append((float(r[0]), int(r[2])))
                except ValueError:
                    continue
    except OSError:
        return {}
    per, windows = {}, {}
    for disk, name in dev.items():
        points = cum.get(disk, [])
        per[name] = []
        for (ta, ba), (tb, bb) in zip(points, points[1:]):
            if tb <= ta or bb < ba:
                continue
            rate = (bb-ba)/1e9/(tb-ta)
            per[name].append((rate, tb-ta))
            windows.setdefault((ta, tb), {})[name] = rate
    names = list(dev.values())
    per['TOTAL'] = [(sum(v.values()), tb-ta) for (ta, tb), v in windows.items() if set(v) == set(names)]
    out = {}
    for name in list(dict.fromkeys(names+['internal','White','Green','Yellow','TOTAL'])):
        v = per.get(name, [])
        key = 'tot' if name == 'TOTAL' else name
        if not v:
            for metric in ('act','peak','duty','mean'):
                out[f'ssd_{key}_{metric}'] = None
            continue
        duration = sum(dt for rate, dt in v)
        act = [(rate,dt) for rate,dt in v if rate > .5]
        active_time = sum(dt for rate,dt in act)
        out[f'ssd_{key}_act'] = round(sum(rate*dt for rate,dt in act)/active_time, 2) if active_time else 0
        out[f'ssd_{key}_mean'] = round(sum(rate*dt for rate,dt in v)/duration, 2)
        out[f'ssd_{key}_peak'] = round(max(rate for rate,dt in v), 2)
        out[f'ssd_{key}_duty'] = round(100*active_time/duration)
    out['ssd_window'] = 'sampler window; includes any recorded loading and idle time'
    return out


def _parse_sys(path: str) -> dict:
    """Sibling .sys file -> memory/GPU extremes for this arm.

    Columns (k3-memsample.sh, 1 Hz): cpu used avail mps swap_mb gpu, GiB.

    Reported as PEAK used / MIN available / PEAK gpu, never as a mean: a mean
    over a window that includes the idle head and tail measures the sampler,
    not the engine. Rows before the engine allocates are skipped for the
    minimum-available figure by taking the true min over the whole arm, which
    is the moment of greatest pressure — the number that matters on a 128 GiB
    host that has already OOMed decode once with an uncapped retain pool.
    """
    try:
        with open(path, errors="ignore") as fh:
            rows = [ln.split() for ln in fh if ln.strip()]
    except OSError:
        return {}
    used, avail, mps, gpu, cpu, free, inact = [], [], [], [], [], [], []
    for r in rows:
        if len(r) < 6:
            continue
        try:
            cpu.append(float(r[0])); used.append(float(r[1]))
            avail.append(float(r[2])); mps.append(float(r[3]))
            gpu.append(float(r[5]))
            # Columns 7/8 exist only from 2026-08-31. Older arms have six
            # columns and get no raw-free figure rather than a fabricated one.
            if len(r) >= 8:
                free.append(float(r[6])); inact.append(float(r[7]))
        except ValueError:
            continue
    if not used:
        return {}
    out = {"ram_peak": round(max(used), 1), "ram_avail_min": round(min(avail), 1),
           "gpu_mem_peak": round(max(mps), 1), "gpu_pct_peak": round(max(gpu)),
           "cpu_peak": round(max(cpu)), "sys_samples": len(used),
           # Mean as well as peak: the Metal pool grows into the run, so a peak
           # alone overstates what it occupied for most of the arm.
           "gpu_mem_mean": round(sum(mps) / len(mps), 1),
           "ram_used_mean": round(sum(used) / len(used), 1),
           # MEAN and idle share alongside the peak. A peak-only GPU column
           # reported 99-100% for three arms whose means were 34.4%, 52.4% and
           # 53.5% — it made a GPU that is idle 41% of the time look saturated,
           # and that is the campaign's central finding about where the time goes.
           "gpu_pct_mean": round(sum(gpu) / len(gpu), 1),
           "gpu_idle_pct": round(100 * sum(1 for g in gpu if g < 5) / len(gpu), 1)}
    if free:
        out["ram_free_min"] = round(min(free), 1)
        out["ram_inactive_min"] = round(min(inact), 1)
    return out


def _stats_blocks() -> list:
    """Pivot of every arm of every block under k3-soak-logs, newest block first.

    Cached on (block dir, log count, newest mtime) because a full rebuild reads
    every log in the tree. Deltas are computed WITHIN a block only: cross-block
    numbers are not comparable in this project and must never be subtracted.

    Returns the Python structure so the JSON pivot and the CSV export share one
    build — the CSV keeps the per-arm settings, the JSON drops them.
    """
    try:
        dirs = [os.path.join(SOAK, d) for d in os.listdir(SOAK)
                if os.path.isdir(os.path.join(SOAK, d))]
    except OSError:
        return []

    key = []
    for d in dirs:
        try:
            names = os.listdir(d)
            logs = [f for f in names if _is_arm_log(f)]
            artifacts = []
            for name in names:
                if name.endswith(('.log', '.err', '.md5', '.sys', '.map', '.csv', '.hotlist', '.expert.json', '.jsonl', '.engine.txt', '.task.json')):
                    st = os.stat(os.path.join(d, name))
                    artifacts.append((name, st.st_mtime_ns, st.st_size))
            newest = max((x[1] / 1e9 for x in artifacts), default=0)
            key.append((d, len(logs), newest, tuple(sorted(artifacts))))
        except OSError:
            continue
    key_t = tuple(sorted(key))
    if _stats_cache["key"] == key_t and _stats_cache["val"]:
        return _stats_cache["val"]

    blocks = []
    for d, nlogs, newest, _artifact_signature in key_t:
        if not nlogs:
            continue
        rows = []
        for f in sorted(os.listdir(d)):
            if _is_arm_log(f) and (r := _parse_arm_cached(os.path.join(d, f))):
                rows.append(r)
        if not rows:
            continue
        # Fallback (b) for the missing `tokens=` header: infer the block's token
        # target from its own arms. Arms in a block share a target, so the modal
        # generated count is that target; anything materially short of it was cut
        # off. Uses the MODE, not the max, so one over-running arm cannot condemn
        # the rest of the block. Only fills in where the header was absent — a
        # real header always wins.
        counts = [r["generated"] for r in rows if r.get("generated")]
        if counts:
            target = max(set(counts), key=counts.count)
            for r in rows:
                if not r.get("tokens") and r.get("generated") is not None:
                    r["tokens_inferred"] = target
                    if r["generated"] < target:
                        r["incomplete"] = True
        # Baseline for this block: first control-looking arm, else first arm.
        # A truncated arm must never become the yardstick the block is read
        # against, and must not be silently comparable to complete arms.
        # Review 2026-09-08: delta_pct once compared 128/512-token generation
        # arms with an 8-token prefill arm (>4,000% "gains"). A baseline is only
        # meaningful at the SAME generation length, and the natural yardstick at
        # a length is its plain-decode control (drafter off) when one exists.
        ok = [r for r in rows if r.get("tok_s") and not r.get("incomplete")]
        by_len: dict = {}
        for r in ok:
            # A ds4 block can contain several prompts and context lengths.
            match = (_arm_length(r), r.get('model'), r.get('prompt_hash'), r.get('comparison_context'))
            if r.get('ds4') and not r.get('prompt_hash'):
                match += (r['arm'],)  # No inferred matched comparison without a prompt.
            by_len.setdefault(match, []).append(r)
        for length, group in by_len.items():
            base = next((r for r in group if _arm_mode(r) == "off"), None)
            if base is None:
                base = next((r for r in group if _RE_CTL.search(r["arm"])), None)
            if base is None:
                base = group[0]
            for r in group:
                metric = 'tok_s_steady' if r.get('tok_s_steady') and base.get('tok_s_steady') else 'tok_s'
                r['delta_metric'] = metric
                r['delta_pct'] = round(100 * (r[metric] - base[metric]) / base[metric], 2)
                r["delta_vs"] = base["arm"]
            base["is_base"] = True
        blocks.append({"block": os.path.basename(d), "mtime": newest,
                       "arms": len(rows), "rows": rows})
    blocks.sort(key=lambda b: b["mtime"], reverse=True)
    _stats_cache.update(key=key_t, val=blocks)
    return blocks


def stats_payload() -> bytes:
    """The pivot as JSON, with per-arm settings stripped.

    ~120 settings columns x ~840 arms is several MB the browser table never
    renders. They stay available through /stats.csv.
    """
    blocks = _stats_blocks()
    lean = [{**b, "rows": [{k: v for k, v in r.items() if k != "set"} for r in b["rows"]]}
            for b in blocks]
    return json.dumps({"blocks": lean}, separators=(",", ":")).encode()


# Measurement columns emitted before the settings families, in this order. Any
# key present on a row but not listed here still appears — see stats_csv.
_CSV_LEAD = ["block", "variant", "rep", "arm", "ran", "valid", "tokens", "generated", "incomplete", "is_base",
             "tok_s", "tok_s_steady", "prefill_tok_s", "first_token_s", "ttft_s_derived", "encode_share_pct", "wall_s",
             "delta_pct", "output_sha", "weights", "cache_experts", "cache_gib", "lands_last",
             "gb_primary", "gb_green", "gb_white", "gb_blue", "swap_mb", "dead_knobs", "notes",
             "s_tok", "elapsed", "chunks", "drafts", "accept",
             "layer_passes", "prompt", "overrides", "mirror",
             "feat_mirror", "feat_retain", "feat_summer"]


def stats_csv(frm: str = "", to: str = "", only_block: str = "", *,
              hours: int | None = None, last_runs: int | None = None,
              now: float | None = None) -> bytes:
    """Every arm of every block as one CSV, with the settings families attached.

    Date range is inclusive on both ends and uses each arm's `ran` timestamp
    (harness completion, or file mtime), never the block name. Relative hours
    end at the server's current time. Recent-count exports sort across blocks,
    newest first, with block/arm as the stable tie-break. Recent exports omit
    undated rows; all-runs and legacy date exports retain and mark them.

    Columns are the UNION across the selected arms, so a knob that appears in
    only one block still gets a column rather than being hidden.
    """
    if hours is not None and (type(hours) is not int or hours not in (1, 3, 6, 24)):
        raise ValueError('Choose 1, 3, 6 or 24 hours.')
    if last_runs is not None and (type(last_runs) is not int or last_runs not in (1, 5, 10, 20)):
        raise ValueError('Choose the latest 1, 5, 10 or 20 runs.')
    if hours is not None and last_runs is not None:
        raise ValueError('Choose a time range or a run count, not both.')
    now = time.time() if now is None else now
    blocks = _stats_blocks()
    rows: list[dict] = []
    recent: list[tuple] = []
    for b in blocks:
        if only_block and b["block"] != only_block:
            continue
        for r in b["rows"]:
            day = (r.get("ran") or "")[:10]
            if frm and day and day < frm:
                continue
            if to and day and day > to:
                continue
            flat = {k: v for k, v in r.items() if k != "set"}
            flat["block"] = b["block"]
            if not day:
                flat["ran"] = "(no mtime)"
            flat.update(r.get("set") or {})
            if hours is not None or last_runs is not None:
                try:
                    # Naive harness timestamps are local to the Mac serving
                    # the reports. ISO timestamps with an offset retain it.
                    stamp = datetime.fromisoformat(r.get('ran') or '').timestamp()
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
                if hours is not None and not now - hours * 3600 <= stamp <= now:
                    continue
                recent.append((stamp, b['block'], r.get('arm', ''), flat))
                continue
            rows.append(flat)
    if hours is not None or last_runs is not None:
        recent.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        rows = [entry[3] for entry in (recent[:last_runs] if last_runs is not None else recent)]
    if not rows:
        return b"# no arms matched the selected range\n"

    seen: set = set()
    for r in rows:
        seen.update(r)
    lead = [c for c in _CSV_LEAD if c in seen]
    rest = sorted(seen - set(lead))
    # Settings last and grouped by family, so the measurement columns stay
    # readable when the sheet is opened without scrolling 120 columns right.
    req = [c for c in rest if c.startswith("req_")]
    cfg = [c for c in rest if c.startswith("cfg_")]
    nat = [c for c in rest if c.startswith("nat_")]
    other = [c for c in rest if not c.startswith(("req_", "cfg_", "nat_"))]
    cols = lead + other + req + cfg + nat

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", restval="")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue().encode()


# Reading every .csv in the tree is ~16M lines; a browser request must not sit
# on that. Above this many arms the export REFUSES with a message rather than
# quietly returning a prefix — a truncated export that looks complete is the
# same failure as a truncated arm reported as a measurement.
LIVE_ARM_CAP = 300


def live_csv(frm: str = "", to: str = "", only_block: str = "") -> bytes:
    """Per-second telemetry time series for every arm in the range.

    One row per ARM per SECOND: CPU, RAM, Metal/GPU, swap, and per-drive read
    throughput. This is the sample-level data behind the Live tab; the Stats
    export carries only each arm's peaks, which cannot show WHEN pressure
    happened or which drive stalled.

    THE JOIN IS BY ELAPSED SECONDS FROM ARM START, NOT A SHARED CLOCK.
    .sys is sampled at 1 Hz with no timestamp (row index = second) and the
    diskscope .csv is sampled at 200 ms with its own t_s. They are started
    independently by the harness, so alignment can be off by up to ~1 s. That is
    fine for reading trends and wrong for claiming a precise lead/lag between a
    memory event and a drive event. `drive_bins` reports how many 200 ms samples
    backed each second so thin bins are visible rather than implied.

    Drive columns are READ throughput only: diskscope emits a separate `<disk>w`
    row for writes, which is not in the arm's .map and is therefore skipped.
    """
    blocks = _stats_blocks()
    picked = []
    for b in blocks:
        if only_block and b["block"] != only_block:
            continue
        for r in b["rows"]:
            day = (r.get("ran") or "")[:10]
            if frm and day and day < frm:
                continue
            if to and day and day > to:
                continue
            picked.append((b["block"], r))
    if not picked:
        return b"# no arms matched the selected range\n"
    if len(picked) > LIVE_ARM_CAP:
        return (f"# REFUSED: {len(picked)} arms matched, cap is {LIVE_ARM_CAP}.\n"
                f"# Per-second telemetry for that many arms is ~{len(picked) * 19500 // 1000}k "
                f"input lines and would stall the request.\n"
                f"# Narrow the date range or pick a single block, then export again.\n"
                ).encode()

    names = ["internal", "Yellow", "Green", "White"]
    # Decode rate travels WITH every telemetry row. Roughly half this campaign's
    # experiments (retain, summer, ram-cache) change bytes-read-per-token by
    # design, so for those a LOWER GB/s at equal tok/s is the goal — ranking them
    # on total_gbs alone is meaningless or inverted. An external review ranked
    # arms from this file and could not interpret C10 (+4.5% GB/s) or RET (-3.9%)
    # without it. It is per-arm constant, repeated per row so the file needs no join.
    cols = (["block", "arm", "ran", "tok_s", "generated", "tokens", "incomplete",
             "engine_s", "sampler_overrun_s", "t_s",
             "cpu_pct", "ram_used_gib", "ram_avail_gib",
             "gpu_mem_gib", "swap_mb", "gpu_pct", "ram_free_gib", "ram_inactive_gib"]
            + [f"{n}_gbs" for n in names] + ["total_gbs", "drive_bins"])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", restval="")
    w.writeheader()

    for block, r in picked:
        stem = os.path.join(SOAK, block, r["arm"])
        try:
            sysrows = [ln.split() for ln in open(stem + ".sys", errors="ignore") if ln.strip()]
        except OSError:
            sysrows = []
        # Per-second mean read rate per device, from cumulative byte counters.
        binned: dict = {}
        dev = _map_devices(stem + ".map")
        if dev:
            cum: dict = {}
            try:
                with open(stem + ".csv", errors="ignore") as fh:
                    for line in fh:
                        f = line.rstrip("\n").split(",")
                        if len(f) < 3 or f[1] not in dev or not f[2].isdigit():
                            continue
                        try:
                            cum.setdefault(f[1], []).append((float(f[0]), int(f[2])))
                        except ValueError:
                            continue
            except OSError:
                cum = {}
            for disk, name in dev.items():
                s = cum.get(disk, [])
                for i in range(1, len(s)):
                    dt = s[i][0] - s[i - 1][0]
                    if dt <= 0:
                        continue
                    rate = (s[i][1] - s[i - 1][1]) / 1e9 / dt
                    binned.setdefault(int(s[i][0]), {}).setdefault(name, []).append(rate)

        # CLIP TO THE ENGINE RUN. diskscope is started and stopped independently
        # of the engine and on 8 arms in this corpus it was never reaped — worst
        # case eureka/t64_a, where a 70 s arm carries 10,052 s of .csv (143x).
        # Emitting that whole span produced two artefacts an external review then
        # reported as findings: rows past the engine's exit have blank cpu/ram/gpu
        # (read as "schema drift, 30% nulls") and idle disk samples drag the mean
        # (ARENA30 read as "14% duty", C1 as "0.96 GB/s effective" — both are
        # mostly post-run idle, not broken arms). t64_a's "70-minute I/O stall
        # then recovery at 26%" is an idle machine plus later unrelated work
        # bleeding into an unreaped sampler, not a thermal event.
        # The overrun is REPORTED rather than silently trimmed, so the harness bug
        # stays visible in the data instead of being cleaned out of sight.
        csv_span = (max(binned) + 1) if binned else 0
        engine_s = r.get("elapsed")
        span = len(sysrows) or csv_span
        if engine_s:
            span = min(span or csv_span, int(engine_s) + 1)
        overrun = round(max(0.0, csv_span - (engine_s or csv_span)), 1)
        for t in range(span):
            row = {"block": block, "arm": r["arm"], "ran": r.get("ran", ""), "t_s": t,
                   "tok_s": r.get("tok_s"), "generated": r.get("generated"),
                   "tokens": r.get("tokens") or r.get("tokens_inferred"),
                   "incomplete": r.get("incomplete", False),
                   "engine_s": engine_s, "sampler_overrun_s": overrun}
            sr = sysrows[t] if t < len(sysrows) else []
            if len(sr) >= 6:
                try:
                    row.update(cpu_pct=float(sr[0]), ram_used_gib=float(sr[1]),
                               ram_avail_gib=float(sr[2]), gpu_mem_gib=float(sr[3]),
                               swap_mb=float(sr[4]), gpu_pct=float(sr[5]))
                    # Columns 7/8 exist only from 2026-08-31; older arms leave
                    # them blank rather than reporting a fabricated zero.
                    if len(sr) >= 8:
                        row.update(ram_free_gib=float(sr[6]), ram_inactive_gib=float(sr[7]))
                except ValueError:
                    pass
            bucket = binned.get(t, {})
            tot = 0.0
            nbins = 0
            for n in names:
                v = bucket.get(n) or []
                if v:
                    m = sum(v) / len(v)
                    row[f"{n}_gbs"] = round(m, 3)
                    tot += m
                    nbins = max(nbins, len(v))
            if bucket:
                row["total_gbs"] = round(tot, 3)
                row["drive_bins"] = nbins
            w.writerow(row)
    return buf.getvalue().encode()


# ---------------------------------------------------------- scheduler tab
# Per-device cost constants the engine actually uses (storage.rs
# MIRROR_COST_US), and the bandwidth each was derived from.
SCHED_COST_US = {"internal": 1502, "K3C": 2478, "K3B": 3122, "K3A": 3025}



# ---- "by Layer" tab: per-barrier Gantt from a K3_READ_TRACE csv ---------------
# A barrier = one expert tile (one layer pass). Device records carry the tier
# that served each read and its transfer duration (per chunk on the 2026-09-06
# binary; per file with the open duration only on older traces, in which case
# the matching "read" record's duration is borrowed). The tab answers, per
# layer pass: which device finished last and how long the pass waited for it.
_BYLAYER_CACHE: dict = {}
_BYLAYER_ROLE = {"K3A": "Yellow", "K3B": "Green", "K3C": "White", "internal": "internal", "White": "White", "Green": "Green", "Yellow": "Yellow"}

def _bylayer_parse(path: str) -> dict:
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if _BYLAYER_CACHE.get("key") == key:
        return _BYLAYER_CACHE["data"]
    barriers: dict = {}
    reads_by_key: dict = {}
    with open(path, errors="ignore") as fh:
        header = fh.readline().strip().split(",")
        lengths_exact = "len" in header
        has_prio = "prio" in header
        has_target = "target_barrier" in header      # 2026-09-06 format: exact pass and byte range per read
        for line in fh:
            r = line.rstrip("\n").split(",")
            if len(r) < 6:
                continue
            try:
                t_us, b, layer, expert, src, dur_ns = int(r[0]), int(r[1]), int(r[2]), int(r[3]), r[4], int(r[5])
            except ValueError:
                continue
            prio = r[6] if has_prio and len(r) > 6 else "-"
            rec_bytes = None
            if has_target and len(r) > 10:
                try:
                    b = int(r[header.index("target_barrier")]); rec_bytes = int(r[header.index("len")]) if lengths_exact else int(r[10])
                except ValueError:
                    pass
            if lengths_exact:
                try:
                    rec_bytes = int(r[header.index('len')])
                    if rec_bytes < 0:
                        continue
                except (ValueError, IndexError):
                    continue
            if src == "read":
                if expert == 1023 and dur_ns == 0:
                    barriers.setdefault(b, {"layer": layer, "recs": []})["begin"] = t_us / 1000.0
                    continue
                reads_by_key[(b, layer, expert)] = max(reads_by_key.get((b, layer, expert), 0), dur_ns)
                continue
            dev = _BYLAYER_ROLE.get(src, src if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", src) else None) or (src if src in ("internal", "Green", "White", "Yellow") else None)
            if not dev:
                continue
            barriers.setdefault(b, {"layer": layer, "recs": []})["recs"].append([t_us, dev, dur_ns, expert, prio, layer, rec_bytes])
    # older traces: device record = open only (~60 us); borrow the transfer duration
    for b, info in barriers.items():
        for rec in info["recs"]:
            if rec[2] < 500_000 and rec[4] == "-":
                borrowed = reads_by_key.get((b, info["layer"], rec[3]))
                if borrowed:
                    rec[2] = borrowed
    # Marker alignment: captures on 2026-09-06 stamped the
    # pass-begin marker one barrier behind the reads of that pass (fetch_add's
    # old value); detect (markers never coincide with read barriers, shifted by
    # one they do) and shift.
    marked = [b for b, info in barriers.items() if "begin" in info]
    if marked:
        # a marker that stamps LATER than every read of its own pass belongs to the next pass
        late_markers = 0; checked = 0
        for b in marked:
            recs = barriers[b]["recs"]
            if not recs:
                continue
            checked += 1
            if barriers[b]["begin"] > max(r[0] / 1000.0 for r in recs):
                late_markers += 1
        if checked and late_markers > checked * 0.5:
            for b in sorted(marked, reverse=True):
                begin = barriers[b].pop("begin")
                if b + 1 in barriers:
                    barriers[b + 1]["begin"] = begin
                elif not barriers[b]["recs"]:
                    barriers.pop(b, None)
    # A prefetch read (prio P) for layer L issued during barrier b (layer Lb) lands for the
    # pass of layer L, i.e. barrier b + ((L - Lb) mod 93): move it there so each barrier's
    # Gantt holds the reads that gate THAT pass. Demand/union reads stay where they were.
    moved = {}
    for b in list(barriers):
        info = barriers[b]; keep = []
        for rec in info["recs"]:
            if not has_target and not lengths_exact and rec[4] == "P" and rec[5] != info["layer"]:
                target = b + ((rec[5] - info["layer"]) % 93)
                moved.setdefault(target, []).append(rec)
            else:
                keep.append(rec)
        info["recs"] = keep
    for target, recs in moved.items():
        if target in barriers:
            barriers[target]["recs"].extend(recs)
    # bytes per record: a file read as n jobs in one barrier -> each job is FULL/n bytes
    for b, info in barriers.items():
        n_per = {}
        for rec in info["recs"]:
            n_per[(rec[5], rec[3])] = n_per.get((rec[5], rec[3]), 0) + 1
        for rec in info["recs"]:
            rec[6] = rec[6] if rec[6] is not None else 17_547_264 / n_per[(rec[5], rec[3])]
    devs = sorted({r[1] for info in barriers.values() for r in info["recs"]})
    per_barrier = []
    last_count = {d: 0 for d in devs}
    wait_for = {d: [] for d in devs}
    for b in sorted(barriers):
        info = barriers[b]; recs = info["recs"]
        finish = {}; start_all = None; bytes_dev = {d: 0 for d in devs}; n_dev = {d: 0 for d in devs}
        for t_us, dev, dur_ns, _e, prio, _layer, nbytes in recs:
            end = t_us / 1000.0; start = end - dur_ns / 1.0e6
            finish[dev] = max(finish.get(dev, 0.0), end)
            start_all = start if start_all is None else min(start_all, start)
            bytes_dev[dev] += nbytes
            n_dev[dev] += 1
        if not finish:
            continue
        begin = info.get("begin")
        if begin is not None:
            # exact format: the pass starts at its marker; reads that landed before it were
            # prefetched in time and cannot be "last"; the span runs from the marker
            late = {d: t for d, t in finish.items() if t > begin}
            if not late:
                per_barrier.append({"b": b, "layer": info["layer"], "last": "none", "wait_ms": 0.0, "span_ms": 0.0,
                                    "n": len(recs), "gb": round(sum(bytes_dev.values()) / 1e9, 3), "finish": {}})
                continue
            finish = late; start_all = begin
        order = sorted(finish.items(), key=lambda kv: kv[1])
        last_dev, last_t = order[-1]
        second_t = order[-2][1] if len(order) > 1 else start_all
        if begin is not None and len(order) == 1:
            second_t = begin
        wait_ms = last_t - second_t
        last_count[last_dev] += 1
        wait_for[last_dev].append(wait_ms)
        per_barrier.append({"b": b, "layer": info["layer"], "last": last_dev, "wait_ms": round(wait_ms, 2),
                            "span_ms": round(last_t - start_all, 2), "n": len(recs),
                            "gb": round(sum(bytes_dev.values()) / 1e9, 3),
                            "finish": {d: round(finish[d] - start_all, 2) for d in finish}})
    def pct(v, q):
        if not v: return 0.0
        v = sorted(v); return round(v[min(len(v) - 1, int(round((len(v) - 1) * q)))], 2)
    nb = max(1, len(per_barrier))
    last_count.setdefault("none", 0); wait_for.setdefault("none", [])
    summary = []
    for d in devs:
        n = sum(1 for info in barriers.values() for r in info["recs"] if r[1] == d)
        gb = sum(r[6] for info in barriers.values() for r in info["recs"] if r[1] == d) / 1e9
        busy = sum(r[2] for info in barriers.values() for r in info["recs"] if r[1] == d) / 1.0e6
        ends = [r[0]/1000 for info in barriers.values() for r in info['recs']]
        starts = [r[0]/1000-r[2]/1e6 for info in barriers.values() for r in info['recs']]
        span_total = max(ends)-min(starts) if ends else 0
        summary.append({"dev": d, "reads": n, "bytes": sum(r[6] for info in barriers.values() for r in info["recs"] if r[1] == d), "gb": round(gb, 1), "last_pct": round(100.0 * last_count[d] / nb, 1),
                        "wait_p50": pct(wait_for[d], 0.5), "wait_p90": pct(wait_for[d], 0.9),
                        "wait_total_ms": round(sum(wait_for[d]), 0),
                        "concurrency": round(busy / span_total, 2) if span_total else 0.0})
    data = {"path": path, "barriers": per_barrier, "summary": summary, "exact_passes": has_target, "byte_source": "trace lengths" if lengths_exact or has_target else "estimated K3 record size",
            "span_p50": pct([pb["span_ms"] for pb in per_barrier], 0.5),
            "span_p90": pct([pb["span_ms"] for pb in per_barrier], 0.9),
            "concurrency_basis": "whole trace interval from first read start to last read end", "timing_note": "Last-landing gap describes recorded reads; it is not measured GPU stall or recoverable time. Legacy trace timestamps may be approximate.", "has_prio": has_prio, "records": sum(len(i["recs"]) for i in barriers.values())}
    _BYLAYER_CACHE["key"] = key; _BYLAYER_CACHE["data"] = data; _BYLAYER_CACHE["raw"] = barriers
    return data


def bylayer_csv_payload(path: str) -> bytes:
    """CSV of the by-Layer analysis: one row per barrier (layer pass) plus a summary block."""
    if not path or not os.path.exists(path):
        return b"error,no such trace\n"
    data = _bylayer_parse(path)
    devs = ["internal", "White", "Green", "Yellow"]
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["trace", path]); w.writerow(["device_records", data["records"]]); w.writerow(["barriers", len(data["barriers"])])
    w.writerow(["pass_span_ms_p50", data["span_p50"]]); w.writerow(["pass_span_ms_p90", data["span_p90"]]); w.writerow([])
    w.writerow(["summary_device", "reads", "GB", "lands_last_pct_of_passes", "extra_wait_when_last_p50_ms", "extra_wait_when_last_p90_ms", "extra_wait_total_s", "mean_concurrent_reads"])
    for r in data["summary"]:
        w.writerow([r["dev"], r["reads"], r["gb"], r["last_pct"], r["wait_p50"], r["wait_p90"], round(r["wait_total_ms"] / 1000, 1), r["concurrency"]])
    w.writerow([])
    w.writerow(["barrier", "layer", "last_device", "extra_wait_for_last_ms", "pass_span_ms", "reads", "GB"] + [f"{d}_finish_ms" for d in devs])
    for pb in data["barriers"]:
        w.writerow([pb["b"], pb["layer"], pb["last"], pb["wait_ms"], pb["span_ms"], pb["n"], pb["gb"]] + [pb["finish"].get(d, "") for d in devs])
    return out.getvalue().encode()

def bylayer_list_payload() -> bytes:
    import glob
    files = []
    for f in glob.glob(os.path.join(ROOT, "k3-soak-logs", "*", "readtrace*.csv")) + glob.glob(os.path.join(SOAK, "*", "*.readtrace.csv")):
        st = os.stat(f)
        files.append({"path": f, "mtime": st.st_mtime, "mb": round(st.st_size / 1e6, 1)})
    files.sort(key=lambda x: -x["mtime"])
    note = "" if files else ("No per-read traces yet. deltafin: K3_READ_TRACE csv. ds4: apply tools/ds4/apply-read-trace-patch.py, "
                             "rebuild, then run a trace arm (GLM_TRACE=1 writes <tag>.readtrace.csv next to the log).")
    return json.dumps({"traces": files, "note": note}).encode()

def bylayer_payload(path: str, barrier: int) -> bytes:
    if not path or not os.path.exists(path):
        return json.dumps({"error": "no such trace"}).encode()
    data = _bylayer_parse(path)
    raw = _BYLAYER_CACHE.get("raw", {})
    gantt = None
    if data["barriers"]:
        bs = [pb["b"] for pb in data["barriers"]]
        if barrier not in raw:
            barrier = bs[len(bs) // 2]
        recs = raw[barrier]["recs"]
        t0 = min(r[0] / 1000.0 - r[2] / 1.0e6 for r in recs)
        gantt = {"b": barrier, "layer": raw[barrier]["layer"],
                 "reads": [{"dev": r[1], "start": round(r[0] / 1000.0 - r[2] / 1.0e6 - t0, 3),
                            "end": round(r[0] / 1000.0 - t0, 3), "expert": r[3], "prio": r[4]} for r in recs]}
    out = {k: v for k, v in data.items()}
    out["gantt"] = gantt
    return json.dumps(out).encode()

def scheduler_payload() -> bytes:
    """Charged vs measured, per drive, for every arm that ran the scheduler.

    `[mirror-split]` reports what the CLOCKS charged; the diskscope CSV reports
    what the DRIVES did. If the cost model were right these would agree — every
    expert record is the same 17,547,264 B, so open share and byte share track.

    SUPERSEDED 2026-08-31 (this docstring previously asserted the opposite).
    The single-arm observation that motivated this tab — K3C charged 23.5% and
    carrying 27.2% — does not survive the full population. Across all 21
    scheduler arms the divergence is a small FIXED OFFSET, not a calibration
    error: internal mean -1.21 pp (sd 0.85), K3C -0.86 (sd 1.71), K3B +1.30
    (sd 0.49), K3A +0.77 (sd 0.58). The decisive test is whether the gap tracks
    speed — a wrong constant would misprice more heavily when the pipeline is
    working harder. It does not:

        correlation(tok_s, internal gap) = +0.056  over 0.3555..0.5827 tok/s

    So the cost model and its constants are fine within measurement error, and
    the tab's job has changed: it is no longer evidence the scheduler steers on
    a bad model, it is evidence the model is NOT the problem. The two arms with
    real divergence (R_ON, SCHED_ON at K3C ~ -4.4 pp) are the ones where the
    LAYOUT changed, not the cost model, and both were slower than their controls.

    CAPABILITY IS THE SUSPECT NUMBER NOW — BUT `cap_peak` CANNOT SAY WHICH WAY.
    internal reports 12.72 GB/s against a rated 11.68. A device cannot exceed its
    own ceiling, so one of the two is wrong: either the rating is understated, or
    the metric is aliased. `cap_peak` is the max over 200 ms windows of v1 byte
    deltas, so it is bounded by the device rate AND by duty cycle within the
    window — a device whose bytes arrive in long contiguous bursts reports a
    higher "peak" than an equally fast device with the same bytes spread across
    more, shorter bursts. internal carries ~40% of traffic on one device, so its
    bursts are the densest, which is exactly the shape that would alias high.

    THE SYMMETRY IS THE POINT (external review 2026-08-31): if the metric is
    unreliable enough to report internal beating its ceiling, then the
    enclosures' -6..-10% deficits cannot be trusted either, and the tempting
    conclusion — "the constants are miscalibrated against the fastest device" —
    is not supported. It is NOT independently corroborated by the Little's Law
    B-infinity fit as first claimed: that fit uses v3 (residency) rather than v1
    (bytes), so the columns differ, but both come from the same sampler at the
    same timestamps and a shared completion-batching artefact would produce
    agreement without either being right.

    RESOLVED BY: rerunning a rung under k3-diskscope at 10 ms and taking rolling
    100 ms maxima. Recorded precedent (2026-08-26): rolling-100 ms landed on
    measured capability (5.83-5.98 vs 5.58 rated; 14.03-14.05 vs 13.73) while
    single-tick values overshot to 6.8-7.4 and 15.9 through completion batching.
    If all four devices land on their ratings at that resolution, cap_peak is an
    artefact and this whole line of reasoning is void. Until then the rated
    constants stay UNPATCHED — the direction of any retune currently rests on a
    metric that reports a device beating its own ceiling.
    """
    rows = []
    try:
        blocks = sorted(os.listdir(SOAK), reverse=True)
    except OSError:
        return json.dumps({"arms": [], "cost": SCHED_COST_US, "cap": CAP}).encode()
    for block in blocks:
        bdir = os.path.join(SOAK, block)
        if not os.path.isdir(bdir):
            continue
        for name in sorted(os.listdir(bdir)):
            if not _is_arm_log(name):
                continue
            arm = name[:-4]
            try:
                text = open(os.path.join(bdir, name), errors="ignore").read()
            except OSError:
                continue
            m = re.search(r"\[mirror-split\] internal=(\d+) K3C=(\d+) K3B=(\d+) K3A=(\d+)", text)
            if not m:
                continue
            charged = {"internal": int(m.group(1)), "K3C": int(m.group(2)),
                       "K3B": int(m.group(3)), "K3A": int(m.group(4))}
            if sum(charged.values()) == 0:
                continue       # scheduler present but disabled: nothing to compare
            drawn = _parse_csv(os.path.join(bdir, arm + ".csv"),
                               os.path.join(bdir, arm + ".map"))
            sp = re.findall(r"speed=([\d.]+) token/s", text)
            rows.append({
                "block": block, "arm": arm,
                "tok_s": float(sp[-1]) if sp else None,
                "charged": charged,
                "drawn": {d: drawn.get(f"ssd_{d}_act") for d in CAP},
                "peak": {d: drawn.get(f"ssd_{d}_peak") for d in CAP},
            })
            if len(rows) >= 30:
                break
        if len(rows) >= 30:
            break
    # Observed maximum per device across the arms in view. Reported ALONGSIDE
    # the rated cap, never in place of it — internal has been seen at 12.72
    # against a rated 11.68, so the rated figure is demonstrably low, but a peak
    # is a single sample and may be burst-completion accounting rather than
    # sustained capability. Showing both makes the discrepancy auditable.
    cap_peak = {}
    for d in CAP:
        seen = [r["peak"].get(d) for r in rows if r.get("peak", {}).get(d)]
        if seen:
            cap_peak[d] = round(max(seen), 2)
    return json.dumps({"arms": rows, "cost": SCHED_COST_US, "cap": CAP,
                       "cap_peak": cap_peak},
                      separators=(",", ":")).encode()


def scheduler_csv(frm: str = "", to: str = "", only_block: str = "") -> bytes:
    """Scheduler arms as CSV: what the clocks CHARGED vs what the drives DID.

    Both are carried per device, as counts AND as shares, plus the gap in
    percentage points. The gap is the reason this tab exists: every expert
    record is the same 17,547,264 B, so charged share and drawn share should
    track. On 2026-08-31 K3C was charged 23.5% and carried 27.2% — the model the
    scheduler steers on disagreeing with the hardware by about the size of the
    effect being chased. A CSV that exported only one side would hide exactly
    the thing worth analysing.

    Unlike the tab, this is NOT capped at 30 arms — the cap exists to keep the
    page light, and silently truncating a download would misrepresent the
    population.
    """
    rows = []
    try:
        blocks = sorted(os.listdir(SOAK), reverse=True)
    except OSError:
        return b"# k3-soak-logs unreadable\n"
    devs = list(CAP)
    for block in blocks:
        bdir = os.path.join(SOAK, block)
        if not os.path.isdir(bdir) or (only_block and block != only_block):
            continue
        for name in sorted(os.listdir(bdir)):
            if not _is_arm_log(name):
                continue
            arm = name[:-4]
            path = os.path.join(bdir, name)
            try:
                text = Path(path).read_text(errors='ignore')
            except OSError:
                continue
            m = re.search(r"\[mirror-split\] internal=(\d+) K3C=(\d+) K3B=(\d+) K3A=(\d+)", text)
            if not m:
                continue
            charged = {"internal": int(m.group(1)), "K3C": int(m.group(2)),
                       "K3B": int(m.group(3)), "K3A": int(m.group(4))}
            tot_charged = sum(charged.values())
            if tot_charged == 0:
                continue
            try:
                ran = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
            except OSError:
                ran = ""
            day = ran[:10]
            if frm and day and day < frm:
                continue
            if to and day and day > to:
                continue
            drawn = _parse_csv(os.path.join(bdir, arm + ".csv"),
                               os.path.join(bdir, arm + ".map"))
            sp = re.findall(r"speed=([\d.]+) token/s", text)
            gen = re.findall(r"generated=(\d+)", text)
            tot_drawn = sum((drawn.get(f"ssd_{d}_act") or 0.0) for d in devs)
            row = {"block": block, "arm": arm, "ran": ran,
                   "tok_s": float(sp[-1]) if sp else "",
                   "generated": int(gen[-1]) if gen else "",
                   "charged_total": tot_charged,
                   "drawn_total_gbs": round(tot_drawn, 3)}
            for d in devs:
                act = drawn.get(f"ssd_{d}_act")
                cpct = 100.0 * charged[d] / tot_charged
                dpct = (100.0 * act / tot_drawn) if (act and tot_drawn) else None
                row[f"cost_us_{d}"] = SCHED_COST_US.get(d, "")
                row[f"cap_gbs_{d}"] = CAP.get(d, "")
                row[f"charged_{d}"] = charged[d]
                row[f"charged_pct_{d}"] = round(cpct, 2)
                row[f"drawn_gbs_{d}"] = act if act is not None else ""
                row[f"drawn_pct_{d}"] = round(dpct, 2) if dpct is not None else ""
                row[f"peak_gbs_{d}"] = drawn.get(f"ssd_{d}_peak", "")
                # The headline number: positive = charged MORE than it carried.
                row[f"gap_pp_{d}"] = round(cpct - dpct, 2) if dpct is not None else ""
            rows.append(row)
    if not rows:
        return b"# no scheduler arms matched the selected range\n"
    cols = ["block", "arm", "ran", "tok_s", "generated", "charged_total", "drawn_total_gbs"]
    for d in devs:
        cols += [f"cost_us_{d}", f"cap_gbs_{d}", f"charged_{d}", f"charged_pct_{d}",
                 f"drawn_gbs_{d}", f"drawn_pct_{d}", f"peak_gbs_{d}", f"gap_pp_{d}"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", restval="")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode()


# ------------------------------------------------------- single-arm detail
def arm_detail(block: str, arm: str) -> bytes:
    """Everything the engine declared about ONE arm.

    Sourced from the arm's own log, never from what the harness believed it
    sent: the `[config] resolved:` line is what the engine actually resolved,
    and several knobs (K3_SPEC_DEPTH, K3_EXPERT_RETAIN, K3_SUMMER_POOL) do not
    appear in it at all — which is why the `[native]` lines are shown beside it.
    A setting visible in neither can only be asserted by effect.
    """
    block = os.path.basename(block or "")
    arm = os.path.basename(arm or "")
    path = os.path.join(SOAK, block, arm + ".log")
    if not os.path.isfile(path):
        return json.dumps({"error": f"no such arm: {block}/{arm}"}).encode()
    try:
        text = Path(path).read_text(errors='ignore')
    except OSError as error:
        return json.dumps({"error": str(error)}).encode()

    out: dict = {"block": block, "arm": arm}
    first = text.split("\n", 1)[0]
    if m := _RE_ARM.match(first):
        ov = m.group(3)
        if p2 := _RE_PROMPT.search(ov):
            out["prompt"] = p2.group(1)
            ov = ov[: p2.start()]
        out["overrides"] = [t for t in ov.split() if "=" in t]
    if m := re.search(r"\[config\] resolved:(.*)", text):
        cfg = {}
        for tok in m.group(1).split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                cfg[k] = v
        out["config"] = cfg
    # Engine-declared lines: model shape, mirror/retain/summer state, memory.
    out["native"] = [ln for ln in text.split("\n") if ln.startswith("[native]")][:14]
    # End-of-run counters.
    tail = {}
    for tag in ("[stats]", "[phases]", "[opens]", "[metal-cache]", "[verify-width]",
                "[mirror-split]", "[retain]", "[summer]", "[stall-trace]"):
        hits = [ln for ln in text.split("\n") if ln.startswith(tag)]
        if hits:
            tail[tag.strip("[]")] = hits[-1]
    out["counters"] = tail
    if m := re.search(r'^PROMPT \[(.*)\]$', text, re.M): out['prompt'] = m.group(1)
    if m := re.search(r'^CONFIG (.*)$', text, re.M): out['config'] = dict(re.findall(r'(\w+)=(\S+)', m.group(1)))
    if m := re.search(r'^DS4_ENV (.*)$', text, re.M): out['environment'] = dict(re.findall(r'(\w+)=(\S+)', m.group(1)))
    if m := re.search(r'^ENGINE (.*)$', text, re.M): out['engine_build'] = m.group(1)
    if m := _RE_DS4_SUMMARY.search(text):
        try: out['summary'] = json.loads(m.group(0))
        except ValueError: pass
    out['settings'] = _arm_settings(text)
    out['row'] = _parse_arm_cached(path)
    out['streaming'] = streaming_profile(text, engine_header(path))
    out['files'] = arm_artifacts(path)
    out['folder'] = os.path.dirname(path)
    out['log_path'] = path
    return json.dumps(out, separators=(",", ":")).encode()


# ------------------------------------------------------------- experts tab
_experts_cache: dict = {"key": None, "val": b"{}"}
REC_BYTES = 17_547_264  # one expert record


def experts_payload(trace: str) -> bytes:
    """Per-(layer, expert) route counts from a router trace.

    The trace is the engine's own `--router-trace` JSONL: one row per
    (step, layer) with the routed expert ids. Rows carry 16 ids per decoded
    position, so a row may hold a multiple of 16 when several positions were
    routed in one pass — every id is counted, because every one was a real
    route that had to be read or served.

    Only USED cells are sent. The full grid is 92 x 896 = 82,432 cells and the
    page fills the rest as grey; sending 52k zeroes would triple the payload to
    say nothing.
    """
    # Accepts one trace, or several comma-separated ones whose counts are
    # SUMMED. Consolidating runs answers the question a single run cannot: does
    # routing follow the prompt, or is there a stable set a cache could hold
    # across requests?
    names = [t.strip() for t in (trace or "").split(",") if t.strip()]
    names = [n for n in names if n.endswith((".jsonl", ".hotlist", ".expert.json"))]
    if not names:
        return json.dumps({"error": "trace must be one or more .jsonl / .hotlist / .expert.json names"}).encode()
    paths, stamps = [], []
    for name in names:
        path = _resolve_trace(name)
        try:
            stamps.append((name, os.path.getmtime(path)))
            paths.append(path)
        except (OSError, TypeError):
            return json.dumps({"error": f"no such trace: {name}", "cells": []}).encode()
    key = tuple(stamps)
    if _experts_cache["key"] == key:
        return _experts_cache["val"]

    counts: dict = {}
    # Bitmask of which traces touched each cell, so "in how many runs" is
    # answerable without shipping per-run grids.
    seen_in: dict = {}
    steps = 0
    max_layer = 0
    max_expert = 0
    rec_bytes = REC_BYTES
    ds4_dims = None
    for index, path in enumerate(paths):
        bit = 1 << index
        run_steps = set()
        if path.endswith((".hotlist", ".expert.json")):
            try:
                c2, nl, ne, st_ = _ds4_counts(path)
            except Exception as error:
                return json.dumps({"error": f"{os.path.basename(path)}: {error}", "cells": []}).encode()
            for k2, v in c2.items():
                counts[k2] = counts.get(k2, 0) + v
                seen_in[k2] = seen_in.get(k2, 0) | bit
                max_layer = max(max_layer, k2 >> 16); max_expert = max(max_expert, k2 & 0xFFFF)
            ds4_dims = (nl, ne); steps += st_
            # GLM-5.3 Q4_K routed expert ≈ 20.25 MiB (ds4 log line); K3 record = 17,547,264 B
            rec_bytes = 20.25 * 2**20
            continue
        try:
            with open(path, errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        layer = int(row["layer"])
                        ids = row["ids"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    run_steps.add(row.get("step", 0))
                    max_layer = max(max_layer, layer)
                    for raw in ids:
                        expert = int(raw)
                        max_expert = max(max_expert, expert)
                        key2 = (layer << 16) | expert
                        counts[key2] = counts.get(key2, 0) + 1
                        seen_in[key2] = seen_in.get(key2, 0) | bit
        except OSError as error:
            return json.dumps({"error": str(error), "cells": []}).encode()
        steps += len(run_steps)

    cells = [[k >> 16, k & 0xFFFF, v, bin(seen_in.get(k, 0)).count("1")]
             for k, v in counts.items()]
    # How many cells appear in exactly N of the runs. This is the pattern
    # question: if most reused experts appear in ALL runs, a cross-request
    # cache is viable; if each prompt routes somewhere different, it is not.
    shared = {}
    for c in cells:
        shared[c[3]] = shared.get(c[3], 0) + 1
    reused = sum(1 for c in cells if c[2] > 1)

    # Reuse pivot. `routes` is the decision-relevant column: a cache can only
    # ever serve a REPEAT, so a bucket's value is the routes it carries beyond
    # the first touch, not the number of experts in it. Experts routed exactly
    # once are pure cost — a miss, an admission, an eviction, never a hit.
    total_routes = sum(c[2] for c in cells)
    # 2026-09-02: individual bands 1x..20x instead of the coarse 2-5 /
    # 6-10 groups — the admission decisions live at 2x, 3x, 4x, and a
    # grouped band hid where the repeats actually sit. Tails stay grouped.
    edges = [(n, n) for n in range(1, 21)] + [(21, 50), (51, 1 << 30)]
    buckets = []
    for lo, hi in edges:
        sel = [c[2] for c in cells if lo <= c[2] <= hi]
        routes = sum(sel)
        repeats = sum(v - 1 for v in sel)
        buckets.append({
            "lo": lo,
            "hi": None if hi > 1 << 20 else hi,
            "experts": len(sel),
            "routes": routes,
            "repeats": repeats,
            # Traffic the band DEMANDED, and the part of it a cache could have
            # served (every route after the first). `per` is traffic per expert
            # — the density that decides which band earns its RAM.
            "traffic_gib": round(routes * rec_bytes / 2**30, 1),
            "cacheable_gib": round(repeats * rec_bytes / 2**30, 1),
            "per_expert_gib": round(routes * rec_bytes / 2**30 / len(sel), 3) if sel else 0,
        })
    # Cumulative "more than N" counts, which is how the question is usually asked.
    over = {}
    # Same granularity as the per-band rows: more than 1x .. more than 20x, then 50.
    for n in list(range(1, 21)) + [50]:
        sel = [c[2] for c in cells if c[2] > n]
        rts = sum(sel)
        rep = sum(v - 1 for v in sel)
        over[str(n)] = {
            "experts": len(sel),
            "routes": rts,
            "repeats": rep,
            "traffic_gib": round(rts * rec_bytes / 2**30, 1),
            "cacheable_gib": round(rep * rec_bytes / 2**30, 1),
            "per_expert_gib": round(rts * rec_bytes / 2**30 / len(sel), 3) if sel else 0,
        }
    if ds4_dims:
        max_layer = max(max_layer, ds4_dims[0]); max_expert = max(max_expert, ds4_dims[1] - 1)
    body = {
        "trace": ", ".join(names),
        "rec_bytes": rec_bytes,
        "layers": max_layer,
        "experts": max_expert + 1,
        "steps": steps,
        "grid": max_layer * (max_expert + 1),
        "used": len(cells),
        "reused": reused,
        "max_count": max((c[2] for c in cells), default=0),
        "total_routes": total_routes,
        "traces": names,
        "n_traces": len(names),
        "shared": shared,
        "buckets": buckets,
        "over": over,
        "cells": cells,
    }
    val = json.dumps(body, separators=(",", ":")).encode()
    _experts_cache.update(key=key, val=val)
    return val


def expertmap4_payload(traces: str, top: int) -> bytes:
    """Per-trace top-N expert cells for the 4-color overlay map.

    Unlike /experts (which SUMS traces), this keeps each trace separate so
    the page can color one test per hue and show cross-test repeats as
    blended cells. Trace basenames are searched in the project root and the
    local model root (where K3_ARM_TRACE files land).
    """
    out = []
    for name in [t.strip() for t in (traces or "").split(",") if t.strip()]:
        if not name.endswith((".jsonl", ".hotlist", ".expert.json")):
            continue
        path = _resolve_trace(name)
        if path is None:
            out.append({"name": name, "error": "not found", "cells": []})
            continue
        counts: dict = {}
        if path.endswith((".hotlist", ".expert.json")):
            try:
                c2, nl, ne, _ = _ds4_counts(path)
                counts = {(k >> 16, k & 0xFFFF): v for k, v in c2.items()}
            except Exception as error:
                out.append({"name": name, "error": str(error), "cells": []}); continue
            best = sorted(counts.items(), key=lambda kv: -kv[1])[:max(1, min(top, 500))]
            out.append({"name": name, "layers": nl, "experts": ne, "cells": [[l, e, c] for (l, e), c in best]})
            continue
        try:
            with open(path, errors="ignore") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    layer = r.get("layer")
                    for e in r.get("ids") or []:
                        k = (layer, e)
                        counts[k] = counts.get(k, 0) + 1
        except OSError as error:
            out.append({"name": name, "error": str(error), "cells": []})
            continue
        best = sorted(counts.items(), key=lambda kv: -kv[1])[:max(1, min(top, 500))]
        out.append({"name": name,
                    "cells": [[l, e, c] for (l, e), c in best]})
    return json.dumps({"tests": out}, separators=(",", ":")).encode()


def list_traces() -> list:
    import glob as _glob
    out = []
    try:
        out += sorted(f for f in os.listdir(ROOT) if f.endswith(".jsonl"))
    except OSError:
        pass
    # ds4 (GLM project): DS4_EXPERT_HOTLIST files (full layer x expert histogram) and
    # --expert-profile JSON (top 16 per layer) written next to each arm log.
    for pat in ("*/*.hotlist", "*/*.expert.json", "*/*.jsonl"):
        out += sorted(os.path.relpath(f, SOAK) for f in _glob.glob(os.path.join(SOAK, pat)))
    return out


def _resolve_trace(name: str):
    bases = [ROOT, SOAK, os.path.join(ROOT, "deltafin-root-local")]
    # An external trace root is opt-in so the source does not embed a
    # reference machine's volume name or mount point.
    external = os.environ.get("K3_EXTERNAL_TRACE_ROOT")
    if external:
        bases.append(external)
    for base in bases:
        cand = os.path.join(base, name)
        if os.path.exists(cand):
            return cand
    return None


def _ds4_counts(path: str) -> tuple:
    """(counts {(layer1based<<16|expert): n}, layers, experts, steps, rec_bytes) from a ds4
    hotlist ('layer expert hits weight' lines, 0-based layers) or expert-profile JSON."""
    counts, n_layer, n_expert, steps = {}, 0, 0, 0
    if path.endswith(".hotlist"):
        with open(path, errors="ignore") as fh:
            for line in fh:
                if line.startswith("#"):
                    if m := re.match(r"# layers (\d+)", line): n_layer = int(m.group(1))
                    elif m := re.match(r"# experts (\d+)", line): n_expert = int(m.group(1))
                    elif m := re.match(r"# layer_records (\d+)", line): steps = int(m.group(1)) // max(n_layer, 1)
                    continue
                parts = line.split()
                if len(parts) >= 3 and int(parts[2]) > 0:
                    counts[((int(parts[0]) + 1) << 16) | int(parts[1])] = int(parts[2])
    else:
        j = json.load(open(path, errors="ignore"))
        n_layer, n_expert = int(j.get("layers", 0)), int(j.get("experts", 0))
        steps = int(j.get("layer_records", 0)) // max(n_layer, 1)
        for L in j.get("layers_detail", j.get("layer_profiles", [])) or []:
            for e in L.get("top_experts", []):
                counts[((int(L["layer"]) + 1) << 16) | int(e["id"])] = int(e["count"])
    return counts, n_layer, n_expert, steps


# ------------------------------------------------------------- process tab
_procs_cache: dict = {"t": 0.0, "val": b"{}"}


def procs_payload() -> bytes:
    """Top processes by RSS, plus the engine's own footprint broken out.

    Process RSS and system Metal allocations are different, potentially
    overlapping views. Neither their sum nor a residual against system used
    memory establishes the engine's total footprint.

    Cached for 2 s: `ps -A` over ~700 processes is not free, and this page is
    meant to be open DURING runs.
    """
    now = time.monotonic()
    if now - _procs_cache["t"] < 2.0:
        return _procs_cache["val"]
    rows = []
    error = None
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,rss=,%cpu=,comm="],
                             capture_output=True, text=True, timeout=10, check=True).stdout
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            try:
                pid, rss, pcpu = int(parts[0]), int(parts[1]), float(parts[2])
            except ValueError:
                continue
            name = parts[3].rsplit("/", 1)[-1]
            rows.append({"pid": pid, "gib": round(rss * 1024 / 1073741824, 2),
                         "cpu": round(pcpu, 1), "name": name,
                         "full": parts[3][-110:]})
    except (OSError, subprocess.SubprocessError) as exc:
        error = str(exc)
    rows.sort(key=lambda r: r["gib"], reverse=True)
    top = rows[:10]
    with lock:
        mps = sysv.get("ram_mps", 0.0)
        used = sysv.get("ram", 0.0)
    eng = round(sum(r["gib"] for r in rows if r['name'] in ('deltafin','ds4')), 2) if not error else None
    body = {"top": top, "count": len(rows), "engine_rss": eng,
            "engine_mps": None, "system_metal": round(mps, 2), "ram_used": round(used, 1),
            # The overlapping measurements cannot establish a residual.
            "unattributed": None, "available": error is None, "error": error}
    val = json.dumps(body, separators=(",", ":")).encode()
    _procs_cache.update(t=now, val=val)
    return val


_ARM_SCORE_CACHE: dict[str, dict] = {}


def arm_score(marker: dict) -> dict | None:
    """Scorecard for the arm the marker describes, parsed once at state=done.

    steady excludes the first chunk (spine load + prefill); GB/token is the
    arm's OWN csv delta over generated tokens; text_md5 is the normalized
    generation (ARM banner stripped), the corruption detector of record.
    """
    log = marker.get("log") or ""
    key = f"{log}:{marker.get('start')}"
    if key in _ARM_SCORE_CACHE:
        return _ARM_SCORE_CACHE[key]
    try:
        with open(log, errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    stats = []
    counters: dict[str, str] = {}
    text_parts = []
    for line in lines:
        if line.startswith("[stats]"):
            f = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)
            try:
                stats.append((int(f["generated"]),
                              float(f["elapsed"].rstrip("s")),
                              float(f.get("speed", "0"))))
            except (KeyError, ValueError):
                pass
            for k in ("drafts", "layer_passes", "chunks"):
                if k in f:
                    counters[k] = f[k]
        elif line.startswith("[summer]"):
            f = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)
            for k in ("hits", "hit_rate", "suppressed", "wide_skips", "inserts"):
                if k in f:
                    counters[f"summer_{k}"] = f[k]
        elif not line.startswith("[") and not line.startswith("deltafin:") and line.strip():
            text_parts.append(line)
    if not stats:
        return None
    import hashlib
    text = "".join(text_parts).replace("\n", "")
    text = re.sub(r"^ARM [^]]*\]", "", text)
    score = {
        "arm": marker.get("arm"), "block": os.path.basename(marker.get("out") or ""),
        "rc": marker.get("rc"), "tokens": marker.get("tokens"),
        "overrides": marker.get("overrides"),
        "fused": stats[-1][2],
        "generated": stats[-1][0],
        "first_chunk_s": round(stats[0][1], 1),
        "text_md5": hashlib.md5(text.encode()).hexdigest()[:12],
        "counters": counters,
    }
    if len(stats) >= 2 and stats[-1][1] > stats[0][1]:
        score["steady"] = round(
            (stats[-1][0] - stats[0][0]) / (stats[-1][1] - stats[0][1]), 4)
    csv_path = log[:-4] + ".csv"
    try:
        mins: dict[str, int] = {}
        maxs: dict[str, int] = {}
        with open(csv_path, errors="ignore") as fh:
            fh.readline()
            for row in fh:
                parts = row.split(",")
                if len(parts) >= 3:
                    dev, v1 = parts[1], int(parts[2])
                    if dev not in mins:
                        mins[dev] = v1
                    maxs[dev] = v1
        total = sum(maxs[d] - mins[d] for d in mins)
        score["gb_total"] = round(total / 1e9, 1)
        if stats[-1][0]:
            score["gb_tok"] = round(total / 1e9 / stats[-1][0], 2)
    except (OSError, ValueError):
        pass
    _ARM_SCORE_CACHE[key] = score
    if len(_ARM_SCORE_CACHE) > 64:
        _ARM_SCORE_CACHE.pop(next(iter(_ARM_SCORE_CACHE)))
    return score


def live_progress():
    engines = health.get('engines', [])
    result = {'engines': engines, 'state': 'unknown' if health.get('engine_detection') != 'ok' else 'idle',
              'rate': None, 'unit': 'tok/s', 'source': 'unavailable'}
    if not engines:
        return result
    result['state'] = 'engine detected'
    if any(e['engine'] == 'deltafin' for e in engines) and phases['active']:
        result.update(rate=phases['tok10'] or None, source='deltafin stats', state=phases['meta'].get('state') or 'running')
    if any(e['engine'] in ('ds4', 'ds4-bench', 'ds4-server') for e in engines):
        import glob
        marker = _read_marker()
        paths = [os.path.splitext(marker['log'])[0]+'.chunks'] if marker and marker.get('log') else glob.glob(os.path.join(SOAK, '*', '*.chunks'))
        if paths:
            newest = max(paths, key=lambda p: os.path.getmtime(p) if os.path.isfile(p) else 0)
            # Only a marker can associate a log with a process; otherwise label it as recent telemetry.
            prog = chunk_progress(newest)
            if prog and time.time()-prog['last_wall'] < 15:
                result.update(prog, state='recent response telemetry', associated=bool(marker and marker.get('state') == 'running'))
        # The bounded DeepSeek benchmark has no response-chunk sidecar. Phase
        # markers still provide an authoritative live state while the engine
        # process is present; do not label that interval idle.
        if phases['active']:
            result.update(rate=phases['tok10'] or None, source='ds4 phase markers',
                          state=phases['meta'].get('phase', 'running'))
    return result


def payload() -> bytes:
    with lock:
        now = max((s[-1][0] for s in rates.values() if s), default=0.0)
        cut = now - WINDOW
        tr = {k: [[round(t, 2), v] for t, v in s if t >= cut] for k, s in rates.items()}
        # Guard every index: with drives unplugged (2-SSD era) a device's
        # series can be empty or shorter than the others; one short list must
        # never 500 the whole payload.
        total = [[t, v] for t, v in total_rates if t >= cut]
        fresh = time.time()-health['sample_at'] < 2 if health.get('sample_at') else False
        cur = {k: (s[-1][1] if fresh and s and time.time()-s[-1][0] < 2 else None) for k, s in tr.items() if k in DEVS.values()}
        total_now = total[-1][1] if fresh and total and time.time()-total[-1][0] < 2 else None
        mh = [m for m in memhist if m[0] >= cut]
        # ACTIVE rate x duty per device over the visible window — the
        # k3-table.py method. A window mean over idle measures the sampler,
        # not the engine (trap of record); these two numbers replace it.
        duty = {}
        for k, v in tr.items():
            floor = 0.05 * CAP[k] if CAP.get(k) else 0.5
            act = [x[1] for x in v if x[1] > floor]
            duty[k] = {
                "duty_pct": round(100 * len(act) / len(v)) if v else 0,
                "active": round(sum(act) / len(act), 2) if act else 0.0,
            }
        body = dict(traces=tr, total=total, cap=CAP, peaks=dict(peaks),
                    # Physical counters are device-wide. Until the engine's
                    # per-request class telemetry is enabled, the Engram
                    # monitor must show this explicitly rather than inventing
                    # a weights/Engram split.
                    streaming_attribution={
                        'status': 'unavailable',
                        'source': 'Physical SSD counters (device-wide)',
                        'note': 'The sampler cannot identify whether a read served a weight tensor or an Engram row.'},
                    cur=cur, cur_total=round(total_now, 2) if total_now is not None else None, sys=dict(sysv),
                    health=dict(health), memhist=mh, ram_total=RAM_TOTAL,
                    dist={}, staging={}, duty=duty,
                    summer={k: (list(v)[-240:] if k == "hist" else v)
                            for k, v in summer_live.items()},
                    phases={"active": phases["active"], "arm": phases["arm"],
                            "chunks": phases["chunks"], "per_tok": phases["per_tok"],
                            "spt": phases["spt"], "meta": dict(phases["meta"]),
                            "tok10": phases["tok10"], "tok_run": phases["tok_run"],
                            "spine_ram_gib": phases["spine_ram_gib"],
                            "spine_ssd_gib": phases["spine_ssd_gib"],
                            "spine_resident": phases["spine_resident"],
                            # Coarse run stage for the header badge: before the
                            # first chunk everything is spine-load + prefill
                            # (encode); chunks flowing = decode.
                            "stage": ("decode" if phases["active"] and phases["chunks"] > 0
                                      else "prefill/encode" if (phases["meta"].get("state") == "running")
                                      else "idle"),
                            "hist": [[t, d] for t, d in phases["hist"]][-240:],
                            "decode_start": phases["decode_start"],
                            "gb_tok": None})  # Needs bytes and tokens over the same interval.
        body['read_windows'] = {k:list(v) for k,v in read_windows.items()}
        body['read_operations'] = read_operations.summary(time.time()) if fresh else {}
        # Burst view: last 10 s, max-in-bucket to <=250 buckets per device —
        # peaks must survive decimation (mean-in-bucket is the exact mistake
        # the 10 ms spec exists to correct). Raw ticks ship when zoomed.
        bcut = now - 10.0
        burst_out = {}
        for k, ring in burst.items():
            pts = [(x[0], x[1]) for x in ring if x[0] >= bcut]
            if len(pts) < 2:
                burst_out[k] = []
                continue
            t0, span = pts[0][0], max(1e-9, pts[-1][0] - pts[0][0])
            buckets: dict[int, float] = {}
            for t, r in pts:
                i = min(249, int((t - t0) / span * 250))
                buckets[i] = max(buckets.get(i, 0.0), r)
            burst_out[k] = [[round(t0 + (i + 0.5) * span / 250, 3), round(v, 3)]
                            for i, v in sorted(buckets.items())]
        body["burst"] = burst_out
        body["jitter"] = {k: (round(100.0 * _jitter[k] / _jticks[k], 1)
                              if _jticks[k] else 0.0) for k in _jitter}
    marker = _read_marker()
    if marker and marker.get("state") == "done":
        score = arm_score(marker)
        if score:
            body["last_arm"] = score
    present = sorted(set(DEVS.values()))
    body["present"] = present
    body['absent'] = [d['id'] for d in DRIVE_INFO if not d['present']]
    body['devices'] = DRIVE_INFO
    body['version'] = VERSION
    body['mode'] = 'reports' if OPTIONS.reports_only else 'live'
    body['sample_ms'] = OPTIONS.sample_ms
    body['health']['scope'] = bool(body['health']['scope'] and fresh)
    body['health']['sample_age_s'] = round(time.time()-health['sample_at'], 1) if health.get('sample_at') else None
    body['individual_ceiling_sum'] = sum(CAP[k] for k in present) if present and all(k in CAP for k in present) else None
    # Per-drive calibrations do not establish simultaneous throughput. In
    # particular, two downstream SSDs can share the same hub uplink. Keep the
    # aggregate uncalibrated until a common-window calibration is recorded.
    body['cap_total'] = CAP.get(present[0]) if len(present) == 1 else None
    body['cap_total_note'] = 'A simultaneous multi-drive calibration is required; individual ceilings are not additive.' if len(present)>1 else 'Single physical drive, where configured.'
    body['runs_path'] = SOAK
    body['source_path'] = ROOT
    body['discovery_errors'] = DISCOVERY_ERRORS
    # Startup Spotlight policy check is read-only. Include the latest snapshot
    # in the common payload so every screen can warn before a benchmark, not
    # only the SSD settings page.
    body['spotlight'] = spotlight.snapshot()
    body['live_progress'] = live_progress()
    body['system_fresh'] = bool(health.get('system_at') and time.time()-health['system_at'] < 5)
    return json.dumps(body, separators=(",", ":")).encode()


def peakreader_payload() -> bytes:
    """Last 10 s of RAW 10 ms ticks per device — every tick, no decimation —
    plus the largest single-tick transfer ever seen (bytes). This is the
    'did a 20 MB burst happen?' view. 10 ms is the IOKit counter granularity:
    a tick's bytes are exact, its sub-10ms shape is not.
    """
    with lock:
        now = max((r[-1][0] for r in burst.values() if r), default=0.0)
        cut = now - 10.0
        devs = {}
        for k, ring in burst.items():
            ticks = [[x[0], (x[2] if len(x) > 2 else 0) / 1e6, x[1]]
                     for x in ring if x[0] >= cut]   # [t, MB, GB/s]
            mx = _peaktick.get(k, (0, 0.0, 0.0))
            wmax = max((t[1] for t in ticks), default=0.0)
            # The physically honest peak: bytes / ACTUAL dt = GB/s, bounded by
            # the drive. Raw MB is misleading when the sampler was descheduled
            # (a 48ms 'tick' shows ~5x the bytes of a real 10ms one).
            mx_dt = mx[2] if mx[2] > 0 else 10.0
            devs[k] = {"ticks": ticks,
                       "window_max_mb": round(wmax, 2),
                       "session_max_mb": round(mx[0] / 1e6, 2),
                       "session_max_gbps": round(mx[0] / 1e6 / mx_dt, 2),
                       "session_max_t": mx[1], "session_max_dt_ms": mx[2]}
        jit = {k: (round(100.0 * _jitter[k] / _jticks[k], 1) if _jticks[k] else 0.0)
               for k in _jitter}
    return json.dumps({"now": now, "devs": devs, "jitter": jit,
                       "tick_ms": OPTIONS.sample_ms}, separators=(",", ":")).encode()


def burst_dump() -> bytes:
    """Snapshot the burst ring to CSV next to the rung logs (spec §1.4)."""
    out_dir = os.path.join(SOAK,
                           time.strftime("%Y-%m-%d") + "-monitor")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, time.strftime("burst-%H%M%S.csv"))
    with lock:
        rows = [(x[0], k, x[1], x[2] if len(x) > 2 else 0)
                for k, ring in burst.items() for x in ring]
    rows.sort()
    with open(path, "w") as fh:
        fh.write("t_s,device,rate_gbps,bytes\n")
        for t, k, r, by in rows:
            fh.write(f"{t},{k},{r},{by}\n")
    return json.dumps({"saved": path, "rows": len(rows)}).encode()


PAGE_PATH = os.path.join(ROOT, "k3-live-page.html")


def page() -> bytes:
    """Re-read per request. The page was previously cached at import AND served
    with no cache headers, so a browser kept running stale JS against a changed
    /data schema -- missing keys silently rendered as 0.0 rather than erroring."""
    try:
        with open(PAGE_PATH, "rb") as fh:
            return fh.read()
    except OSError:
        return b"<h1>k3-live-page.html missing</h1>"


def source_info(path):
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError('Choose an existing folder containing run folders or .log files.')
    blocks = [p for p in folder.iterdir() if p.is_dir()]
    count = sum(1 for b in blocks for f in b.glob('*.log') if _is_arm_log(f.name))
    direct = sum(1 for f in folder.glob('*.log') if _is_arm_log(f.name))
    if direct and not count:
        raise ValueError('This is a single run folder. Choose its parent: ' + str(folder.parent))
    return {'runs': str(folder), 'logs': count, 'blocks': len(blocks)}


from test_runner import TestRunner, atomic
TEST_COMMAND = ([sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]) + ['--test-worker']
test_runner = TestRunner(STATE_DIR, TEST_COMMAND, inventory=lambda: DRIVE_INFO)
from ssd_tuner import SSDTuner
ssd_tuner = SSDTuner(STATE_DIR, TEST_COMMAND[:-1] + ['--ssd-tuner-worker'])

from engine_profiles import draft_shape
from model_support import model_catalog, model_identity, readiness as model_readiness


def settings_payload():
    return {'runs': SOAK, 'exists': os.path.isdir(SOAK), 'mode': 'reports' if OPTIONS.reports_only else 'live',
            'sample_ms': OPTIONS.sample_ms, 'token': SETTINGS_TOKEN, 'version': VERSION,
            'engine_draft': SAVED_SETTINGS.get('engine_draft'), 'persisted_runs': SAVED_SETTINGS.get('runs'), 'cli_override': bool(OPTIONS.runs or CONFIG.get('runs'))}


def apply_source(path, validate_only=False):
    global SOAK, SAVED_SETTINGS
    info = source_info(path)
    if not validate_only:
        saved = {**SAVED_SETTINGS, 'runs': info['runs']}
        LOCAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        atomic(LOCAL_SETTINGS, saved)
        SOAK = info['runs']
        SAVED_SETTINGS = saved
        _stats_cache.update(key=None, val=None)
        _arm_cache.clear()
        _BYLAYER_CACHE.clear()
        _experts_cache.update(key=None, val=b'{}')
    return {**info, 'applied': not validate_only}


def save_engine_draft(value):
    global SAVED_SETTINGS
    draft = draft_shape(value)
    saved = {**SAVED_SETTINGS, 'engine_draft': draft}
    LOCAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    atomic(LOCAL_SETTINGS, saved)
    SAVED_SETTINGS = saved
    return {'saved': True, 'engine_draft': draft, 'applied_to_engine': False}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence per-request logging
        pass

    def local_host(self):
        try:
            hostname = urllib.parse.urlsplit('http://' + self.headers.get('Host', '')).hostname
        except ValueError:
            hostname = None
        if hostname not in ('localhost', '127.0.0.1', '::1'):
            self.send_error(403, 'This is a localhost application.')
            return False
        return True

    def do_POST(self):
        if not self.local_host(): return
        expected = 'http://' + self.headers.get('Host', '')
        if self.headers.get('Origin') != expected or self.headers.get('X-Argodrive-Token') != SETTINGS_TOKEN:
            self.send_error(403, 'Use the local ARGODRIVE interface to change settings.')
            return
        if self.path not in ('/settings', '/spotlight', '/spotlight/scan', '/models/preflight', '/test-runner/start', '/test-runner/stop', '/ssd-tuner/start', '/ssd-tuner/stop'):
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192: raise ValueError('Invalid settings request size')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict): raise ValueError('A JSON object is required')
            if self.path == '/ssd-tuner/start':
                result = ssd_tuner.start(body)
            elif self.path == '/ssd-tuner/stop':
                if set(body) != {'id'}: raise ValueError('Specify the owned calibration ID.')
                result = ssd_tuner.stop(body['id'])
            elif self.path == '/test-runner/start':
                result = test_runner.start(body, SOAK)
            elif self.path == '/test-runner/stop':
                if set(body) != {'id'}: raise ValueError('Specify the owned test ID.')
                result = test_runner.stop(body['id'])
            elif self.path == '/models/preflight':
                result = model_readiness(body)
            elif self.path == '/spotlight/scan':
                result = spotlight.scan()
            elif self.path == '/spotlight':
                result = spotlight.change(body.get('id'), body.get('enabled'))
            elif 'engine_draft' in body:
                if set(body) != {'engine_draft'}: raise ValueError('Save the engine draft separately from other settings')
                with SOURCE_LOCK:
                    result = save_engine_draft(body['engine_draft'])
            else:
                if not isinstance(body.get('runs'), str): raise ValueError('A folder path is required')
                with SOURCE_LOCK:
                    result = apply_source(body['runs'], body.get('validate_only') is True)
            status = 200
        except (ValueError, OSError) as exc:
            result, status = {'error': str(exc)}, 400
        b = json.dumps(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self.local_host(): return
        # Inventory can take seconds. It must not block live /data responses.
        if urllib.parse.urlparse(self.path).path == '/topology':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            b = json.dumps(topology_inventory.snapshot(list(DRIVE_INFO), OPTIONS.reports_only,
                           force=(q.get('refresh') == ['1']), config=CONFIG)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        # Trace parsers retain a one-file cache. Serialize report/source operations.
        with SOURCE_LOCK:
            self.get_response()

    def get_response(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ('/app.css', '/app.js', '/app-model.js', '/topology-view.js', '/cluster-view.js', '/spotlight-view.js', '/engine-settings.js', '/campaign-view.js', '/model-support.js', '/ssd-tuner-view.js', '/benchmark-view.js', '/engram-monitor-view.js'):
            b = Path(ROOT, p[1:]).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/css' if p.endswith('.css') else 'text/javascript')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/ssd-tuner':
            b = json.dumps(ssd_tuner.status(), allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/spotlight':
            b = json.dumps(spotlight.snapshot()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/test-runner':
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            test_payload = {'run': test_runner.status()}
            if query.get('options') == ['1']: test_payload['options'] = test_runner.options()
            b = json.dumps(test_payload, allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/models':
            b = json.dumps(model_catalog()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/campaigns':
            b = json.dumps(campaign_monitor.snapshot(SOAK), allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/harness':
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            b = json.dumps(harness_monitor.snapshot(SOAK, (query.get('arm') or [''])[0]), allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/cluster':
            b = json.dumps(cluster_snapshot(STATE_DIR, ROOT)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == '/settings':
            b = json.dumps(settings_payload()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
        elif p == "/data":
            b = payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/peakreader":
            b = peakreader_payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p in ('/burstdump', '/reset'):
            self.send_error(405, 'Legacy mutation endpoints are not available in the product interface.')
            return
        elif p == "/bylayer.csv":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            tr = (q.get("trace") or [""])[0]
            b = bylayer_csv_payload(tr)
            stem = os.path.splitext(os.path.basename(tr))[0] if tr else "trace"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", f'attachment; filename="k3-bylayer_{stem}.csv"')
            self.send_header("Cache-Control", "no-store")
        elif p == "/bylayer_list":
            b = bylayer_list_payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/bylayer":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            tr = (q.get("trace") or [""])[0]
            try:
                bar = int((q.get("barrier") or ["-1"])[0])
            except ValueError:
                bar = -1
            b = bylayer_payload(tr, bar)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/scheduler":
            b = scheduler_payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/arm":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            b = arm_detail((q.get("block") or [""])[0], (q.get("arm") or [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/experts":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            b = experts_payload((q.get("trace") or [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/expertmap4":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                top = int((q.get("top") or ["30"])[0])
            except ValueError:
                top = 30
            b = expertmap4_payload((q.get("traces") or [""])[0], top)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/traces":
            b = json.dumps({"traces": list_traces()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/procs":
            b = procs_payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p == "/stats":
            b = stats_payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif p in ("/stats.csv", "/runs-export.csv"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query, keep_blank_values=True)
            frm = (q.get("from") or [""])[0]
            to = (q.get("to") or [""])[0]
            blk = (q.get("block") or [""])[0]
            if p == '/runs-export.csv':
                # Separate endpoint: an older running backend must refuse a
                # recent export, never silently download its entire library.
                try:
                    if set(q) - {'hours', 'last'} or len(q) != 1 or any(len(v) != 1 for v in q.values()):
                        raise ValueError('Choose one export range.')
                    hours = int(q['hours'][0]) if 'hours' in q else None
                    last_runs = int(q['last'][0]) if 'last' in q else None
                    now = time.time()
                    b = stats_csv(hours=hours, last_runs=last_runs, now=now)
                except ValueError as exc:
                    self.send_error(400, str(exc))
                    return
                scope = f'last-{hours}-hours' if hours is not None else f'last-{last_runs}-runs'
                span = scope + time.strftime('_exported-%Y-%m-%d_%H-%M-%S', time.localtime(now))
            else:
                b = stats_csv(frm, to, blk)
                span = f"{frm or 'all'}_to_{to or 'all'}" + (f"_{blk}" if blk else "")
            # The range goes in the FILENAME, not just the query string. A folder
            # of k3-arms.csv files with no way to tell which range each covers is
            # how two exports get compared as if they were the same population.
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="k3-arms_{span}.csv"')
            self.send_header("Cache-Control", "no-store")
        elif p == "/scheduler.csv":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            frm = (q.get("from") or [""])[0]
            to = (q.get("to") or [""])[0]
            blk = (q.get("block") or [""])[0]
            b = scheduler_csv(frm, to, blk)
            span = f"{frm or 'all'}_to_{to or 'all'}" + (f"_{blk}" if blk else "")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="k3-scheduler_{span}.csv"')
            self.send_header("Cache-Control", "no-store")
        elif p == "/live.csv":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            frm = (q.get("from") or [""])[0]
            to = (q.get("to") or [""])[0]
            blk = (q.get("block") or [""])[0]
            b = live_csv(frm, to, blk)
            span = f"{frm or 'all'}_to_{to or 'all'}" + (f"_{blk}" if blk else "")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="k3-telemetry_{span}.csv"')
            self.send_header("Cache-Control", "no-store")
        elif p == "/":
            b = page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
        else:
            self.send_error(404)
            return
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def watch_parent(pid):
    # A killed native window must not leave a sampler running indefinitely.
    while True:
        time.sleep(1)
        if os.getppid() != pid:
            _shutdown_children()
            os._exit(0)


if __name__ == "__main__":
    if OPTIONS.parent_pid:
        if os.getppid() != OPTIONS.parent_pid:
            raise SystemExit('The launching app is no longer running.')
        threading.Thread(target=watch_parent, args=(OPTIONS.parent_pid,), daemon=True).start()
    if OPTIONS.doctor:
        print(json.dumps({'version': VERSION, 'mode': 'reports' if OPTIONS.reports_only else 'live',
                          'devices': DRIVE_INFO, 'warnings': DISCOVERY_ERRORS,
                          'sampler_built': os.path.isfile(os.path.join(ROOT, 'k3-diskscope')),
                          'runs': SOAK, 'runs_exists': os.path.isdir(SOAK)}, indent=2))
        raise SystemExit(0)
    if len(DEVS) > 8:
        raise SystemExit('The current sampler supports eight disks. Select up to eight physical disks in --config.')
    if not OPTIONS.reports_only and not os.path.isfile(os.path.join(ROOT, 'k3-diskscope')):
        raise SystemExit('Sampler missing. Run ./argodrive build, or use --reports-only.')
    try:
        server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H)
    except OSError as exc:
        raise SystemExit(f'Cannot listen on port {PORT}: {exc}. Try --port with another number.')
    PORT = server.server_address[1]
    if OPTIONS.ready_file:
        ready = Path(OPTIONS.ready_file)
        ready.parent.mkdir(parents=True, exist_ok=True)
        with ready.open('x') as marker:
            marker.write(json.dumps({'port': PORT, 'pid': os.getpid(), 'version': VERSION}))
    if not OPTIONS.reports_only:
        if DEVS:
            threading.Thread(target=scope_reader, daemon=True).start()
        threading.Thread(target=sys_reader, daemon=True).start()
        threading.Thread(target=phases_reader, daemon=True).start()
    # Check indexing policy once for every Live Hardware start. This does not
    # change Spotlight and runs in the background so the live page opens fast.
    # Reports Only remains fully passive: no hardware or policy probes.
    if not OPTIONS.reports_only:
        threading.Thread(target=spotlight.scan, name='spotlight-startup-check', daemon=True).start()
    print(f"ARGODRIVE {VERSION} -> http://localhost:{PORT} ({'reports only' if OPTIONS.reports_only else 'live'}; Ctrl-C stops)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        _shutdown_children()
