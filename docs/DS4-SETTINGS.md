# ds4 engine and SSD settings

Open **Streaming → ds4 settings**. ds4 is the execution engine; GLM and DeepSeek
are model families. A tuning result is specific to an engine build, model,
quantisation, workload, memory budget and hardware topology.

The editor creates **unvalidated candidates**. It does not launch ds4, move
weights, configure RAID, change active inference settings or establish a speed
gain. Recorded arms and Compare retain the measured result and effective-setting
evidence independently of the editor.

## Controls and capability boundaries

The source reference is ds4 base `6289c51` plus the locally reviewed Argonaut
changes in `ds4_metal.m`, SHA-256
`af57b29f7e62b8b2cbb78c2312abbe1ace68b4c55307ca578ae944e9b0bcb539`.
This is a capability subset reviewed on 12 September 2026, not automatic detection
of an installed executable and not a claim that all upstream versions match it.
The base commit alone does not identify the modified fork or its binary.

| Control | ds4 core profile | Argonaut ds4 fork profile |
| --- | --- | --- |
| Backend / streaming | Metal, `--ssd-streaming` | Same |
| Reader workers | 1–18 in the reviewed base source | 1–64 in the reviewed fork |
| OS expert read-ahead | Engine default or disable | Same |
| Expert cache target | Automatic, positive expert count or NGB | Same |
| Cold start | Skip popularity preload; runtime cache remains enabled | Same |
| Replica allocation | Single source only | Single source, weighted split reads, offset-hashed replica reads |
| Per-source controls | Not offered | Positive weights, piece counts, split-mode in-flight caps |
| Expert file caching / eviction | Not offered | F_NOCACHE request and pure LRU |
| Decode cache reserve | Automatic | Optional `DS4_ARGODRIVE_DECODE_CACHE_PCT=100` candidate; total memory allowance unchanged |
| GLM lookahead / selected prefill | Not offered | GLM family only |

For the current DeepSeek V4.1 qualification, the retained binary is the reviewed 18-worker build. A temporary source experiment expanded the pool to 32 and 64; both were slower in the 40-token screen and produced a different capitalization, so those settings are unqualified and are not the default.

The editor bounds the fork to eight source descriptors and a sum of at most 64
weighted slots. Piece counts are 1–16; split-mode in-flight limits are 0–64,
where 0 means uncapped. Source order is primary, then replicas. Path uniqueness
does not establish distinct physical devices or identical content. Commas and
asterisks cannot be represented in the replica path syntax and are rejected.

OS read-ahead disabling is a **presence-based** flag in the inspected source.
Engine default unsets that flag; setting it to zero would still disable it.
Single-source subdivision sets the global piece count so that the reviewed
shared planner activates. Lookahead candidates which would leave that planner
inactive are rejected. GLM controls are cleared when the family or build changes.
The V4.1 decode-cache control transfers unused prefill reserve into decode
expert slots during a drained phase; it is disabled when unset. The 100% value
is a candidate for matched qualification, not a default or a guaranteed gain.
Switching to the core profile removes replica rows and fork-only controls; do
that deliberately, or save/export the fork draft first.

## Saving and exporting

**Save draft** stores data in the app's local `argodrive.local.json`, preserving
the run folder. It works with incomplete drafts and survives backend restart and
port changes. Writes require the existing localhost Origin/token checks and are
atomic. The backend checks bounded draft shape; this is not engine qualification.
The browser checks the draft again before rendering it or compiling a candidate.

**Review candidate** validates supported values and dependencies. **Export profile
JSON** includes CLI arguments, environment overrides and explicit unsets, source
paths, model family, objective and the supplied build identity. Compatibility and
physical-device fields remain unverified; speed gain is null and validation arms
are empty.

**Export environment** safely quotes values and clears only the managed controls.
It does not execute a launcher. Its CLI argument comment must be passed separately
by the ds4 launcher. Other flags and environment variables remain unchanged; the
candidate is not a full replacement for the harness configuration. Use a clean,
matched test environment when qualifying it. No environment file is sourced by
ARGODRIVE.

## Qualification still required

1. Identify the exact engine binary and verify supported model/quantisation paths.
2. Resolve source files to physical SSDs; verify complete identical replicas.
3. Measure per-drive and simultaneous shared-hub read behaviour.
4. Compare a working baseline and candidate on the same workload with reversed
   order, output checks and separate prefill/decode measurements.
5. Promote a profile only when the effective settings and repeatable gain are
   established. More read GB/s alone is not sufficient.

The integrated benchmark runner and automatic recommendation/promotion are still
being developed. This editor is the engine-specific configuration/export step.
