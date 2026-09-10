# ARGODRIVE development preview

This branch consolidates the newer local ds4/GLM dashboard with the published
ARGODRIVE instruments. It is a source preview, not a signed Mac application.

## Start with saved runs

Python 3.10 or newer is required. No third-party Python packages are needed.
From the repository directory:

```sh
./argodrive run --reports-only --runs /path/to/arms
```

Open http://localhost:8130. The runs directory should contain block directories,
with each arm's `.log`, `.csv`, `.map`, `.sys` and other available artifacts
beside one another. Missing artifacts are allowed; their metrics remain unavailable.
A bare log is enough to inspect its recorded result.

Reports-only mode starts no sampler or engine. Quit the foreground process with
Ctrl-C. Choose another port with `--port 8131` if the original dashboard is running.

## Live collection on macOS

Build the existing C sampler using Apple's command-line developer tools:

```sh
./argodrive build
./argodrive doctor --runs /path/to/arms
./argodrive run --runs /path/to/arms
```

The dashboard binds to localhost. It discovers the internal physical store and
mounted volumes, and deduplicates volumes backed by the same disk. Multi-store
volumes are not attributed to an arbitrary single disk. Drive ceilings are
unknown until configured; the reference machine's numbers are never used as
another user's hardware limits. The current C sampler supports at most eight
physical devices.

Copy `config.example.json` to `config.local.json` if you want to select drives,
use friendly names or specify measured ceilings. An optional `ceiling_gbps`
field on each drive must be a positive number measured for that setup. A
`connection` field can describe a verified port/hub arrangement; auto-discovery
alone does not establish that topology.

```sh
./argodrive run --config config.local.json
```

Default collection is 100 ms, rendered in approximately 200 ms windows.
`--sample-ms 10` enables a more expensive diagnostic sampling cadence. Raw ticks
are completion-counter deltas, not direct measurements of instantaneous bus
saturation. Keep the dashboard off during headline speed benchmarks.

An optional `--marker /path/to/k3-live-marker.json` connects a harness's live arm
identity. Engine detection recognises `deltafin` and `ds4`. ds4 `.chunks` files
expose response-write rates, displayed as **chunks/s**, because writes have not
been established to equal tokenizer tokens. An engine process alone does not
establish that it is generating rather than serving idle. Without a marker,
recent chunks are labelled recent response telemetry rather than attached to a
particular process.

See [DASHBOARD.md](DASHBOARD.md) for the redesigned views and interaction guide.

## Measurement changes

- Aggregate live rates use the same start/end interval on every mounted disk;
  missing devices do not silently contribute zero. Peaks are updated from those
  common windows, never by summing independent per-drive peaks. Mount changes
  reset the live aggregate history and peaks.
- Rates use counter byte differences divided by actual elapsed time. Counter
  resets and duplicate timestamps cannot produce negative throughput.
- Aggregate capacity includes only resolved physical disks and is unavailable
  if any included disk lacks a calibrated ceiling. The old M1 network member
  is excluded from SSD totals.
- Collector freshness and engine detection are visible. Missing counters are
  unavailable rather than synthetic zero measurements.
- GLM read-trace accounting uses each row's byte length and recognises named
  devices. Expert-map summaries consistently use their model-size estimate.
  Historical GLM expert-map estimates still assume 20.25 MiB per routed Q4_K
  expert; other formats need explicit model metadata before their traffic can
  be treated as accurate. Raw read-trace byte counts do not use that estimate.
- By-layer last-landing gaps describe read completion order, not causal GPU
  stalls or guaranteed recoverable time. Concurrency uses the whole recorded
  interval. Older traces can contain approximate timestamps; this cannot repair
  an incorrectly recorded timestamp.
- Historical SSD means distinguish active-only throughput from the full sampler
  window. Historical totals require matching device intervals.
- ds4 inclusive throughput and steady decode are distinct. ds4 comparisons
  match model, prompt hash, generation length and recorded context/template/
  sampling fields. Other environmental differences still require review; this
  is not automatic experimental qualification. The product Compare view also blocks claims for
  legacy K3 logs with missing workload fields; old CSV delta columns retain their
  historical rules and should not be used to qualify a new comparison.
- System GPU allocation is not attributed to an engine just because it exists,
  and is not added to process RSS or mapped weights. Memory capacities use
  detected hardware rather than an assumed 128 GiB.

## Validation

```sh
./argodrive test
```

Tests cover aligned and misaligned disk samples, missing devices, counter resets,
variable sampling intervals, drive deduplication, invalid configuration, process
identification, response-chunk labels, exact GLM byte lengths, expert-size
summaries and inclusive-versus-steady rates. The sampler builds on macOS.

## Remaining before external beta

- Validate live IOKit collection, mount changes and process detection outside
  the development sandbox on another Mac. This development session cannot
  access the host's disk-management service or process list.
- Add a native folder picker with the Mac shell; path validation and persistent
  source selection are already available in Settings.
- Version engine/model telemetry schemas; remove remaining legacy assumptions
  from advanced K3-only tabs and unsupported expert formats.
- Package the backend and sampler in a native Mac shell, sign and notarize it.
- Check end-to-end collection overhead and recruit independent testers.

The existing benchmark harness and placement scripts remain research tools.
Launching the dashboard does not run inference, download weights, move model
files or change an engine configuration.

## Product pass verification (10 September)

The redesigned workspace passed 20 Python tests and 8 JavaScript model tests.
Local HTTP checks exercised the shell/assets, source settings, reports, ds4 run
details, read timing and expert-profile endpoints. Four saved 128/512-token runs
were used for the preview, with their original storage, memory and fingerprint
artifacts. The saved read trace parsed 288,390 records and exactly 408,238,424,064
bytes. The expert heatmap dimensions come from the supplied profile (79 layers,
256 experts), rather than an assumed GLM dimension.

These are report/data checks. They do not qualify live sampler overhead, visual
rendering on other devices, or a downloadable Mac release.
