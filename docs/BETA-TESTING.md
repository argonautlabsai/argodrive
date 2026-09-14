# ARGODRIVE 0.2.0 Beta 3 — technical preview

This is an early single-Mac monitoring and saved-run analysis app for Apple
silicon. Python and the hardware sampler are included. Testers do not need Xcode,
Homebrew, Node or a separate Python installation.

ARGODRIVE's next product milestone is guided SSD testing and model-specific
expert-streaming optimisation. This package tests its measurement interface;
it does not yet run drive benchmarks, search engine settings or apply a profile.
Drive tests and configuration experiments currently use separate research
tools; the guided workflow remains to be built.

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
2. Open the app. It starts in **Live Hardware** mode and immediately shows the
   connected drives, memory, and engine activity. Choose **Monitor → Reports
   Only — Stop Collection** when you only want to review saved reports.
3. Choose **File → Choose Run Folder**, or use **Settings → Choose folder**.
4. Select the parent containing experiment folders. Supported data currently comes
   from ds4/deltafin measurement harnesses; ordinary arbitrary engine logs may not
   contain the required summary fields.
5. Open Runs, inspect a result, select two comparable runs and check Compare.
6. Export a CSV or comparison and verify that the native Save dialog works.
7. Choose **Monitor → Live Hardware** to restart collection after Reports Only.
   Closing the last window quits the app and
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
# Beta 2 Cluster preview

This local build adds saved Cluster evidence and experimental native one-link
transfer tools. Open Cluster, check the recorded-session label, refresh the
evidence and export the JSON. It does not start a remote daemon or change model
placement. The bundled measurements are from the development M1/M5 test rig.
Refer to `wire/BENCH.md` for the real 1,000-transfer check and unqualified gates.
This build is ad-hoc signed and not notarized; it has not been published to GitHub.

## Beta 3 model readiness and detailed Live charts

Streaming → Models & readiness distinguishes Kimi K3 on Deltafin, GLM 5.3 on
the Argonaut ds4 fork, and experimental DeepSeek V4.1 Flash on current ds4.
The V4.1 form checks the assembled file size/header and optional enclosure
capacity without starting inference. A successful metadata check does not mean
checksum verification or performance qualification. V4.1 replica streaming is
not implemented in this build. See [DeepSeek preparation](DEEPSEEK41.md).

Live drive charts are stacked full width, with a shared scale, a 10/20/60-second
window, time-weighted average and window peak. Peaks use the actual displayed
aggregation intervals. Reports-only mode does not start the sampler.
