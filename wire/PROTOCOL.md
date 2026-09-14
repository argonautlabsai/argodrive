# Wire implementation contract · experimental revision 2

The supplied v1 specification is preserved in `SPEC.md`. This document records
the concrete S1 contract and differences found during source review.

## Identity and source layout

A logical expert is identified by **index identity + layer + expert**. The GLM
GGUF path reads gate, up and down from three separate spans. It is not safe to
assume their bytes occupy one contiguous source range. `index_records.py spans`
indexes those ranges in logical gate/up/down order without repacking the model.
For K3, `index_records.py k3` indexes existing `L*-E*.bin` records.

`records.index` is metadata, not a second weight format. Its first line is
`AWRINDEX2<TAB><64 lowercase hex identity>`. Remaining lines are
`layer<TAB>expert<TAB>offset<TAB>length<TAB>absolute source path`. Adjacent lines
with the same key form one logical record. Tabs/newlines in paths are rejected.
The identity is SHA-256 of the canonical sorted manifest containing each record's
layer, expert, length and SHA-256. Obtain the manifest through authenticated SSH,
not the unauthenticated data connection. Weight sources must remain immutable
while serving. They are read-only descriptors; this does not prevent other
processes from modifying the files. Full content hashing occurs in qualification,
not on each daemon read.

Current limits: 131,072 records, 240 source files, 16 spans per record, 64 MiB per
record, eight connections. The bounded source count fits the node's current 256
descriptor limit. An adapter for a full many-file K3 store needs descriptor
management before promotion. A full GLM GGUF uses far fewer source descriptors,
but its tensor-index exporter and engine integration are still pending.

## Framing

The original 24-byte little-endian header is retained:

`AWR1 | type:u8 | flags:u8=0 | reserved:u16=0 | layer:u32 | expert:u32 | piece:u32 | length:u32`

Revision 2 is intentionally incompatible with the proposed v1 payloads. The
HELLO version makes this explicit. The v1 text required duplicate rejection by
sequence number without defining a sequence field. Every GET response now echoes
a 64-bit request ID. Integers below are little-endian; hashes and secrets are raw bytes.

| Type | Value | Payload |
| --- | ---: | --- |
| HELLO | 1 | version:u32=2, P:u32=1, index_identity:32 bytes, shared_secret:32 bytes |
| HELLO_OK | 2 | version:u32=2, P:u32=1, record_count:u64, cache_budget_bytes:u64 |
| GET | 3 | request_id:u64, strictly increasing and nonzero per connection |
| DATA | 4 | request_id:u64 followed by the logical record bytes |
| NAK | 8 | request_id:u64 followed by a bounded UTF-8 reason |
| STAT | 7 | request: request_id:u64; response: request_id:u64 followed by bounded JSON counters |

HELLO headers use layer=expert=0 and piece=0xffffffff. GET/DATA/NAK use piece=0
and echo layer/expert. Unsupported types, bad reserved fields, duplicate request
IDs, wrong lengths and oversized frames close the connection. Unknown records
and actual short reads return NAK and allow the same connection to continue.
Handshake failure returns a generic NAK and closes. Secrets must be private
32-byte files (no group/other permissions), never command-line values or logs.

S1 negotiates **P=1 only**. S2 must freeze P and boundaries for an entire session
epoch. A dropped link must not change piece offsets. A request ID is necessary
but not sufficient for retries: the engine must give each attempt exclusive
ownership of its destination range before late data can be discarded safely.

## Buffer and lifecycle rules

The node preads into a referenced cache entry or a reusable private response
slot. Each connection has one reader and one sender with 1–4 bounded slots,
allowing reads to overlap sends. Up to eight connection workers share up to
three explicit IPv4 listeners and a single optional LRU. This is not the proposed
16-worker demand/hint pool. STAT requests return counters; there is no periodic
STAT stream or hint queue. Cache entries are retained until all sends release
their references; partial source reads never publish an entry. See PIPELINE.md.

The host native library receives DATA directly into the caller's destination.
There is no payload memcpy in that code path. Socket/kernel copies still occur.
Metal alignment and wrapping are the engine adapter's responsibility and are not
implemented here. The synchronous API has one outstanding request. The batch API supports a
window of 1–8 per connection, with a transport-wide maximum of 32. Responses
remain ordered and each destination must be exclusive for the entire batch.

NAK means the destination is invalid. Transport, length or identity errors also
invalidate it, and the Python adapter closes the connection before returning.
The direct C API's caller must close on -1 and discard/overwrite the entire
destination before fallback. No background retry can keep writing after return.
Local engine fallback is a future adapter; the library does not silently claim
to perform it. SIGTERM stops admission, finishes current reads/responses and
joins workers; idle clients have bounded socket timeouts.

The prototype binds only explicit IPv4 addresses, not 0.0.0.0. It does not install
a service, change interface addresses, change wired-memory limits or use LAN
fallback automatically. TCP/shared-secret operation is for the controlled
Thunderbolt test network; this is not an encrypted public-network service.

## Qualification corrections

- Preserve failed arms with a failure reason; exclude them from promoted results.
  Do not delete an arm because intent/resolution/effect checks failed.
- A cold node is limited by its one SSD as well as the links. A 9–11 GB/s target
  needs independently measured links and sufficient RAM hits or faster backing.
- F_NOCACHE does not establish physical SSD throughput by itself. Compare
  application bytes with device-counter deltas and record cache state.
- The supplied S5 text says both 85% and 90% of the control. Use the stricter
  **90% promotion target**, retain the 75% stop threshold, and record 75–90% as
  unqualified pending investigation. Do not move the threshold after testing.
- RDMA and multi-node execution remain future transports, not current features.

The queued GET and cache extensions preserve revision-2 framing. STAT uses
layer=expert=piece=0 and the same monotonic sequence space as GET; it does not
increment the GET count. Old revision-2 servers do not support STAT and close
on it. Use the matching tested binary for telemetry. HELLO_OK now advertises
the configured cache budget in its existing last eight bytes; old clients can
ignore it. A queued NAK invalidates the entire host batch and closes that
connection before return. TCPTransport waits for all other writers before
raising, so the caller can safely discard/reuse its buffers.
