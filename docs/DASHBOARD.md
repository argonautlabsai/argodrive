# ARGODRIVE product workspace

ARGODRIVE helps users understand and improve local AI inference with recorded
measurements. The interface is organised around five jobs, with setup in Settings.
It runs on localhost; this source preview does not yet include a signed Mac app.

## Product navigation

| View | Purpose | What is available |
| --- | --- | --- |
| Overview | Understand the latest recorded result | Latest complete run, steady/inclusive speed, first response, recorded bytes per token, recent results and evidence coverage |
| Live monitor | Understand the current machine activity | A common storage timeline, compact drive cards, response telemetry, memory context and collector health |
| Runs | Find and inspect an experiment | Search, engine/length/status filters, pagination, run details, prompt, configuration and CSV export |
| Compare | Evaluate one explicit baseline/candidate pair | Workload match checks, speed and latency deltas, configuration differences, output fingerprint checks and a Markdown export |
| Diagnostics | Investigate the recorded mechanism | Per-drive read-tail attribution, layer-pass timelines, trace export, expert route map and most-used expert counts |
| Settings | Connect data and personalise the workspace | Validate/apply a local run folder, persistent source selection, light/dark/system theme, collection instructions |

The old Processes, Scheduler, Peak reader, Storage + Memory and Expert map tabs
are no longer separate top-level destinations. Useful memory and device context
lives in Live; read traces and expert activity live in Diagnostics. Legacy
placement scripts, scheduler analysis and standalone research tools remain in the
repository. Their experimental controls are not presented as product features.
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
application. Source selection currently uses an editable path; a native folder
picker belongs with the Mac shell.
