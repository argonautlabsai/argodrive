# Kimi K3 / Deltafin streaming profile

Argodrive now carries the storage method used by the `argonautlabsai/deltafin`
fork as a reviewable Kimi profile. It is separate from the DeepSeek and GLM
`DS4_*` profiles.

Deltafin's method keeps complete, byte-identical K3 expert copies on several
volumes. The native reader can split a request into weighted pieces, balance
duplicate homes by tier and expected completion, and run bounded demand and
prefetch pools. This is application-level replica scheduling; it is not RAID0
and it does not change K3 routing or model precision.

The candidate settings are visible in the Model support view and can be printed
without starting an engine:

```bash
python3 scripts/kimi-deltafin-profile.py \
  /absolute/path/to/deltafin-root \
  /absolute/path/to/dir-b-experts \
  /absolute/path/to/hot-experts \
  /absolute/path/to/dir-c-experts --shell
```

Replica arguments are positional: `K3_EXPERT_DIR_B`, `K3_EXPERT_HOT_DIR`, then
`K3_EXPERT_DIR_C`. The command only inspects paths and filesystem device IDs;
it never copies, deletes or launches anything. Resolve physical SSD identity,
full replica hashes and shared Thunderbolt uplinks before running a benchmark.

The fork's published ladder reports roughly 52%, 73% and 90% of four-drive
speed for one, two and three drives. Those are reference results, not a claim
about another machine. The Argodrive replay of the recorded Kimi route traces
estimated a 12.2% barrier-time improvement for a 28 GiB hot band under the
full-replica policy. It is a placement simulation, not a token/s measurement.

For a live qualification, run interleaved one-, two- and three-drive arms with
the same prompt, length, binary and cache state. Record `K3_*` settings,
per-drive bytes and output identity in Recorded arms. Keep the profile disabled
until the candidate beats the matched control repeatedly.
