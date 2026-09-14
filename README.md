# ArgoDrive tools

Measurement instruments for SSD-streamed mixture-of-experts inference. These
are the tools that found every gain in the ArgoDrive Deltafin benchmark
package; they are shared as they are — deltafin-specific, rough, and honest —
because the results are not credible without them. Paths, drive names and
role assignments are the ones used on the reference machine (a MacBook Pro
M5 Max with three Thunderbolt 5 NVMe enclosures); adapt `K3_DIR` and the
volume names to yours.

## What they show

Per-drive read throughput during one 200-token completion, all four drives
sampled together at 100 ms by `k3-diskscope`: first as a replay of the sampler's
record (the bars move as the drives did, at 8× speed), then reduced to one-second windows:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/argonautlabsai/argodrive/main/charts/drives-live-dark.svg">
  <img src="https://raw.githubusercontent.com/argonautlabsai/argodrive/main/charts/drives-live.svg" alt="Animated replay of the four drives' read throughput during the 200-token record arm, from the sampler">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/argonautlabsai/argodrive/main/charts/read-timeline-dark.svg">
  <img src="https://raw.githubusercontent.com/argonautlabsai/argodrive/main/charts/read-timeline.svg" alt="Per-drive read throughput during a 200-token run">
</picture>

The same run reduced to median and peak draw per drive, against each drive's
standalone ceiling measured with `k3-drive-ceiling.py` while the engine was
idle — every drive at 88–100% of its own ceiling, which is why the read barrier
rather than total bandwidth sets the decode speed:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/argonautlabsai/argodrive/main/charts/drive-draw-dark.svg">
  <img src="https://raw.githubusercontent.com/argonautlabsai/argodrive/main/charts/drive-draw.svg" alt="Per-drive draw under the engine vs standalone ceiling">
</picture>

Both charts are produced from the sampler CSV and the ceiling tool's output by
the chart script in the benchmark package
([`argonautlabsai/deltafin`](https://github.com/argonautlabsai/deltafin/tree/main/k3-public-bench/results/charts)).

| directory | tool | what it does |
|---|---|---|
| `monitor/` | `k3-diskscope.c` | 100 ms sampler of per-device read bytes and ops, RAM, CPU and GPU counters, to CSV. `cc -O2 -o k3-diskscope k3-diskscope.c -framework IOKit -framework CoreFoundation` |
| `monitor/` | `k3-live.py` | local web dashboard on port 8130: live per-drive throughput, arm history with same-length deltas, by-layer barrier report from a per-read trace, CSV exports |
| `monitor/` | `k3-memsample.sh` | one line per second of macOS memory truth (used / available / wired / swap) |
| `harness/` | `k3-arm.sh`, `k3-arm-inner.sh`, `k3-measure.sh` | one measured arm: the configuration of record as environment, cold start enforced, swap guard, device map recorded, sampler and memory log per arm, text-identity check |
| `harness/` | `k3-pressure.c` | memory-pressure step before an arm (records what preceded each measurement) |
| `harness/` | `k3-stdbench-table.py` | inclusive / steady / first-token table from arm logs; derived prompt-processing rate |
| `trace/` | `k3-trace-gaps.py` | per-barrier attribution from the engine's read trace: which device landed last, its gap behind the next-to-last, by demand-vs-prefetch, barrier width and layer |
| `drives/` | `k3-drive-ceiling.py`, `k3-ceiling-run.sh` | standalone read ceiling of an expert directory (whole files, no page cache, N in flight), per drive and per queue depth |
| `drives/` | `k3-drive-map.py`, `k3-drive-names.example.json` | which physical drive is which — model, serial, whole-disk, bus, direct port or hub — and drift against the last snapshot; run after any replug |
| `placement/` | `k3-regen-manifests.py` | regenerate and verify placement manifests from the live directories |
| `placement/` | `k3-stage-wider-bands.py` | widen replica bands by traffic share from a usage trace, with a manifest and rollback script per band |

## How they were used

Every published number came from one `k3-arm.sh` invocation: it exports the
configuration of record, refuses to run warm or under swap, records the device
map, starts the sampler and the memory log, runs the engine once, and checks
the output against the text of record. Arms were run one at a time with the
dashboard server stopped (its sampler costs about one percent). Read traces
(`K3_READ_TRACE=<file>`) were taken on separate arms and analysed with
`k3-trace-gaps.py` and the dashboard's by-layer view, never used for speed
figures. Drive ceilings were measured with the engine idle.

## What these tools are not

They are not a product and not general-purpose: the sampler and the harness
assume macOS, the deltafin engine's log format and its `K3_*` environment
knobs. Nothing here schedules reads or changes the engine; these are
instruments only.

## Development acknowledgements

Claude, ChatGPT and OpenAI Codex assisted with development and review. Their use is acknowledged here; private chat histories are not part of this repository. Performance and correctness claims are supported by the stated tests and measurements, with limitations recorded separately.

## Licence

MIT.
