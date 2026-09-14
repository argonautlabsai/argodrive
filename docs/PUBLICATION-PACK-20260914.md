# DeepSeek V4.1 Flash Q4 — publication evidence pack

**Status: experimental local qualification.** This pack is ready for review, but it does not make a cross-build speed-record claim. The upstream ds4 binary and the Argodrive fork produced different deterministic output for the same 2,048-token prompt, so their rates are reported as separate controls rather than an apples-to-apples gain.

## Workload and controls

- Apple M5 Max, 128 GiB; one process at a time; idle GPU; fresh process for every arm; swap-growth guard; 100 ms physical-device sampler.
- DeepSeek V4.1 Flash Q4, full-file SHA-256 verified before admission.
- Upstream ds4 promessi_sposi.txt prompt, 2,048 prompt tokens, 128 generated tokens, context allocation 4,096, greedy benchmark defaults.
- Three repetitions per Argodrive storage layout in alternating order: internal only, internal + Green, and internal + Green + White. Two same-shape upstream internal controls were also run.
- Application expert bytes and physical sampler bytes are different measurements. No Engram bytes are inferred from token rate.

## Three-repeat Argodrive layout result

| Layout | Repetitions | Prefill median | Generation median | Steady median | vs internal | Steady range | Output SHA |
|---|---:|---:|---:|---:|---:|---:|---|
| Internal only | 3 | 67.87 tok/s | 10.57 tok/s | **11.30 tok/s** | baseline | 10.74–12.08 | `9c91f33bc55c5494d97c6237c8fe793a34085d6d28f9d7ead963cdb784b8bc13` |
| Internal + Green | 3 | 66.72 tok/s | 10.34 tok/s | **11.08 tok/s** | −1.9% | 9.51–11.19 | `9c91f33bc55c5494d97c6237c8fe793a34085d6d28f9d7ead963cdb784b8bc13` |
| Internal + Green + White | 3 | 66.61 tok/s | 11.80 tok/s | **12.73 tok/s** | +12.7% | 9.70–13.16 | `9c91f33bc55c5494d97c6237c8fe793a34085d6d28f9d7ead963cdb784b8bc13` |

All nine Argodrive arms produced the same output SHA-256. The +Green arm served application expert bytes from internal and Green; the +two arm served bytes from internal, Green and White in every repetition.

## Same-shape upstream control

**9.30 tok/s steady** (9.26–9.35) and **66.93 tok/s prefill** were measured in the two upstream internal controls. Their output SHA is `0809f8c4e2c910aefe3ebf017ed4af35185095ba3af7ad41d58f6c7c9b81cd42`; the Argodrive arms use `9c91f33bc55c5494d97c6237c8fe793a34085d6d28f9d7ead963cdb784b8bc13`. Because the text differs, these rates must not be described as a direct speedup or regression until the fork semantic drift is reviewed.

## Prior local 512/512 result

The earlier three-repeat Argodrive candidate measured 15.47 tok/s generation-inclusive and 15.91 tok/s steady with the internal SSD plus Green and White. That is a separate 512-prompt/512-generation workload and remains a local engineering result; it is not interchangeable with this 2K/128 table or the upstream ds4 Q2 reference.

## What can be published now

Publish the measurement method, the three layout medians and the output-parity caveat as an experimental engineering report. Do not publish “X% faster than upstream” until the output difference is explained and a matched quality gate passes. Do not include raw generated text, absolute paths, volume serials, private IPs, sampler logs or model files in a public repository.

## Evidence locations

- Numerical public-safe data: docs/PUBLIC-BENCHMARK-DATA-20260914.json.
- Raw per-arm plans, engine logs, generated text and sampler CSVs: local .build/ds41-public-qualification-20260914/, .build/ds41-public-qualification-r3-20260914/ and .build/ds41-upstream-qualification-20260914/; these are not public artifacts.
- Upstream comparison rules: https://github.com/antirez/ds4/blob/main/docs/PERFORMANCE.md
