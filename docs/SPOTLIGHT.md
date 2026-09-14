# Spotlight controls

Implemented locally, 12 September 2026. No indexing setting is changed by
installing, opening or scanning with ARGODRIVE.

Open **SSDs → Spotlight indexing → Check drives**. Each mounted volume shows
its name, mount point, physical disk when resolved, indexing policy and the raw
macOS status response. Newly attached drives are included on the next check.
Multiple APFS volumes on a physical disk have independent indexing policies;
aliases of the same mount point are shown only once.

For a volume with a confirmed policy, choose **Turn indexing off** or **Turn
indexing on**. A review dialog states the selected volume and effect on search.
macOS asks for administrator authentication; passwords never pass through the
web UI or Python server. Disabling applies `mdutil -i off` to only that volume.
It persists until changed, and existing files and indexes are retained. Re-enabling
can generate background disk activity. There is no all-drives action, index
deletion, reindex operation, or background privileged helper.

Indexing **on** means it is allowed. It does not measure active Spotlight I/O,
prove a benchmark is affected, or establish a performance gain. Compare otherwise
matched runs to measure the effect. Search-and-indexing-disabled, unsupported,
permission-denied and unavailable-service states are shown without a toggle.
In particular, `Spotlight server is disabled` is **unavailable**, not proof of a
per-volume policy. The local development sandbox can prevent DiskManagement or
Spotlight access; run the app normally to inspect real settings in that case.

## Behaviour and evidence

- Only an explicitly requested scan runs `diskutil info` and `mdutil -s`.
  Checks are not part of the 100 ms hardware sampler. GET `/spotlight` reads
  saved in-process evidence and has no hardware side effects.
- Authenticated POST `/spotlight/scan` checks current volumes. POST `/spotlight`
  accepts a scanned volume ID and a boolean `enabled`; arbitrary paths or shell
  commands are not accepted. Both use the app's localhost Host, Origin and CSRF
  checks.
- A change runs asynchronously. Before requesting privileges, it rechecks the
  volume UUID, mount point and physical device. The bounded shell command checks
  the UUID again after authentication, protecting against a swapped drive while
  the dialog is open. Path text is quoted independently for shell and AppleScript.
- The app reads back `mdutil -s` after the command. Exit zero alone is insufficient
  for success. Cancellation, timeout, disconnection and failed verification stay
  distinct. A timeout requires a fresh check before another decision.
- Completed attempts append `spotlight-changes.jsonl` in the app's state directory,
  including previous/requested/observed policies, volume identity and outcome.
  This file contains local mount paths, never passwords.
- The source Mac build includes the new view module automatically. Previously
  packaged beta files and GitHub releases are not updated by this source change.

Validation uses the installed macOS `mdutil(1)` contract, policy parser tests,
HTTP authorization tests, escaped-path checks, five-volume rendering and mocked
administrator success/cancel/error/timeout paths. No real indexing toggle is
performed by the automated tests; the native authentication dialog and actual
indexing transition still require a normal macOS app run.
