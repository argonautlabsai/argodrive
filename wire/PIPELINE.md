# Queued TCP transfers and shared expert cache

The M1 Max remains a TCP peer. The host keeps model execution local. This
prototype adds data movement and telemetry; it does not connect to ds4 or claim
a token-speed improvement.

## Node configuration

```sh
argodrive-node <node-address-1>,<node-address-2> 9017 records.index session-secret \
  --read-ahead 2 --cache-mib 4096
```

Choose valid local addresses and an unused port. Port zero chooses one free
port shared by every listener. The secret is a private 32-byte file, never a
command-line value. Index identity and source immutability rules still apply.

- `--read-ahead`: 1–4 slots per connection; default **1**. Two lets the next
  file read run while the current response is sent. Four is an experiment,
  not an automatic improvement.
- `--cache-mib`: 0–8192 MiB; default **0**, disabled. This is a bound on allocated
  expert payloads, including entries still loading. Metadata, socket buffers
  and private response slots are additional memory.
- Up to three explicit listeners share **one process-wide LRU**. Cache keys are
  index identity (fixed for the daemon lifetime), layer and expert. Data is
  stored once, independent of which cable requests it.
- At most eight connections. Private slots grow only to requested record sizes;
  at the 64 MiB record limit, eight connections × four slots can require 2 GiB
  outside the cache. The measured two-connection, depth-two configuration has
  four possible private slots (about 67 MiB for this sample).

The cache uses ordinary pageable RAM, not wired memory or Metal allocation.
No system memory limits are changed. A product launcher must choose a budget
from current memory pressure; the CLI applies the explicit user budget without
an adaptive pressure controller. The development campaign checks at least 25%
system-wide free memory as reported by `memory_pressure -Q` before each arm.

## Ownership and error handling

Each connection has an ordered reader/sender ring. A slot is released only after
its send ends. The cache reserves capacity before loading and publishes an entry
only after a complete successful source read. Concurrent misses on an entry
being loaded wait for that load; they do not create duplicate cached payloads.

Referenced and loading entries cannot be evicted. If the budget cannot fit an
entry because other entries are pinned, that request uses its private slot.
It does not exceed the cache limit or wait indefinitely for eviction. Shutdown
joins connection readers before freeing slots and checks zero remaining cache
references/loading entries in qualification.

The host library queues a bounded number of GETs, validates every response's
request ID, record identity and length, and receives directly into caller-owned
destinations. On a queued NAK or transport error, the entire batch is invalid.
The Python adapter closes the failed connection; the transport waits for every
other writer before raising. No background operation writes after return.
Piece retries and engine fallback are still pending.

## Host interface

`wire/host/transport.py` defines `ExpertTransport.read_many(requests)` and
`close()`. `TCPTransport` implements them using persistent native clients,
one or two connections per endpoint, an adjustable window, and deterministic
assignment to the connection with the fewest planned bytes. It supports up to
64 records per call, rejects overlapping destination ranges, serializes calls,
and caps the total outstanding window at 32.

Requests are `(layer, expert, ctypes_buffer, length)` tuples. The caller owns
the destinations and their memory budget. `benchmark_pipeline.py` additionally
caps its total preallocated host buffers at 1 GiB. The current measured batch
of 16 records uses about 268 MiB. An RDMA backend could implement this ownership
contract later; there is no RDMA implementation or hardware enablement here.

## Telemetry

`Client.stats()` / `TCPTransport.stats()` use a synchronous STAT request.
Counters are shared across all listeners:

- GETs, completed bytes sent and errors.
- Cache hits, misses, coalesced waits, evictions, bypasses and hit bytes.
- Cache budget, allocated/peak bytes, live references and loading entries.
- Bytes returned by `pread`, read calls, gross read seconds and gross send
  seconds. Gross times sum overlapping workers and must not be added as wall
  time. `pread` bytes are application reads, not physical SSD counters.

Hit rate is `hits / (hits + misses)`. A coalesced loading wait counts as a miss
and a wait, not a resident hit. Cache-off GETs count as misses. Capacity gauges
are snapshots; differences in those gauges are not traffic counters. Snapshot
stats during traffic may span slightly different instants across workers.

## Qualification

The loopback suite covers ordered windows, destination guards, bad framing,
partial network/source reads, shared-cache hits, eviction, budget bounds,
multi-connection failures and quiescence before destination reuse. Hardware
arms use both independently configured M1 addresses on a single daemon.

The benchmark uses a fixed 200-record K3 index, a full-index warm-up, identical
seeded requests, 16-record host batches and two SHA-256 workers. Each record is
verified. Transfer throughput uses the common interval around `read_many`,
including both paths and scheduling. Hash-inclusive throughput additionally
includes verification between batches. Warm-up is excluded and reported.

A 4 GiB cache holds **the entire 3.27 GiB sample**. A 100% hit rate here proves
cache behavior; it does not forecast hits for the full model or real routing.
OS cache state is uncontrolled, and cold SSD throughput is not qualified.

See [measured results](results/pipeline-2026-09-11/RESULTS.md). The old test
evidence and executable are retained as historical references. No changes are
published and no background node service is installed.
