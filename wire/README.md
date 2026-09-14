# ARGODRIVE Wire · TCP pipeline and shared cache prototype

Remote expert **bytes**, with compute retained on the host. The Cluster tab
displays saved qualification evidence. It does not enable remote inference.

Implemented: reproducible route sensitivity replay, C SSD-serving daemon, native
receive-into-destination client, record/span index metadata, authenticated model
identity, read-error recovery, bounded request windows, overlapping reads/sends,
byte-balanced scheduling over independent TCP connections, an optional shared
RAM LRU, cache counters, verification harness and UI evidence export.

Pending: independent three-link qualification, piece-striping and retry
ownership, predictive hints, GGUF tensor-index export,
ds4 balancer/Metal/fallback integration and matched model benchmarks.

## Build and test

On Apple silicon with the Command Line Tools installed, from the repository:

```sh
make -C wire
python3 -m unittest discover -s tests -p test_wire.py -v
```

The resulting `.build/wire/argodrive-node` and `libargodrive-wire.dylib` are native
C. The Mac app remains a Swift shell with the existing local Python backend.
There is no Rust runtime or new Python dependency.

## Repeat the transport sensitivity replay

```sh
python3 wire/sim/wire_tier.py /path/to/route-dump.csv --output /path/to/new-results
```

The default assumptions are explicit in JSON/Markdown. Preserve the committed
September 11 prediction; write later experiments to another output folder. The
source trace is not distributed; its SHA-256 is recorded for verification.

## Isolated node

Create a bounded index of existing records on the node; this reads but does not
modify weights. The initial experiment selected 200 existing K3 experts.

```sh
python3 wire/tools/index_records.py k3 /path/to/experts --limit 200 --output /path/to/session
python3 -c 'import os; p="/path/to/session/secret"; f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(f,os.urandom(32)); os.close(f)'
.build/wire/argodrive-node <bind-address> 9017 /path/to/session/records.index /path/to/session/secret
```

Use a free port and an explicit interface address on a trusted, private link.
Transfer `manifest.json` and the secret to the host through an authenticated
channel; keep the secret mode 600.
Do not commit the secret or `records.index`, which contains machine paths.

```sh
python3 wire/tools/verify_records.py --host <node-address> --port 9017 \
  --manifest /path/to/manifest.json --secret /path/to/secret \
  --library .build/wire/libargodrive-wire.dylib --requests 1000 \
  --output /path/to/S1-identity.json
```

For packed/GGUF sources, `index_records.py spans input.json --output ...` accepts:

```json
[{"layer":4,"expert":0,"spans":[
  {"path":"/absolute/model.gguf","offset":1234,"length":100},
  {"path":"/absolute/model.gguf","offset":5678,"length":100},
  {"path":"/absolute/model.gguf","offset":9012,"length":100}
]}]
```

These offsets are an example, not valid GLM offsets. Obtain actual tensor spans
from the model's existing reader; guessing them invalidates the experiment.

For the read-only device-counter probe on macOS:

```sh
python3 wire/tools/ssd_probe.py /path/to/session/records.index --output /path/to/ssd-probe.json
```

`verify_records.py` samples with replacement using a recorded seed. It reports
both verified requests and distinct records, transfer-only and hashing-inclusive
rates. It retains failures. A matching digest is not a model quality test.

## Cluster evidence

`monitor/cluster-evidence.example.json` is the empty, schema-safe reference
shipped with the preview. To show a local session, place a schema-1
report at `<state-dir>/wire/status.json`; refresh the Cluster tab. `/cluster`
only reads that evidence and never probes or starts a remote process. Hardware
reports and raw captures stay outside the repository.

See [BENCH.md](BENCH.md), [PROTOCOL.md](PROTOCOL.md) and the unmodified [SPEC.md](SPEC.md).

## Queued transfers and RAM cache

See [PIPELINE.md](PIPELINE.md) for queue bounds, cache ownership, telemetry,
transport API and the matched two-cable campaign. The cache stays disabled by
default. This is an opt-in transport prototype, not an enabled inference tier.
