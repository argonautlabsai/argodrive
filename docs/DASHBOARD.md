# ARGODRIVE product workspace

ARGODRIVE's core product workflow is SSD testing and model-specific expert-streaming
optimisation. The current interface supplies measurement and comparison for that
workflow, with dedicated views for live hardware, recorded settings and results.
It runs on localhost, either in a browser or in the new native Mac wrapper.
The initial downloadable build is an ad-hoc-signed technical preview, not a
Developer ID–signed/notarized release. See [MAC-BETA-RELEASE.md](MAC-BETA-RELEASE.md).

Next, integrate **Test drives → Tune for a model → Validate → Save settings**;
see [STREAMING-OPTIMIZER.md](STREAMING-OPTIMIZER.md) for the source tools and
remaining work. The longer-term direction is distributed inference across Macs,
RAM and SSDs; see
[PRODUCT-DIRECTION.md](PRODUCT-DIRECTION.md) for the planned cluster, model and
expert-placement controls. The view list below describes implemented features.

## Product navigation

| View | Purpose | What is available |
| --- | --- | --- |
| Overview | Understand the latest recorded result | Latest complete run, steady/inclusive speed, first response, recorded bytes per token, recent results and evidence coverage |
| Benchmark | Find a safe streaming profile | Guided read-only SSD calibration, shared-link checks, candidate reader/weight settings, and an explicit model-verification handoff; token speed stays unmeasured until verified |
| Monitor | Understand current or recorded activity | Source selector for live hardware, the latest test or a past test; two optional chart checkboxes for SSD throughput and Monitor Engram; when class telemetry exists, the same per-drive bars overlay teal weights and purple Engram traffic; per-drive average/peak bars, response telemetry, memory context and collector health |
| Run monitor | Follow an existing GLM harness | Auto-follow arm files, host SSD bars with average/peak, response chunks, final engine timers, memory samples and completed M1 traffic/cache totals; no extra sampler |
| SSDs / Monitor Engram | Backward-compatible links into Monitor layers | `#ssds` and `#engram` open Monitor and select the corresponding checkbox. Classified colours require engine per-request attribution; otherwise device bars remain explicitly unclassified |
| Topology | See physical connections | Mac, port, hub and enclosure icons; hardware identity mapping, link speeds, live reads, IOPS, read time, device inspector and SVG export |
| Streaming | Inspect the method used by an arm | Requested versus engine-confirmed read settings, replicas and weights, cache policy, source files and an exportable manifest |
| Runs | Find and inspect an experiment | Search, engine/length/status filters, pagination, run details, prompt, configuration and CSV export of all runs, the last 1/3/6/24 hours, or the latest 1/5/10/20 runs |
| Compare | Evaluate one explicit baseline/candidate pair | Workload match checks, speed and latency deltas, configuration differences, output fingerprint checks and a Markdown export |
| Diagnostics | Investigate the recorded mechanism | Read-tail attribution, layer-pass timelines, trace export, expert routes, on-demand processes and raw sampler intervals |
| Settings | Connect data and personalise the workspace | Validate/apply a local run folder, persistent source selection, light/dark/system theme, collection instructions |

In SSDs, **Spotlight indexing → Check drives** reads the current indexing policy
for mounted volumes, including new enclosures. This explicit check also works in
reports mode without starting a sampler. Changes require a confirmed volume UUID,
an explicit action and macOS administrator authentication, followed by a status
read-back. See [Spotlight controls](SPOTLIGHT.md) for scope and verification.

In Runs, **Export range** applies to the entire selected run folder, independently
of table filters or pagination. Recent exports use each run's recorded `ran` time
(harness completion, otherwise file modification time) and retain incomplete runs
with their status. Hour windows end at the server's current time and include both
boundaries; naive timestamps use that Mac's timezone. Count exports sort across
all run folders, newest first, with block/arm names breaking timestamp ties. Rows
without a readable timestamp are excluded from recent exports and retained in
**All runs**. Every export keeps the full CSV settings columns; filenames identify
the range and, for recent exports, the export time. An empty recent window shows
a message instead of downloading an empty file. Restart an older backend before
using recent exports; it cannot silently substitute an all-runs download.

Each Monitor drive card includes a read-throughput bar history on a fixed 0–16
GB/s axis. This keeps SSDs directly comparable when one drive is idle or has a
higher calibrated ceiling. The combined aggregate chart uses its own scale
because it represents multiple drives; calibrated ceilings remain shown as
metadata rather than changing the per-drive chart axis.

For a benchmark already running in another terminal, use **Run monitor** and
select its parent results folder in Settings. This also works in reports-only
mode. See [HARNESS-MONITOR.md](HARNESS-MONITOR.md) for the GLM session handoff,
file format and remote-metric limitations.

The SSDs view excludes disconnected devices and can hide drives without recent
reads. Its averages include measured idle intervals and use actual durations.
Window peaks exclude delayed intervals over 350 ms. Unknown ceilings remain
uncalibrated. The Streaming view describes a **saved arm**, not an inferred live
configuration: replica split reads and dual-home placement are separate from
filesystem RAID0. A descriptor count does not establish a physical-drive count.
See [FEATURE-AUDIT-2026-09-10.md](FEATURE-AUDIT-2026-09-10.md) for retained
capabilities and useful features still requiring product integration.

The old Processes, Scheduler, Peak reader, Storage + Memory and Expert map tabs
are no longer separate top-level destinations. Useful memory and device context
lives in Live; read traces and expert activity live in Diagnostics. Legacy
placement scripts, scheduler analysis and standalone research tools remain in the
repository. They will feed a guided Optimise streaming workflow once its runner
and engine adapter are implemented. Removing the old tab layout does not remove
SSD testing, streaming selection or scheduling from the product scope.
The historical inventory is preserved in
[archive/DASHBOARD-RESEARCH-INVENTORY.md](archive/DASHBOARD-RESEARCH-INVENTORY.md).

## Start

```sh
./argodrive run --reports-only --runs /path/to/arms
```

Open http://localhost:8130. In reports-only mode, no hardware sampler or inference
engine is started. Read [BETA-DEVELOPMENT.md](BETA-DEVELOPMENT.md) for live setup.

The run folder contains one level of subfolders:

```text
arms/
  experiment-2026-09-10/
    baseline.log
    baseline.err            optional engine declarations and read-mode evidence
    baseline.csv            optional drive/system counters
    baseline.map            optional device identities for those counters
    baseline.sys            optional memory sampler
    baseline.md5            optional output fingerprint
    baseline.readtrace.csv  optional read timing
    baseline.hotlist        optional expert profile
    candidate.log
```

`.expert.json` and router `.jsonl` profiles are also supported. The data source
setting reads files in place; it does not import, relocate or modify them. It is
saved to ignored `monitor/argodrive.local.json`. A CLI `--runs` or config-file
source takes priority again on restart. Settings explains this precedence.

## Measurement semantics

- **Steady decode** excludes the first response. **Inclusive** throughput includes
  it. **First response** includes setup and prompt processing; it is not a pure
  prefill benchmark. The ds4 harness records time to first response byte and uses
  response chunks as its generated count. These sources are identified in detail.
- Comparison gains require known, matching engine, model, prompt fingerprint,
  requested and generated output counts, context, prompt format, temperature and
  thinking mode, plus complete runs. Unknown fields block a gain claim. Legacy
  K3 logs missing these fields can still be inspected side by side.
- One pair has no confidence interval. Matching workload fields do not establish
  equal machine state or quality. Multiple configuration changes prevent
  attributing a gain to just one knob.
- Equal saved MD5 fingerprints are an output consistency check, not a quality
  evaluation. Missing fingerprints are unknown, not a failed quality check.
- Live ds4 response writes are labelled **chunks/s**, not tokenizer tokens/s.
  A detected process by itself does not establish generation progress.
- Storage totals use simultaneous intervals across physical drives. Missing
  counters or uncalibrated ceilings stay unavailable. A stale chart is labelled;
  disconnected current values are not presented as live measurements.
- GPU memory and engine RSS can overlap and are not summed. Swap allocation alone
  does not establish current paging. Available RAM includes reclaimable memory.
- A last-landing read gap is not measured GPU stall or guaranteed recoverable time.
  Expert reuse is not a measured RAM cache hit. The new expert map displays route
  counts, avoiding unsupported model-size traffic estimates.

## Interface and collection cost

The interface uses local HTML/CSS/JavaScript modules, system fonts and code-native
charts. It has no frontend framework, runtime package install, CDN or web-font
request. Layout adapts to desktop and narrow windows. Navigation and run dialogs
support the keyboard; themes respect the system preference by default.

The live display polls only while visible and selected, at one-second intervals.
Pausing freezes the display; **the sampler keeps running**. Saved reports refresh
on request. Switching folders clears the report caches. Copied artifacts with
older timestamps still invalidate their run's evidence cache.

Legacy placement discovery no longer runs on the hot `/data` path. This removes
unnecessary work; no inference speed gain or new overhead number is claimed.

The server accepts localhost hostnames only. Applying settings requires a matching
Origin and a per-process token. Files are saved atomically. Old GET mutation
endpoints `/reset` and `/burstdump` return 405; they are not product actions.

## Source map

- `monitor/k3-live-page.html`: document shell and navigation landmarks.
- `monitor/app.css`: layout, visual tokens, themes and responsive styles.
- `monitor/app.js`: views, local API requests, interactions and chart rendering.
- `monitor/app-model.js`: pure comparison, formatting and filtering rules.
- `monitor/k3-live.py`: collection, artifact parsing, report APIs and local setup.
- `monitor/argodrive_core.py`: portable measurement and discovery primitives.
- `monitor/hardware_topology.py`: on-demand macOS topology discovery and storage identity joins.
- `monitor/topology-view.js`: interactive connection map and device inspector.
- `monitor/streaming_profile.py`: requested settings, engine declarations and arm file manifests.

## Validation and remaining work

```sh
./argodrive test
node --test tests/test_app_model.mjs
```

Python tests cover measurement correctness, artifact refresh, source validation,
atomic persistence, and settings HTTP failure paths. JavaScript tests cover
comparison eligibility, missing values, output checks, settings differences and
filtering. No browser automation or new inference benchmark was run for this pass.

Before an external beta: perform browser/accessibility review at several window
sizes; validate live hardware collection on another Mac; measure collection
cost; version engine telemetry schemas; and package, sign and notarize the Mac
application. The Mac wrapper now includes a native folder picker and export Save dialogs.
These still need clean-install validation on another Mac.
# Cluster module · Beta 2

The **Cluster** tab displays the saved ARGODRIVE Wire qualification report:
observed host/node topology, verified transfer counts, transfer-only and
verification-inclusive rates, application/device read comparisons, the S0
sensitivity replay and S0–S5 gate status. Export downloads the displayed JSON.

It is explicitly a recorded test session. Bundled development-rig evidence is
labelled as a reference and never presented as the current user's live cluster.
`<state-dir>/wire/status.json` overrides that reference; malformed local evidence
produces an error rather than silently substituting a successful reference.
The `/cluster` endpoint is read-only, filters public report fields, and neither
probes a peer nor enables inference. Network topology in this view is historical
inventory, distinct from the live local Topology tab.

The native C transport tools ship under `Contents/Resources/wire` in the Mac
preview. See `wire/README.md` for source-based index generation and verification.
Multipath, remote RAM caching, hints, RDMA and engine integration remain gated
future work. See `wire/BENCH.md` for the actual September 11 results and limits.

### ds4 engine settings

Streaming now separates **ds4 settings** from **Recorded arms**. Engine controls
are independent of the selected model family; Argonaut fork features and GLM-only
extensions are gated explicitly. Local drafts and candidate JSON/environment
exports are available. They do not launch or reconfigure an engine or establish
speed gains. See [DS4-SETTINGS.md](DS4-SETTINGS.md).
