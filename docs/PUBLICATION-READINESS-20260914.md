# Publication readiness — 14 September 2026

## Decision

The local release materials are prepared for review, but nothing has been pushed,
emailed or posted. The current checkout must not be published wholesale. Use the
curated public file list below and run the privacy scan again on the exact staged
tree immediately before a GitHub push.

## Verification completed

| Check | Result |
|---|---|
| Argodrive Python tests | **287 passed, 1 skipped, 1 warning, 52 subtests** in 22.68 s |
| Argodrive JavaScript tests | **73 passed, 0 failed** in 79 ms |
| Frozen Mac app smoke | **PASS**: launch without external Python, assets, reports-only isolation, source selection, CSV export, source immutability, persisted restart, parent identity refusal, clean termination |
| App code signature | **PASS**: `codesign --verify --deep --strict` |
| DS4 deterministic/unit checks | **PASS**: Q4_K, MXFP4, memory, session, tensor-parallel commands, evaluation cases, extractors, agent tests |
| DS4 full `make test` | **INCOMPLETE**: integration step requires the private/local `ds4flash.gguf` fixture, which is not present |

The rebuilt candidate is ad-hoc signed and not notarized. The current ZIP is
`.build/release-20260914-final/ARGODRIVE-0.2.0-beta.3-macos-arm64.zip` with
SHA-256 `2ef421b7f6e1aa006b457a7f60a171e9e366ff1c597cb59cfc9c0b47c4f78558`.
Do not describe it as a Gatekeeper-ready public release until a Developer ID
build is notarized.

## DeepSeek V4.1 measurements

The fresh current-binary screen used 512 prompt tokens and 512 generated tokens,
the internal model plus Green and White replicas, primary weight 2, eight Engram
readers, queue/early-expert/resident-gate settings, read-ahead disabled, and a
fresh process per arm. All three arms produced the same output SHA-256.

| arm | prefill tok/s | generation tok/s | steady tok/s | result |
|---|---:|---:|---:|---|
| default profile | 16.29 | 9.05 | 9.15 | diagnostic only |
| 64 read threads | 16.49 | 8.72 | 8.83 | slower; do not promote |
| timeline disabled | 16.25 | 12.43 | 12.70 | one arm; requires repeat |

This run has application byte counts but no attached physical-device sampler
trace, and it does not reproduce the retained historical 16.54 tok/s observation.
It is therefore a diagnostic screen, not a new publication claim. The retained
publishable local result remains the three-repeat pp512/tg512 qualification at
15.47 tok/s generation-inclusive and 15.91 tok/s steady with two external replicas.
The separate 2,048-prompt/128-generation table remains an experimental layout
comparison: 11.30 tok/s internal, 11.08 with one enclosure, and 12.73 with two.

At the time of this screen, Green and White were mounted and Yellow was absent.
This is a two-replica run; it is not evidence for a three-enclosure result.

The 16.54 observation stays explicitly historical until the exact executable or
complete source state is recovered and passes a quiet, sampler-backed, rotated
three-repeat run. No record or cross-build speed claim is ready from today’s
screen.

## Public-file privacy gate

The selected public pack was scanned for absolute home and volume paths, private
addresses, non-public email addresses, credential assignments, private-key
markers, serials, raw responses and model files. It contains only the public
`benchmarks@argonautlabs.ai` contact and documentation placeholders.

Publish only the files listed in
[`PUBLIC-FILE-MANIFEST-20260914.md`](PUBLIC-FILE-MANIFEST-20260914.md). Exclude
`.build/`, `dist/`, `wire/results/`, raw sampler CSVs, generated responses, model
weights, session handoffs, backups, object files and local benchmark captures.
The working tree still contains older K3 and wire files with machine paths and
test-network examples; those are development evidence and are not in the public
allowlist. A final scan of the staged file list is mandatory.

## Ready-to-review materials

- [Hacker News draft](HN-DRAFT-20260914.md)
- [Email draft for Antirez](EMAIL-ANTIREZ-20260914.md)
- [Publication evidence pack](PUBLICATION-PACK-20260914.md)
- [Privacy audit](PUBLISH-PRIVACY-AUDIT-20260914.md)
- [Public benchmark data](PUBLIC-BENCHMARK-DATA-20260914.json)

The email draft deliberately does not embed an unverified personal recipient
address. The final recipient must be selected from a verified public channel.
