# Argodrive Wire benchmark guide

The wire module is an opt-in transport prototype. It serves byte ranges for
expert records while inference remains on the host. This document describes a
repeatable measurement format; it contains no machine inventory, addresses,
model files, or benchmark captures.

## Measure a node

1. Build with `make -C wire` on Apple silicon.
2. Create an index from a local test fixture and a randomly generated 32-byte
   secret. Keep both outside the repository.
3. Run the daemon on a trusted point-to-point interface and verify records with
   the client library.
4. Repeat with one, two, and three persistent connections and record transfer
   rate, hashing-inclusive rate, request count, errors, and cache hit rate.

Use the same record list, seed, and output identity for every arm. Report
application bytes separately from physical-device counters: application
`pread` bytes do not prove cold SSD traffic. Do not claim a token-speed gain
until a matched engine run has measured it.

## Required correctness gates

- Every response checks request id, layer, expert, length, and SHA-256.
- A short read or connection loss invalidates the whole batch and falls back to
  the local source.
- Cache-on and cache-off arms use the same request sequence.
- A failed arm remains in the report; it is never removed to improve a mean.

The Cluster view displays only bounded fields from a schema-1 report. Put
hardware-specific results in a local state directory or a separate publication
artifact after removing paths, hostnames, addresses, serials, and raw output.
The wire transport is not RDMA and is disabled unless explicitly configured.
