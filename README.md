# ARGODRIVE

**An expert-streaming optimiser for local AI.** ARGODRIVE's product goal is to
test your SSDs and find effective streaming settings for your model and workload,
then show the measured improvement in response time and generation speed.

The V4.1 engine integration lives in the
[Argonaut Argodrive DS4 fork](https://github.com/argonautlabsai/ds4/tree/argonaut-v41-benchmark).

The current Mac technical preview provides Overview, Live monitor, Runs, Compare
and Diagnostics, plus dedicated SSD charts, saved Streaming configurations, persistent data-source settings and light/dark themes.
Drive tests, benchmark harnesses and expert-placement tools also exist in the
source repository as separate research scripts. The DeepSeek V4.1 placement
workflow now parses router traces, maps routed experts to GGUF spans, and emits
rate-aware manifests without changing model files. The guided test → tune → validate
→ save-settings workflow is the next product milestone; it is not yet integrated
into the downloadable app. See the [streaming optimiser plan](docs/STREAMING-OPTIMIZER.md).

```sh
# Review saved runs without starting a hardware sampler
./argodrive run --reports-only --runs /path/to/arms
```

Open http://localhost:8130. Use Settings to validate or change the run folder.
See the [dashboard guide](docs/DASHBOARD.md) for the product workflow and metric
definitions, or [development setup](docs/BETA-DEVELOPMENT.md) for live collection.
Python 3.10+ is required; the UI has no runtime package dependencies. A self-contained Apple-silicon Mac technical preview can now be built with
[scripts/build-macos.py](scripts/build-macos.py). The initial build is ad-hoc signed
and not notarized; see [Mac beta release instructions](docs/MAC-BETA-RELEASE.md).

## Model support in the local Beta 3 build

| Model family | Engine | Current scope |
|---|---|---|
| Kimi K3 | Deltafin | Recorded-run analysis and streaming evidence |
| GLM 5.3 | Argonaut ds4 fork | Recorded runs, candidate profiles and qualified campaign evidence |
| DeepSeek V4.1 Flash | Argodrive ds4 fork · Metal | Experimental three-drive profile and recorded qualification evidence |

The current V4.1 evidence is a single-machine qualification: 15.47 tok/s median
over three pp512/tg512 repetitions on an M5 Max with the internal SSD plus two
verified replicas. It is not a general speed guarantee, and the packaged app
does not launch inference automatically. The GLM fork's replica settings are
not available in the fresh upstream engine. See [V4.1 preparation and test
procedure](docs/DEEPSEEK41.md).

## Product direction

First, turn SSD calibration and model-specific streaming experiments into a
guided workflow on one Mac. Compare supported read methods, per-drive work
allocation, reader concurrency, prefetch and expert-cache budgets using real
inference runs. The dashboard is the interface for this workflow and its evidence.

Then extend the same model to multiple Macs, local and remote expert RAM caches,
and SSDs. Cluster management, RDMA and peer-cache execution are planned
capabilities, not implemented features.

See [the product direction and staged architecture](docs/PRODUCT-DIRECTION.md).

## Original research instruments

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
| `monitor/` / `scripts/` | `ds41_placement.py` / `ds41-placement.py` | parse DeepSeek router traces, map GGUF expert spans, generate Deltafin-style usage-weighted manifests, and qualify matched A/B records |
| `scripts/` | `kimi-deltafin-profile.py` | print the Deltafin Kimi replica/ETA candidate and verify path filesystem identities without changing model data |

## How they were used

Every published number came from one `k3-arm.sh` invocation: it exports the
configuration of record, refuses to run warm or under swap, records the device
map, starts the sampler and the memory log, runs the engine once, and checks
the output against the text of record. Arms were run one at a time with the
dashboard server stopped (its sampler costs about one percent). Read traces
(`K3_READ_TRACE=<file>`) were taken on separate arms and analysed with
`k3-trace-gaps.py` and the dashboard's by-layer view, never used for speed
figures. Drive ceilings were measured with the engine idle.

## Current implementation boundary

The local **0.2.0-beta.3** build adds an experimental Cluster tab with saved M1
qualification evidence, plus native one-link expert transfer tools. A real
Thunderbolt run verified 1,000 requests against a 200-record K3 sample at
1.30 GB/s of transfer time. This is a transport result, not a decode speedup.
Multipath, remote RAM caching, RDMA and ds4 integration remain unimplemented.
See [wire/README.md](wire/README.md) and [wire/BENCH.md](wire/BENCH.md).

The packaged app monitors and compares measurements. The separate research
scripts can test reads, run configured benchmark arms and prepare expert replicas;
many still assume the reference machine's paths and deltafin's `K3_*` settings.
They need adaptation before use on another setup.

The engine performs the actual expert reads and scheduling. The generated
DeepSeek manifests are advisory until the ds4 fork explicitly consumes them;
the current three-drive runner still uses its explicit split-reader settings.
ARGODRIVE's next step is to select, test and export settings through versioned engine adapters.
An integrated automatic tuner and runtime adaptation to changing load are not
implemented yet. A standalone SSD result alone does not establish the fastest
inference configuration.

## Development acknowledgements

Claude, ChatGPT and OpenAI Codex assisted with development and review. Their use is acknowledged here; private chat histories are not part of this repository. Performance and correctness claims are supported by the stated tests and measurements, with limitations recorded separately.

## Licence

MIT.
