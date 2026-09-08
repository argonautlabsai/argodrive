#!/usr/bin/env python3
"""Validate that an arm's INTENDED config actually resolved in the engine.

  k3-assert-config.py <log> KEY=VAL [KEY=VAL ...]     -> exit 0 pass / 1 FATAL

Written 2026-08-29 after a 10-arm block silently ran READ8/PF8 because
per-arm settings were passed as an `env` prefix, which the rung script's own
line-30 defaults then clobbered. Resolution is checked against the engine's
`[config] resolved:` line, never against what the harness believed it sent.
"""
import re, sys

# env var -> field name inside the engine's `[config] resolved:` line
FIELD = {
    "K3_EXPERT_READ_THREADS":        "expert_read_threads",
    "K3_EXPERT_PREFETCH_THREADS":    "expert_prefetch_threads",
    "K3_SPEC_DEPTH":                 "spec_depth",
    "K3_EXPERT_PIN_GB":              "expert_pin_bytes",
    "K3_PROVIDER_RESIDENT_LAYERS":   "provider_resident_layers",
    "K3_PILOT_GATE":                 "pilot_gate",
    "K3_ORACLE_DEPTH":               "oracle_depth",
    "K3_DSPARK":                     "dspark",
    "K3_UAG_DRAFT":                  "qwen",
}


def resolved(text):
    m = re.search(r"\[config\] resolved:(.*)", text)
    if not m:
        return None
    out = {}
    for tok in m.group(1).split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def main():
    log, pairs = sys.argv[1], sys.argv[2:]
    try:
        text = open(log, errors="ignore").read()
    except OSError as e:
        print(f"FATAL: cannot read {log}: {e}")
        return 1
    r = resolved(text)
    if r is None:
        print("FATAL: no '[config] resolved:' line — engine never started")
        return 1
    bad = []
    for p in pairs:
        if "=" not in p:
            continue
        k, want = p.split("=", 1)
        field = FIELD.get(k)
        if not field or field not in r:
            continue                      # not independently checkable
        got = r[field]
        # pin is reported in bytes; compare in GiB
        if k == "K3_EXPERT_PIN_GB":
            got = str(round(int(got) / 1e9)) if got.isdigit() else got
        if got.lower().lstrip("0") not in (want.lower().lstrip("0"), want.lower()):
            if not (want.lower() in ("on", "1") and got.lower() in ("on", "true")) and \
               not (want.lower() in ("off", "0") and got.lower() in ("off", "false")):
                bad.append((k, want, got))
    if bad:
        print("FATAL: CONFIG MISMATCH")
        for k, want, got in bad:
            print(f"  expected {k}={want}")
            print(f"  resolved {FIELD[k]}={got}")
        print("RUN INVALID — NOT RECORDED")
        return 1
    print(f"config OK ({len(pairs)} intents checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
