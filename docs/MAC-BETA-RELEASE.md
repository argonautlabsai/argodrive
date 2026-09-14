# Mac technical-preview release

The beta uses a native Swift/AppKit window with WKWebView, a bundled Python
backend and the existing C/IOKit sampler. Xcode is the build toolchain; Python is
part of the shipped runtime. Users install neither of them.

The initial release scope is one Mac: reports, comparisons, diagnostics and
opt-in hardware collection. Cluster/RDMA functionality remains planned.

This is a packaging and measurement technical preview of the broader
expert-streaming optimiser. The next local milestone is guided SSD calibration,
model/workload-specific configuration tests and validated profile export, before
cluster work. It is not included in the current app; see
[STREAMING-OPTIMIZER.md](STREAMING-OPTIMIZER.md) for its acceptance criteria.

## Build from source

On an Apple-silicon build Mac with Xcode and Python 3.12:

```sh
python3.12 -m venv .build/packaging-py312
.build/packaging-py312/bin/python -m pip install -r macos/build-requirements.txt
python3 scripts/build-macos.py
python3 scripts/smoke-macos.py dist/ARGODRIVE.app
```

The build selects the installed Xcode compiler/SDK through the child process
`DEVELOPER_DIR`; it does not change the machine's `xcode-select` preference.
The current developer machine has an older command-line Swift compiler beside a
new SDK, so using the matched Xcode toolchain is necessary.

Outputs are an arm64 `.app`, a ZIP and its SHA-256 file under ignored `dist/`.
`--output` selects a separate staging folder. `--python` selects the build venv.
No run data, user settings or weights are bundled. Python and PyInstaller notices
are included. Build information records interpreter, dependencies and commit.

Settings and backend logs are written to Application Support, never into the
signed/read-only bundle. The native process selects an ephemeral loopback port,
checks the child's ready-file PID, and owns its shutdown. A backend also checks
its parent PID so a forcibly closed native process does not leave it indefinitely
running. The source dashboard's independent server is never adopted or stopped.

## Signing and public distribution

The owner does not yet have Apple Developer Program membership. The generated
technical preview is **ad-hoc signed**, not Developer ID signed or notarized.
`codesign --verify` checks bundle integrity; it does not establish Gatekeeper
acceptance. Label the download accordingly and use a small technical test group.
Do not promise that managed Macs will permit running it.

Developer ID and notarization are benefits of the paid program.
[Apple membership comparison](https://developer.apple.com/support/compare-memberships/)

For the normal public-download experience, enroll, obtain a **Developer ID
Application** certificate, rebuild with `--sign 'Developer ID Application: …'`,
submit the signed app to Apple's notarization service, staple the ticket and
recreate/verify the ZIP and checksum. An Apple Development certificate is not a
substitute for Developer ID distribution. Enrollment/notarization completion by
tomorrow has not been established.
[Apple notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

No account enrollment, certificate creation, permission downgrade, notarization
submission, GitHub upload or release publication is performed by the build script.

## Before sharing the technical preview

- Verify the app opens from the extracted ZIP on a second Apple-silicon Mac with
  no Python/Homebrew setup. Record that Mac's actual macOS version.
- Use the native folder picker with real supported logs; compare two runs and
  save CSV and Markdown exports using the native Save dialog.
- Verify reports mode starts no sampler; verify opt-in live collection and
  stopping/restarting it. Quit and check that the backend/sampler stop.
- Check window layout, keyboard navigation, light/dark appearance, missing files,
  unmounted drives, and recovery after backend failure.
- Review release notes and include the supported formats, known limitations,
  signing status, issue link and checksum. Do not ship personal benchmark logs.

See [BETA-TESTING.md](BETA-TESTING.md) for tester instructions. A future signed
release still needs its own download/quarantine/Gatekeeper test; an unquarantined
local launch does not establish that result.
