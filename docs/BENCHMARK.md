# Benchmark tab

The Argodrive **Benchmark** tab is the entry point for selecting an SSD streaming profile on a user's Mac.
It runs the existing read-only SSD tuner and presents the result as a candidate profile for the selected engine.

The workflow is deliberately three steps:

1. **Measure** each eligible drive using expert-sized reads, device counters, and the detected Thunderbolt topology.
2. **Recommend** a reader count, split, and placement profile from the measured throughput and latency.
3. **Verify** the candidate with a short model run before it is promoted or used as a published result.

The tab does not create RAID, write model files, change drive contents, or launch inference automatically. A
calibration result describes the storage side of the workload. It is not a token/s guarantee: generation speed also
depends on the model, GPU kernels, cache residency, Engram, prompt, and engine settings. The UI therefore leaves the
token speed field as **Not measured** until a model verification run records an actual result.

The existing Streaming view remains the detailed settings and readiness workspace; Benchmark is the guided starting
point for new users.

Completed calibrations can be shared with **Compose email report**. Argodrive opens the default mail client addressed
to the public `benchmarks@argonautlabs.ai` intake with a short redacted summary; the final Send action remains with
the user. Export the text or JSON report separately when full trial evidence is needed; local paths, volume UUIDs,
and serial numbers are omitted from the email summary. The address must be provisioned with the domain's mail
provider before distribution.
