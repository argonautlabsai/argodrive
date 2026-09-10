# The ARGODRIVE SSD dashboard — original feature inventory

This inventory predates the development preview. For current setup, metric
definitions and qualification limits, use [BETA-DEVELOPMENT.md](BETA-DEVELOPMENT.md).

`monitor/k3-live.py` + `monitor/k3-live-page.html`. A local web page (port 8130) that
shows what the drives, memory and engine are doing while an SSD-streamed
mixture-of-experts model decodes, and that turns the per-arm files written by
the measurement harness into tables, attributions and exports. It is an
instrument, not a controller: it never changes the engine.

Start: `python3 k3-live.py` from `monitor/`, open http://localhost:8130/.
Stop: kill its pid (`kill $(pgrep -f k3-live.py)`); it spawns and re-spawns its
own sampler and must be stopped first or samplers accumulate.

## Data it consumes

| source | produced by | used for |
|---|---|---|
| `k3-diskscope` CSV — per-device read bytes and ops, RAM, CPU, GPU, at 100–200 ms | spawned by the dashboard (live) and by the harness per arm | live bars, peak reader, per-arm SSD columns |
| arm logs `<tag>.log` | the harness (deltafin `[stats]` lines, or ds4 header + CONFIG + JSON summary) | Stats, Compare, arm detail |
| `<tag>.sys` — one line per second of memory truth | `k3-memsample.sh` per arm | RAM used / available / free / swap columns |
| `<tag>.map` — device map at arm start | harness | labels disk numbers with drive names per arm |
| router traces `*.jsonl` (deltafin) / `*.hotlist`, `*.expert.json` (ds4) | engine, on trace arms | Expert map |
| read traces `readtrace*.csv` (deltafin K3_READ_TRACE) / `*.readtrace.csv` (ds4 patch) | engine, on trace arms | by Layer |
| `[mirror-split]` scheduler lines | deltafin | Scheduler tab |
| staging / weights JSON | placement scripts | Storage + Memory weights panel |

Drive discovery is by volume name (internal, Green, White, Yellow) mapped to
BSD device numbers at startup, with each drive's standalone read ceiling in a
table (`CAP`) used for duty %. Absent drives are reported as absent, not idle.

## Tabs

**Overview** (landing tab) — the idle state, and the state between arms. One
line says whether an inference run is active and whether the sampler is alive.
"Last completed run": model, arm, block, completion time, steady decode tok/s,
first-token time, output length, delta against the block's same-length
baseline, average SSD draw during that run, and the configuration string,
taken from the Stats pivot (refreshed every 10 s; never from live readings).
"Connected storage": the four drives with their port and ceiling, shown as
idle, active (GB/s and duty), or not mounted. "System": RAM available, GPU
utilisation, GPU memory labelled as engine or system allocation depending on
whether an engine process exists, swap in use with its change since the page
opened, and the peak total draw against the capacity of the mounted drives
only. Buttons jump to the last run in Stats, to Compare, and to the live
charts. Historical results and live system readings are never mixed in one
number.

**Live** — per-drive read throughput as 200 ms bars over a rolling window, one
card per drive in a two-column grid: current GB/s, duty % against the drive's
ceiling, peak, utilisation, an active-share × duty figure, and the ceiling line.
Side tiles: total draw now and its share of combined capability, peak this
session, CPU, GPU utilisation and memory, RAM used (engine vs rest), GB per
token, tok/s over the last 10 s, RAM available. Controls: reset peaks, include or
exclude the spine-load phase, show a total row, shared or per-device scale.
Below: per-second CSV export for a date range or block, and the storage /
compute / burst panels.

**Stats** — the pivot of every arm of every block, newest block first: model,
inclusive and steady tok/s, first-token time, encode share, seconds per token,
elapsed, delta against the block's same-length baseline, generated / chunks /
drafts / acceptance / layer passes, RAM peak / available / free minimums, GPU
memory and utilisation, and per-drive active draw, peak and duty from the arm's
own sampler. Incomplete arms (killed, short, non-zero exit) are flagged and never
used as a baseline. Click an arm for its detail. CSV export of the whole pivot
with the per-arm settings families kept separate (requested / resolved /
declared), which is how "a knob that was passed but never took effect" shows up.

**Compare** — side-by-side arms of one block with their deltas.

**Storage + Memory** — one shared time axis for drive draw and memory: used,
available, wired, swap, with the engine's declared memory facts kept separate
from the sampled ones; the placement "weights distribution" panel (which drive
serves each expert, deltafin layouts) and the staging panel during a re-layout.

**Processes** — top processes by RSS, refreshed every 2 s, with the engine's
own footprint broken out and the caveat that RSS excludes Metal-wired memory.

**Expert map** — a layer × expert grid of routing counts from one or several
traces (summed), with a reuse threshold, bands of how many experts were routed
N times and how much traffic each band carried, cross-run overlap, and a
four-trace overlay that colours each run to show shared cells. Grid size and
expert record size come from the trace (K3 92 × 896 at 17.5 MB; GLM 75 × 256 at
20.25 MiB).

**Scheduler** — for deltafin's mirror scheduler: charged vs measured share per
drive for every arm that ran it. Deltafin-only.

**Peak reader** — the raw 10 ms sampler ticks over a 10 s window with no
decimation, so a single-tick burst is never averaged away.

**by Layer** — from a per-read trace: every barrier (one layer pass), which drive
landed last, the extra wait it caused (p50 / p90 / total), mean concurrent
reads per drive, a Gantt of one barrier's reads, and a strip of every barrier
of the run coloured by the last-landing drive. Downloadable as CSV. This is the
attribution that decides placement weights.

## Endpoints

`/data` (live payload), `/peakreader`, `/burstdump`, `/stats`, `/stats.csv`,
`/arm?block=&arm=`, `/live.csv`, `/traces`, `/experts?trace=`,
`/expertmap4?traces=&top=`, `/bylayer_list`, `/bylayer?trace=&barrier=`,
`/bylayer.csv?trace=`, `/scheduler`, `/scheduler.csv`, `/procs`, `/reset`.

## Engine support

| | deltafin (Kimi K3) | ds4 (GLM-5.3, DeepSeek V4) |
|---|---|---|
| Live, Peak reader, Processes, Storage + Memory (memory) | yes | yes |
| Stats, Compare | yes | yes (ds4 header, CONFIG, JSON summary parser) |
| Expert map | router trace `.jsonl` | `.hotlist` / `.expert.json` from `--expert-profile` |
| by Layer | `K3_READ_TRACE` csv | `.readtrace.csv` via the read-trace patch (`DS4_STREAM_READ_TRACE`) |
| Scheduler, weights distribution | yes | not applicable |

## Cost and discipline

The live sampler plus an open browser tab cost about one percent of decode
speed on the reference machine; headline arms are run with the dashboard
closed. Trace arms (router trace, read trace) are separate from speed arms.
Every number the dashboard shows is derived from files that stay on disk next
to the arm log, so any table can be regenerated without the dashboard.
