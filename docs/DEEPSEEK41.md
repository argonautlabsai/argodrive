# DeepSeek V4.1 Flash Q4 in Argodrive

Status: experimental local integration, 12 September 2026. The frozen 30-arm performance campaign passed output comparison; the app does not automatically promote an engine or launch inference. Argodrive separates model families from engines: Deltafin for Kimi K3, and distinct ds4 capabilities for GLM 5.3 and DeepSeek V4.1 Flash.

## Prepare and configure

Streaming → Models & readiness checks the assembled file's size and eight-byte GGUF header, shows model/Engram sizes, plans up to two enclosure replicas and exports a structured preparation report. An existing replica with matching size/header is not budgeted as another full copy. Its content still requires a complete SHA-256 check. Different filesystems are not proof of independent physical SSDs or uplinks.

The ds4 engine-settings editor now supports two DeepSeek choices:

- **ds4 core controls:** the pinned upstream engine, with one model source.
- **Argonaut ds4 fork:** the experimental DeepSeek implementation, with an internal source and up to two complete replicas. This uses `DS4_ARGODRIVE_*`, not the GLM fork's `DS4_MODEL_*` controls.

Selecting the DeepSeek fork starts with nine expert reader threads, OS expert read-ahead disabled, automatic expert cache sizing, an independent primary expert descriptor requesting `F_NOCACHE`, and weights 2:1:1 as sources are added. The profile also enables layer queueing, earlier loading after the actual router IDs are known, resident gate/up compute before missing experts arrive, eight parallel primary-SSD Engram row readers, and phase markers. It does not enable speculative decoding, predicted expert prefetch, cache-reserve expansion or the rejected GPU prototypes.

The editor disables per-source piece and in-flight controls and does not offer offset-hashed reads or GLM eviction/prefill controls for this implementation. Those features belong to a different fork. Settings remain candidates: the user must verify the executable and replicas, then compare matched runs. Exporting an environment does not apply the accompanying CLI arguments or alter a running engine. Some flags are presence-based; unset them rather than assuming `0` disables them.

## Model and engine identity

Pinned upstream base: `bd66c402070042bf0a79ad6ece8242de4c93680c` from antirez/ds4. The clean candidate source patch and two headers are in `experiments/deepseek41-campaign-20260912/clean-candidate/`. Runtime Metal sources are part of build identity. Engine binaries and model weights are not bundled in the app.

The Q4 GGUF is **518,596,067,328 bytes**, approximately **483 GiB**: about 294 GiB main weights and 189 GiB disk-only Engram tables. SHA-256:

`a5e2e2c3ada4b2e98d9f9e4b50f6d9c2a12c2c96f5da165c07e13aff9264984e`

Every enclosure holds a complete identical GGUF. Expert reads are apportioned in 256 KiB blocks with a completion barrier before buffers are consumed. Engram uses whole 264-byte rows on the primary SSD by default; the fork has an opt-in row-striping mode across verified replicas. This is application-level replica reading, not RAID. Begin with automatic cache sizing; GLM's cache budget is not a DeepSeek recommendation.

The **Monitor Engram** view keeps physical-device traffic separate from engine-classified traffic. It renders teal weight bars and purple Engram bars only when per-request class telemetry is present; otherwise it displays grey unclassified device bars and reports the limitation. Engram row striping is opt-in and remains experimental; it requires identical-table verification, ordered whole-row reads, failure fallback and matched speed qualification.

## Reproduce and monitor

The portable build, verification and benchmark scripts are documented in `experiments/deepseek41-campaign-20260912/reproduce/README.md`. They create fresh output directories, record actual CLI/environment and binary/prompt/shader hashes, check completed checksum receipts against current file and volume identity, exclude overlapping inference, and enforce a swap-growth guard. There is no implicit download, copy, deletion or model launch from the readiness page.

Benchmark start/result records are imported by Argodrive's Monitor and Run library. The effective streaming profile reads engine evidence for source count, primary descriptor policy, Engram worker count and source traffic. Requested settings alone are not evidence that a path ran.

The optional sampler captures physical read counters at 100 ms, aligned to explicit monotonic prefill/decode markers. Report physical bytes and application bytes independently and over their actual intervals. Device counters can include other processes. `F_NOCACHE` is a policy request, not proof of a one-to-one physical read volume.

## Qualification and limits

Use 40-token arms for fast screening only, with two matched repetitions per control/candidate pair. Promote a winner to pp512 with tg128 and tg512, using three repetitions per configuration in rotated order. Compare upstream internal, fork internal, fork plus one enclosure and fork plus two. Measure pp2048 separately. Preserve all runs, output hashes, medians and ranges. The raw prompt is upstream `speed-bench/promessi_sposi.txt`, with context allocation 4096. A fresh process resets application cache state; it is not a physical page-cache purge.

Generation speed includes the first decode step. Steady generation excludes it; prompt processing and client time to first response are separate measurements. Response chunks are never labelled as tokens, and first decode-step time is not TTFT. Missing metrics stay unavailable.

The campaign also exercises code/prose/reasoning output equality and a persistent-server genuine partial-read failure/recovery. Those are bounded correctness checks, not comprehensive chat, tools, vision, long-context, multi-client or CUDA qualification. Local settings are not a public speed record. The research target was 25 generation tok/s and was not reached. At pp512/tg512, three-repeat generation medians were 9.45 upstream internal, 12.91 fork internal, 14.04 fork plus one enclosure and 15.47 fork plus two (range 15.46–15.49). Prefill remained essentially unchanged. These are local, one-prompt performance results, not a public record.

Upstream attribution and license remain in the source pack. Publication, email to Antirez and a downloadable signed beta are separate release actions.
