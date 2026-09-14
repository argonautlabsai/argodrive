# Streaming optimiser: the next local product milestone

Owner clarification, 10 September 2026: ARGODRIVE tests SSDs and helps select
expert-streaming settings for the model and workload. The dashboard presents
and controls that work. This document specifies the integration still to build;
it does not describe a shipping automatic tuner.

## What exists

| Component | Present in the source repository | Work needed for the app |
| --- | --- | --- |
| Drive discovery and counters | Physical-device deduplication and aligned read counters in `monitor/argodrive_core.py` and `monitor/k3-diskscope.c` | Reuse identities in calibration records; retain verified topology and calibration provenance |
| SSD read testing | `drives/k3-drive-ceiling.py` reads expert `.bin` files with multiple workers and `F_NOCACHE`; `k3-ceiling-run.sh` sweeps worker counts on fixed paths | Portable folder selection, per-drive and simultaneous tests, structured results, cancellation and worker-error reporting |
| Configuration experiments | `harness/k3-arm.sh` accepts explicit settings overrides; `k3-assert-config.py` checks a subset against deltafin's resolved configuration | Versioned adapters, bounded search and complete verification of every setting being tuned; ds4 support must be audited separately |
| Expert placement | `placement/` contains simulations, replica staging and manifest tools | Model-format checks, capacity planning and an explicit placement workflow before changing weight locations |
| Evidence review | Runs, Compare and Diagnostics inspect saved ds4/deltafin harness artifacts | Repeated-arm qualification, trustworthy tokenizer counts and a saved recommendation tied to its evidence |
| Profile application and load adaptation | No integrated implementation in the preview | Engine-specific export first; controlled application and runtime adaptation later |

The standalone ceiling tester is a useful starting point, not a finished
calibration service. It uses a fixed legacy sampler path, an `iostat` fallback
that mixes reads and writes, and counter endpoints that do not precisely match
its application timing window. The shell sweep also has reference-machine paths.
Replace these assumptions before presenting a result as calibrated physical read
bandwidth. The current configuration checker skips unknown or absent fields;
the optimiser must report those as unverified, not a successful setting check.

## The user workflow

1. **Choose a model and workload.** Select a supported local engine and model;
   identify weight format, expert layout, RAM budget, prompt/output lengths and
   request concurrency. Choose the objective: first response, long-answer speed,
   or throughput under concurrent requests. A storage-only test can run without
   an engine, but cannot produce a validated model configuration.
2. **Test the drives.** Select existing weight files or expert directories. Show
   the proposed duration and I/O load. Measure each physical drive, then the
   selected drives together using model-relevant request sizes and concurrency.
   Distinguish application bytes from physical-device counters and record cache
   policy. Standalone peaks are not a simultaneous aggregate ceiling. Test
   shared hub/port members together as an additional group before all selected
   drives. Deduplicate identical member sets along nested links. Use those
   simultaneous results to constrain the combined allocation to that branch.
3. **Compare streaming settings.** Use only settings the installed engine version
   supports. Start from its working configuration and vary a small candidate
   set within a time and memory budget. Record what the engine actually applied.
4. **Validate the winner.** Repeat matched baseline/candidate arms with order
   reversal on the intended workload, including a long generation for a
   long-answer profile. Show median improvement, spread, worst regression,
   first-response latency and output checks. An inconclusive result retains
   the baseline.
5. **Save settings.** Export an engine-specific profile, exact configuration
   difference, prior settings and evidence. State where it was validated and
   when a hardware, model or engine change requires another test.

The initial workflow uses existing local weights. It need not download a model,
create replicas or integrate multiple Macs to deliver a useful result.

## What to tune

These are search dimensions, not universal knobs or promised supported APIs.
The adapter must enumerate available choices for the actual engine build.

| Dimension | What the optimiser compares | Evidence that matters |
| --- | --- | --- |
| Read method and cache policy | Engine-supported buffered, uncached or mapped paths, where implemented | First response and decode latency, physical/application bytes, CPU cost and memory pressure |
| Reader concurrency and request size | Worker counts, per-drive in-flight limits and chunk sizes where exposed | Completion latency and uncovered read wait; more workers can lose performance |
| Multi-drive scheduling | Supported split ratios, replica source selection and balancing | Simultaneous drive measurements and end-to-end speed; only use sources that contain the required bytes |
| Prefetch | Supported lookahead depth and demand/prefetch allocation | Useful, late and wasted prefetch, demand latency and total bytes |
| RAM expert cache | Supported capacity and admission/eviction settings | Demand cache hits, bytes avoided and model speed within the total memory budget |

Layer batch width, routed experts, quantisation and file layout affect the useful
read pattern. A streaming method that wins during batched prefill may lose during
decode. Only emit separate phase settings if the engine can actually switch them.
Repeated expert access does not by itself establish a RAM cache hit.

## What a recommendation means

Optimise the selected inference objective, not maximum SSD draw. Serving more
experts from RAM may improve token speed while reducing GB/s. Prefetch can raise
GB/s while adding no benefit. Report both mechanisms without confusing them.

A **calibration** describes observed drive performance under a specified test.
A **candidate** is a configuration worth evaluating. A **validated profile** is
the best observed configuration among the candidates tested for a stated
workload. None is a proof of a global optimum.

Use a quick search to shortlist candidates and a longer validation to qualify
them. Derive the time estimate from the baseline and candidate count; allow the
user to stop. A stopped session keeps completed results but does not label its
unfinished candidate validated. Do not use 40/60-token screening runs to claim
long-generation performance. Keep prefill and decode timing separate.

Comparisons must retain model identity, quantisation, engine revision, resolved
configuration, prompt identity and actual tokenizer counts. Existing ds4 response
chunks cannot silently become tokens. Record memory pressure, cache state, other
load, telemetry overhead and test order. A new process alone does not establish
a cold filesystem cache. Validation conditions must represent the intended use,
including warmed serving when appropriate. Identical text is a useful consistency
check where expected; it is not a substitute for quality evaluation.

## Reusable profiles

A profile needs a versioned record containing:

- Model content/revision identity, quantisation, expert/tensor layout and engine
  binary/revision with its supported configuration schema.
- Physical drive identities, selected files/replicas, verified connection topology,
  OS and memory capacity. Private paths stay local unless explicitly exported.
- Workload definition, objective and constraints, cache/pressure conditions,
  phase coverage and sampling policy.
- Baseline and candidate resolved settings, completed run IDs, validation results,
  scope of the recommendation and any unverified fields.
- Exported settings, previous settings for rollback, creation time and the
  conditions that make validation stale.

Use serial/content identities where available instead of drive colours or a
transient `diskN` identifier. Replugging behind a hub, changing the model format,
engine build or RAM budget must trigger a compatibility check. Imported profile
data is not executable shell code.

## Implementation order and beta boundary

| Order | Deliverable | Done when |
| --- | --- | --- |
| 1 | Portable read-only SSD calibration | Existing files remain unchanged; per-drive and simultaneous windows are attributed correctly; errors, disconnects and cancellation cannot create a successful result |
| 2 | One engine adapter and experiment runner | Supported settings are declared, the engine confirms them, tests respect memory/time limits, and stopping a test reaps only owned processes |
| 3 | Optimise streaming interface | User can select a model/workload, review a finite test plan, run it and inspect progress and completed evidence |
| 4 | Validation and profile export | A repeated matched comparison supports the recommendation; missing output/count/configuration evidence blocks promotion; exported and previous settings are inspectable |
| 5 | Controlled application, then adaptation | Profile compatibility is checked and rollback works; later load-triggered switching has bounded overhead, stable thresholds and serving correctness tests |

The existing downloadable 0.2 technical preview tests the measurement UI and Mac
packaging. It does not yet meet this optimiser milestone. Describe that package
accordingly. The first optimiser beta should complete orders 1–4 for one engine
and supported model layout before expanding to RDMA or peer RAM. A storage-only
calibration preview can ship earlier, with its narrower result clearly stated.

Keep the current stack: Swift/AppKit for the Mac application, Python for planning
and experiment orchestration, and native engine/reader code for moving expert
bytes. Engine scheduling remains in the engine; the app chooses and validates
its supported strategy. Automatic adaptation during serving is a later mechanism,
not an inference made from a drive benchmark.

## Five-drive placement constraint (12 September 2026)

The user reports Blue (renamed from K3B) and Yellow as WD_BLACK SN7100 1 TB
SSDs on the same bus-1 hub uplink. Blue's Spotlight policy is reported off;
ARGODRIVE must not silently promote that statement to a fresh `mdutil` reading.

Treat Blue+Yellow as one shared bandwidth constraint with independently measured
device service times. Do not copy Yellow's weight onto Blue automatically or add
their individual ceilings. First measure Yellow alone, Blue alone and both
together with relevant piece sizes; then compare all-five against the qualified
four-drive configuration with matched 512-token workloads and reversed order.
If the pair provides additional capacity, divide its measured branch allocation
between the two drives by completion behaviour. If five drives do not improve
the selected inference objective, retain four as the validated profile.

Local inspection found Blue in the main project's `k3-drive-names.json`, while
the GLM `tools/drives/k3-drive-names.json` copy still omitted it. The GLM mapping
script explicitly reads the main project's registry, so that unused local copy
does not affect the active map. Continue using the authoritative registry when
building the experiment's identity manifest; no weight rename or move is needed.

## ds4 configuration editor (12 September 2026)

**Streaming → ds4 settings** now provides model-independent ds4 core controls,
a separate Argonaut fork capability profile and gated GLM extensions. Drafts
persist locally; candidate JSON/environment exports are available. Existing
streaming evidence is under **Recorded arms**. See [DS4-SETTINGS.md](DS4-SETTINGS.md)
for exact boundaries and the source reference. This completes a configuration
editor/export step; it does not complete automated calibration, executable
compatibility detection, engine qualification or the experiment runner above.
