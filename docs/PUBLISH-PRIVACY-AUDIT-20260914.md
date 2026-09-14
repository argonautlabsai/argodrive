# Publication privacy audit — 14 September 2026

This is a pre-publication audit of the tracked working trees for Argodrive, the local ds4 fork, and the Argonaut Labs website. It does not inspect or publish model weights, raw sampler logs, generated responses, or `.build` output.

## Result

The website tree is clean of machine paths, private addresses, credential values, and personal email addresses. Argodrive's own commits use the GitHub noreply identity. The ds4 history contains upstream contributor identities, but no personal address from the Argonaut Labs account was found in its commit history.

The repositories are **not ready to publish as their current working trees**. The
Argodrive test and app gates are green, but the public decision is based on the
curated allowlist in
[`PUBLIC-FILE-MANIFEST-20260914.md`](PUBLIC-FILE-MANIFEST-20260914.md). The
following tracked or staged candidates need to be excluded or sanitized first:

- Argodrive's older K3 scripts and examples contain user-specific `/Users/...` and `/Volumes/...` paths. Replace them with environment variables or generic examples before staging those files.
- The ds4 tree's `QA_BEFORE_RELEASES.md` and `docs/DISTRIBUTED.md` contain private test-network addresses and operational host examples. Remove or replace them with documentation-only placeholders.
- The ds4 tree's conversion notes, generated steering output, and long-context security fixture contain local filesystem and tool-environment examples. Exclude those fixtures from a public release or sanitize them deliberately; they are not needed to reproduce the DeepSeek V4.1 benchmark.
- The ds4 working tree has untracked backup/object files and a local benchmark CSV. Do not stage backups, generated objects, model files, raw logs, or local benchmark captures.
- Argodrive's wire documentation describes shared-secret files and test endpoints. The source contains no literal secret, but public instructions must never include a real secret file or endpoint.

A second pass is required after creating the clean publication branch. The final pass must scan the exact staged file list and the complete commit diff, not the whole development checkout.

The latest gate rerun found no credentials or private keys in the selected public
pack. It did find the same development-only paths and network examples listed
below; they remain outside the allowlist. The current working tree is therefore
safe to review, but not safe to stage indiscriminately.

The expanded allowlist scan (94 text files) found zero literal `/Users/<user>`, `/Users/<node-user>`,
named reference-volume paths, private test-network addresses, non-Argonaut email
addresses, private-key markers or credential assignments. It reports only generic
documentation placeholders such as `/path/to/model.gguf` and the code's portable
`/Volumes/<label>` lookup. The external trace fallback in `monitor/k3-live.py`
now uses the opt-in `K3_EXTERNAL_TRACE_ROOT` environment variable instead of a
reference-machine mount point.

## Safe public contents

The public pack may contain the redacted numerical summary, model and engine revision identifiers, prompt and model SHA-256 values, hardware class (M5 Max, 128 GiB), storage roles (internal, Green, White), benchmark commands with placeholder paths, and the stated limitations. It must not contain absolute paths, serial numbers, private IPs, hostnames, usernames, passwords, API-key values, SSH material, raw responses, or full model files.

The public contact address is `benchmarks@argonautlabs.ai`. No personal contact address is used by the website draft.

## Reproduction of this audit

The scan covered tracked files from each repository, Git author identities, and the exact files selected for the public website. Pattern checks covered home and volume paths, RFC1918/link-local addresses, serial-like identifiers, private-key markers, credential assignments, and email addresses. Code identifiers containing words such as `token` or `secret` were reviewed as source symbols and were not treated as credentials without a value.
