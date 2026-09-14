# Tests from Monitor

Open **Monitor → Test setup**, choose an enabled model profile, select the SSDs
that already contain full model replicas, choose context capacity and generated
tokens, and press **Start test**. Context is capacity, not prompt length. The
profile supplies a fixed prompt. The default local profile is DeepSeek V4.1 Flash Q4 using
the Argonaut ds4 fork and its verified three-drive layout. GLM 5.3 remains available
when its registered replica profile is selected. Merely opening this dialog starts no benchmark or new sampler.

Start launches exactly one arm. Monitor follows its recorded files. **Stop test**
signals only this app's worker, which cleans up the harness, engine and sampler
process group it created. Switching sources does not stop a test. Closing the app
stops its owned worker. Another inference process or cooperating campaign blocks
launch; unrelated downloads and processes are not stopped.

Results live in the configured run folder under `monitor-<time>-<id>`. The arm's
usual log, counters, output and capture files feed the Run library. The request,
exact launch environment, runtime hashes, drive identities and final status are
also retained. Stopped/failed runs remain available, not promoted. These are
interactive diagnostics, not automatically qualified publication results.

## Local launch profiles

Profiles are local machine configuration in `test-profiles.json` in the app state
directory (normally `~/Library/Application Support/ARGODRIVE`). They are not
shipped with personal paths. The JSON object contains a `profiles` array. A
profile has `id`, `label`, `engine_label`, `enabled`, absolute `engine` and
`project` paths, `engine_sha256`, a fixed `prompt`, an `environment` object of
reviewed DS4 flags, and `drives`. Each drive has `id`, `label`, absolute GGUF
`path`, accepted `volume_uuid`, and integer `weight`. Optional `references` maps
output lengths to fixed-prompt reference files. Unknown public API fields and
arbitrary shell commands are rejected; the browser cannot supply executable paths.

The GLM runner freezes the engine/Metal files and harness per run, checks selected
volume UUIDs, physical SSD uniqueness and model stat identity, and validates those
identities again in the harness and after completion. The verified DeepSeek V4.1
profile uses its native `ds4-bench` path and accepts one to three verified model
replicas. Its three-drive configuration is Internal + Green + White with weights
2:1:1, expert split reads, 64 pread workers and read-ahead disabled. The Monitor
worker owns a single campaign lock and stores DS4 evidence below each run's
`evidence/` directory, so a failed launch cannot overwrite an earlier result.
Equal size/stat identity does not prove replica content equality; full replica
checksum qualification is separate. Single-drive mode loads everything from the
selected primary. Multi-drive mode uses configured weights with split reads;
internal gets two subpieces and each enclosure one.
This selection does not create RAID or copy model weights.

Supported contexts: 1K, 2K, 4K, 8K, 16K, 32K. Output lengths: 40, 60, 100, 128,
200, 512. Contexts through 4K retain the local 70 GB cache setting; larger contexts
use the engine's automatic cache budget. Runs are cold at the application-cache
level, greedy, without speculation or a pressure allocation. Existing swap is
allowed but growth above 1 MB fails validation. Every arm has a ten-minute timeout.
