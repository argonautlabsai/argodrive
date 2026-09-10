# ARGODRIVE 0.2.0 Beta 1 — technical preview

This is an early single-Mac monitoring and saved-run analysis app for Apple
silicon. Python and the hardware sampler are included. Testers do not need Xcode,
Homebrew, Node or a separate Python installation.

The build targets macOS 14 or later on Apple silicon. Only the development Mac
has been used for build checks; clean-install testing on other Macs is required.
RDMA, shared expert RAM and cluster inference are planned, not included.

## Distribution status

The initial technical-preview build is ad-hoc signed. It does not have a Developer
ID signature and has not been notarized. macOS may prevent it from opening.
This package has not received Apple's notarization checks. Do not describe it as
a trusted, signed public beta. Managed Macs may prohibit running it.

Apple documents the per-app choice under System Settings → Privacy & Security
for software a user has decided to trust. We do not disable Gatekeeper or remove
quarantine automatically. See [Apple's opening-apps guidance](https://support.apple.com/102445).

## Install and try it

1. Download and unzip the technical preview. Move `ARGODRIVE.app` to Applications.
2. Open the app. It starts in **Reports only** mode; no inference or sampler starts.
3. Choose **File → Choose Run Folder**, or use **Settings → Choose folder**.
4. Select the parent containing experiment folders. Supported data currently comes
   from ds4/deltafin measurement harnesses; ordinary arbitrary engine logs may not
   contain the required summary fields.
5. Open Runs, inspect a result, select two comparable runs and check Compare.
6. Export a CSV or comparison and verify that the native Save dialog works.
7. Optionally choose **Monitor → Live Hardware** to start local hardware collection.
   **Monitor → Reports Only** stops it. Closing the last window quits the app and
   stops the backend and its sampler. No inference engine is started or stopped.

Example folder layout:

```text
arms/
  experiment-name/
    baseline.log
    baseline.csv           optional sampled counters
    baseline.map           optional physical-device identities
    baseline.sys           optional memory measurements
    baseline.md5           optional output fingerprint
    candidate.log
```

`.readtrace.csv`, `.hotlist`, `.expert.json` and router `.jsonl` artifacts add
diagnostic views. No model weights or personal run data are included in the app.

## Useful feedback

Please record your Mac model, macOS version, app version and what you expected
versus what happened. Check launch/quit, folder selection, comparison, export,
light/dark appearance, drive discovery and missing-data states.

Use File → Show Local Log for backend startup errors. The app does not upload
logs or telemetry. Review files for private paths/prompts before sharing them.
Report issues at https://github.com/argonautlabsai/argodrive/issues.

Settings and the two most recent backend logs live in:
`~/Library/Application Support/ARGODRIVE/`.

## Known limits

- Default memory/drive discovery and token telemetry need validation on more Macs.
- ds4 live response chunks are labelled chunks/s; they are not established as
  tokenizer tokens. Recorded harness speed and live progress have different sources.
- An uncalibrated drive has no capacity percentage. Missing data remains missing.
- Closing/pausing only a browser page does not stop an independently launched CLI
  sampler. Use the native app's mode menu or quit its app instance.
- The app currently supports one local workspace. No automatic updates, cloud
  service, model download or cluster management is included.
