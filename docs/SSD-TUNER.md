# Five-minute SSD tuning test

Open **Streaming → 5-minute SSD test**. Enter one existing, complete GGUF model path per physical SSD (up to five), choose the model family and engine build, then press **Start 5-minute SSD test**. Stop retains partial evidence; it does not apply a recommendation.

The first version supports matching GGUF v2/v3 files with uniform routed Q4_K expert tensors, up to 32 MiB per expert component. DeepSeek V4.1 Q4's recognized decoder components are 6,635,520 bytes. The tester does not reproduce Engram row lookups, prefill batches, GPU overlap, model routing or RAM expert-cache behavior. Unsupported model layouts fail before the read trials.

## What is measured

- Each drive at 2, 4 and 8 workers per drive.
- Detected shared-uplink groups concurrently, followed by all selected drives together; duplicate nested shared groups are tested once.
- Two whole-component confirmations at the lowest tested concurrency within 5% of the best aggregate application rate.
- For multiple drives, equal/weighted/equal/weighted split-piece trials. Candidate weights start from measured simultaneous per-drive rates. Weighted allocation is preferred only if both pairs exceed 3% application throughput gain. These are independent piece reads, not simulated layer barriers.

The read budget is up to 270 seconds plus metadata/cleanup within a five-minute target. A single-drive test can finish earlier. Slow setup, disconnected storage or outstanding blocking OS reads can prevent completion; partial evidence is retained, without a recommendation. A watchdog cancels at the deadline; individual in-flight reads must return before graceful cleanup completes.

Application `pread` throughput and request p50/p95 are reported separately from physical-device read counters. Device counters use aligned sample intervals strictly inside each trial and include all applications. They do not prove exclusive model-file traffic. The bounded Python read tester has its own allocation and scheduling overhead. `F_NOCACHE` is enabled for the test, not inferred to guarantee uncached NAND reads.

## Safety and conflicts

All model descriptors are `O_RDONLY`. Opened file identities are checked before any read trial and after it; volume/model identities are checked before and after calibration. Matching GGUF header hashes and sizes do not establish full replica payload equality. Report instructions require full verification before inference uses replicas.

Start refuses an occupied shared `/tmp/argodrive-glm-campaign.lock`, which is also used by supported model copies and inference campaigns. The worker rechecks the lock, active inference and initial system-wide disk traffic. A watcher cancels calibration if another inference engine appears. Uncoordinated external file copies cannot be prevented; keep other disk workloads idle.

Each test creates a unique directory under `~/Library/Application Support/ARGODRIVE/ssd-tuning/`. No model files are copied, deleted or redistributed. No raw-device writes, RAID creation, formatting, Spotlight changes or engine launches occur. The sampler and calibration process are owned by the app, and closing the app stops its calibration. Reports are retained.

## Handover and validation

**Copy instructions for Claude** includes source identities, component sizes, trial measurements, shared-link information, candidate allocation and a baseline/candidate/baseline/candidate validation plan. Text and raw JSON exports are available. **Open model test setup** opens the existing inference setup; it does not start an engine. Only separately qualified model profiles can run there.

**Compose email report** opens the user's default mail client addressed to the public
`benchmarks@argonautlabs.ai` intake with a short redacted summary. It does not send automatically and does not
include local paths, volume UUIDs, or serial numbers. Attach the exported text or JSON report only after reviewing
it. The address must be provisioned with the domain's mail provider before distribution.

Upstream and Argonaut capabilities remain distinct. DeepSeek V4.1 does not receive an executable GLM replica configuration. The handover asks Claude to inspect exact source/binary support before changing settings. Cache sizing, prefetch, read-ahead and Engram policies are left for model testing. Results are labelled **Recommended settings — awaiting model validation**, with no predicted token-speed gain.
