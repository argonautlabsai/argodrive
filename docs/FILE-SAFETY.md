# File safety

Argodrive's app runtime has no file-delete or disk-format feature. Models and imported benchmark results are opened for reading. Removing a replica from the settings draft removes only that configuration entry; it does not remove the model file.

The app creates its own settings, session logs, startup markers, sampler captures and benchmark result folders. These are writes, even though model inputs are read-only. Each Monitor test uses a new folder. Live collection retains earlier captures and exclusively creates a fresh CSV for each sampler process; an existing output path or symbolic link is refused. `K3_SCOPE_CSV`, when supplied, is a filename prefix: a unique suffix is added. Native session logs and startup markers are retained without automatic pruning. File → Show Local Log locates the current session log.

App settings and test-status updates use uniquely created temporary files. Symbolic links, hard-linked files and non-regular destinations are refused. Atomic replacement is restricted by callers to app settings and app-created test metadata, never model inputs or imported run artifacts.

Monitor's local launch profile pins the engine and benchmark helper hashes. Changed helpers fail the worker's preflight; they must be reviewed and explicitly requalified before updating the local profile. Tests use snapshots of the helpers in their new output folder. Stop controls only a test started by this app. Restarting the live sampler does not stop an inference test.

Spotlight controls require an explicit user action and administrator authentication. They change indexing policy for a verified volume; they do not erase its files or delete the Spotlight index. Drive benchmarking uses reads; the app does not write test data to raw devices.

This is application-level protection, not an operating-system sandbox for third-party engines. The app invokes a verified local engine and helpers with the user's permissions. Hashes detect changes; they do not prove an arbitrary binary safe. Developer packaging scripts are outside the running product and can replace their own generated app/ZIP output.

## Verification

`tests/test_file_safety.py` covers existing-file and symlink refusal in the compiled sampler, settings aliases and hard links, retained sentinel contents, changed-helper rejection, and independent sampler/test cleanup. The native sampler check uses a one-second counter collection, with no inference or SSD write workload. `tests/test_test_runner.py` covers launch validation, owned-process cancellation, unrelated-process preservation and a fixture benchmark worker.
