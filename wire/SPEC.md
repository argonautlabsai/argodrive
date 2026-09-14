# ARGODRIVE Wire — technical specification

**Remote expert node over multipath Thunderbolt, prototype on M1 Max → M5 Max**

Version 1.0 · 11 September 2026 · Argonaut Labs
Implementation sequence: build in the order given; each stage has a validation gate.

---

## 0. What this is and what it is not

**What it is.** A second tier for the ARGODRIVE residency layer: an expert node that
serves expert records to the inference host over several Thunderbolt links at once,
with a RAM cache on the node, split reads across the links, and lookahead hints from
the host so hot records are in the node's RAM before they are requested. On the host
side it is one more device type for the balancer; on the node side it is a daemon.

**What it is not.** Not a replacement for local enclosures on a host that has them.
Not a way to exceed the link's bandwidth. Not distributed inference — the host runs
every layer; the node only holds bytes.

**Why it exists.** The same code, on Thunderbolt 5 RDMA between two Mac Studios,
makes a remote RAM tier faster than a local enclosure. The M1 Max prototype is how
that gets measured before that hardware is bought.

**The constraint it must respect.** A barrier waits for the slowest of its N expert
reads. Every design choice below exists to keep a remote read from being the
slowest one.

---

## 1. Hardware and topology

| | Host | Node |
|---|---|---|
| Machine | MacBook Pro M5 Max 128 GB | MacBook Pro M1 Max 64 GB |
| Ports used | 3 × Thunderbolt 5 (direct, no hub) | 3 × Thunderbolt 4 |
| Cables | 3 × TB4 (TB5 ports negotiate down to 40 Gbps) | |
| Local storage | internal SSD | internal SSD holding the full expert set |
| Engine | ds4 fork with ARGODRIVE patches | none — daemon only |

**No hub anywhere in the path.** A hub funnels all links into one upstream and caps
the whole thing at ~9 GB/s. Three direct links are the design.

**Pre-flight, before any code:**
1. On the node: `system_profiler SPThunderboltDataType` — confirm the three ports
   are on separate controllers. If two share a controller, the design degrades to two
   effective links and the numbers in §7 change; record it and continue.
2. On the node: a two- and three-stream sequential read bench on the internal SSD
   with `F_NOCACHE` — record the aggregate. Expected 5–7 GB/s. This is the cold-miss
   ceiling of the node.
3. Bring up three Thunderbolt Bridge interfaces, one per cable, each with its own
   static IP on its own /30. Do not rely on macOS's `bridge0` aggregation — it does
   not load-balance across physical links to the same peer. `iperf3` each link
   individually; record per-link throughput and round-trip latency. Expected ~4 GB/s
   and ~0.2–1 ms per link.

**Gate 1:** three independent links each measured; SSD aggregate measured; numbers
written into `wire/BENCH.md`.

---

## 2. Data model

The unit is the **expert record**: the same contiguous, aligned byte range the local
path already reads (GLM: one routed expert's gate/up/down for one layer, ~21.7 MB at
Q4_K). Records are identified by `(layer, expert)` exactly as on the host.

The node holds the full record set on its SSD in the same pack layout the host uses
(reuse the existing staging tool; do not invent a second format). The node's RAM
cache holds whole records.

**Wire unit:** a record is transferred as **P pieces**, `P = number of live links`,
each piece a contiguous byte range of the record, one piece per link. Piece
boundaries are fixed per record (`piece_i = [i·len/P, (i+1)·len/P)`) so a piece is
re-requestable on any link.

---

## 3. Protocol

TCP over each Thunderbolt Bridge interface. One long-lived connection per link, kept
warm. No TLS on the wire (point-to-point cable, no network); a shared secret in the
handshake so a stray process cannot request records.

Message framing: fixed 24-byte header, little-endian, then payload.

```
u32 magic            'AWR1'
u8  type
u8  flags
u16 reserved
u32 layer
u32 expert
u32 piece_index      0..P-1, or 0xFFFFFFFF for whole-record messages
u32 length           payload bytes
```

Types:

| type | direction | payload | meaning |
|---|---|---|---|
| `HELLO` | host→node | version, P, secret | opens the link; node replies `HELLO_OK` with cache size, record count, SSD rate |
| `GET` | host→node | none | send this piece of this record now |
| `DATA` | node→host | piece bytes | response to GET; header echoes layer/expert/piece |
| `HINT` | host→node | list of (layer, expert) | these records will be requested soon; warm them into node RAM |
| `CANCEL` | host→node | list of (layer, expert) | hints no longer needed |
| `STAT` | node→host | counters | periodic; see §6 |
| `NAK` | node→host | reason | record not present / read failed; host falls back to local |

A `GET` for one record from the host is P `GET`s, one per link, for pieces 0..P-1.
The host reassembles in a pre-allocated buffer that the engine's Metal path wraps
exactly as it wraps a local read (zero copy — the pieces land in the final buffer at
their offsets). **No memcpy on the host side.** This is the same rule that fixed the
Summer pool; do not reintroduce the copy.

---

## 4. The node daemon (`argodrive-node`, runs on the M1 Max)

Single process, Rust or C. Owns:

**4.1 The record store.** An index `(layer, expert) → (path, offset, len)` built
at start from the pack manifest. Reads use `pread` + `F_NOCACHE`, a reader pool of
N threads (start at 16; ladder later).

**4.2 The RAM cache.** One unified cache — **not one per link**. A record cached
for link A is served over link B when A is busy. Pinned arena, `wired_limit` raised
as on the host, size configurable, default 48 GB on a 64 GB machine (leave ~12 GB
for OS + daemon). Whole-record slots. Policy: LRU with hint-priority — a record
arriving via `HINT` is admitted with a fresh timestamp; a record admitted by demand
is admitted the same way; eviction is LRU over both. Keep it simple; the host's own
cache already holds the head of the heat curve, so this tier is the second band.

**Read-into-slot.** On a miss, reserve the slot, publish `INFLIGHT`, `pread`
directly into the slot's memory, mark `READY`. No scratch buffer. Concurrent `GET`s
for an `INFLIGHT` record wait on it rather than issuing a second read.

**4.3 The link servers.** One accept loop per interface. Each connection is handled
by a thread that reads requests and writes responses in order per connection;
requests on one connection are pipelined (the host may have many outstanding). A
`GET` for a `READY` record is served straight from the slot; for `INFLIGHT` it waits;
for absent it triggers a demand read.

**4.4 Hints.** A `HINT` list is queued to the reader pool at lower priority than
demand reads. A hinted record that is already cached is a no-op. `CANCEL` drops
queued hint reads that have not started; started reads complete (they are useful
anyway).

**4.5 Lifecycle.** Graceful shutdown drains in-flight reads. On a read error the
daemon `NAK`s that piece and logs it; it does not exit. On link loss the daemon keeps
its cache and waits for reconnect.

**4.6 Counters (emitted in `STAT` every 500 ms and to a local log):**
`gets, hits, misses, inflight_waits, bytes_sent[per link], hint_received,
hint_admitted, hint_cancelled, evictions, ssd_read_ms_p50/p90, cache_bytes_live`.

---

## 5. The host side (ARGODRIVE patch in the ds4 fork)

**5.1 A new device type: `wire`.** The balancer already models a device by
`(rate, in_flight)`. A wire link is a device with a measured rate (from Gate 1) and
its own in-flight counter. Three links are three devices.

**5.2 Placement.** The host's existing layout language gains a home type
`wire:<node-id>`. In the prototype the node holds the complete set, so every record
has a wire home; local homes are added as usual. The balancer chooses a home by
expected completion exactly as today. **A remote read is one candidate among the
homes, never the only one for a hot record** — hot records must keep a local home
or the barrier tail moves to the wire.

**5.3 Split reads over the wire.** When the balancer picks the wire home, the read
is issued as P pieces, one per link, each into its offset of the destination buffer.
Completion = all P pieces landed. This is `SPLIT_READ` applied to links; reuse the
completion counting.

Piece-level fallback: if a link's piece has not arrived within `T_piece` (start at
3× the link's p90), re-request that piece on another link. Late duplicate arrivals are
discarded by sequence number.

**5.4 Wire lookahead.** The existing router-lookahead prefetch already computes
the next layer's expected experts K layers ahead. Add: for records whose chosen home
is the wire, send `HINT` to the node at the moment the prediction is made. On
prediction change, send `CANCEL`. The node then has the record in RAM by the time
the `GET` arrives.

**5.5 Fallback.** A `NAK`, a link timeout, or a node disconnect converts the read
to the record's next local home with no error to the caller. Log it.

**5.6 Assertions.** Extend the config assertion harness with the wire's
intent/resolution/effect triple:
- intent: `ARGODRIVE_WIRE=1`, node address, P
- resolution: the engine's resolved line shows the wire device with its measured rate
- effect: `wire_gets > 0` and `bytes_sent` on every link > 0 in the first 60 tokens

An arm that fails any of these is not recorded. This is the rule that caught six
inert configurations; it applies here from the first run.

**5.7 Telemetry.** The 10 ms read monitor gains three columns, one per link, fed
from the host's own byte counts. The barrier trace records `wire` as a device so
`lands_last` and `extra_wait` attribute to it like any drive.

---

## 6. Build order and gates

| Stage | Deliverable | Gate |
|---|---|---|
| **S0** | Simulator: add a device at 4.4 GB/s per link × 3, 1 ms latency, to the layout simulator; replay the recorded routes | Predicted tok/s written down **before** S1 starts |
| **S1** | Node daemon serving from SSD only, one link, `GET`/`DATA` | `argodrive-node` serves 1,000 random records with byte-identical content; measured rate ≈ min(link, SSD) |
| **S2** | Three links, host multipath, split reads | aggregate over three links ≥ 2.5× one link; piece fallback exercised by unplugging one cable mid-run |
| **S3** | Node RAM cache with read-into-slot | hit rate > 0 on a repeated-prompt run; `copied` bytes = 0 |
| **S4** | Wire lookahead (`HINT`/`CANCEL`) | node hit rate rises measurably vs S3 on the same prompt; hints cancelled on route change |
| **S5** | Full arm: GLM-5.3 4-bit, host with internal SSD + wire node, no enclosures | see §7 |

Do not skip S0. Do not start S2 before S1's byte-identity check passes.

---

## 7. Measurement — the arm that decides it

**Configuration of record:** host internal SSD + three-link wire node. No
enclosures. GLM-5.3 uniform Q4_K, the same 434 GB file, host cache as today.

**Control arm:** host internal SSD + **one** enclosure (Green), no wire. Same
prompt, same 200 tokens, same-session A/B/A/B, output md5-identical.

**Pre-registered expectations, written here so they cannot move:**

| Measure | Expected | Falsifier |
|---|---|---|
| Aggregate wire throughput | 9–11 GB/s over three links | < 7 → the links share a controller or the SSD is the ceiling; report which |
| Added latency per remote read | ~1 ms | > 3 ms → protocol or scheduling problem, not physics |
| Decode, wire vs one-enclosure control | wire ≥ 90% of the control | < 75% → the remote tier is not viable on TB4; stop here, keep the code for TB5 |
| Decode, wire vs the four-drive champion (3.70) | 80–85% | informational; not a gate |
| `lands_last` share on the wire device | ≤ its byte share | wire lands last far more than its share → the split or the hints are not working |
| Node hit rate with hints on | materially above hints off | no difference → hints arrive too late; check the lookahead depth |

**Report:** the same table as every arm (tok/s, GB/token, per-device draw, barrier
trace with the wire as a device, node counters), plus the three link rates and
latencies from Gate 1.

---

## 8. What is deliberately out of scope for the prototype

- Node-side routing or compute. The node holds bytes.
- More than one node.
- RDMA. TB4 has no RDMA path on macOS; the protocol is TCP. The TB5 RDMA transport
  is a later swap behind the same `DATA` interface.
- Per-link caches. One cache, any link.
- Encryption on the wire. Point-to-point cable; the shared secret is enough for now.
- Any change to the engine's compute path.

---

## 9. What this becomes

If §7 lands at ≥ 85% of the one-enclosure control on TB4, the same daemon and host
patch, moved to two Mac Studios on Thunderbolt 5 with the transport swapped to RDMA,
is the configuration where a remote RAM tier is *faster* than a local enclosure —
the first arrangement in this project where that ordering holds. That is the
ARGODRIVE cluster, and this prototype is how it gets priced before the hardware is
bought.

If §7 lands below 75%, the code is still the transport layer for that configuration;
only the M1 as a storage node is closed.

---

## 10. Files

```
argodrive/wire/
  SPEC.md                 this document
  BENCH.md                Gate 1 numbers, filled in first
  node/                   argodrive-node daemon
    src/store.rs          record index, pread reader pool
    src/cache.rs          unified RAM arena, read-into-slot, LRU with hint admission
    src/link.rs           per-interface accept loop, framing, pipelined GET/DATA
    src/hints.rs          HINT/CANCEL queue at lower priority than demand
    src/stat.rs           counters, STAT emission
  host/                   patch to the ds4 fork
    wire_device.rs        device type, rate + in-flight, balancer integration
    wire_split.rs         P-piece issue, reassembly into the final buffer, fallback
    wire_hint.rs          lookahead → HINT/CANCEL
    assert_wire.py        the intent/resolution/effect triple
  sim/
    wire_tier.py          S0 — add the wire tier to the layout simulator
  results/
    S0-prediction.md      written before S1
    S5-arm/               the deciding arm, both configurations, traces
```

Reuse: the staging tool for the node's pack layout; the split-read completion
counting; the lookahead prefetch's prediction hook; the assertion harness; the 10 ms
monitor and barrier trace. Nothing here should be a second implementation of
something that already exists in the fork.
