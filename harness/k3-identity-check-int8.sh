#!/bin/sh
# K3 identity gate for the INT8-DIRECT loop recipe (Step 6, user-approved
# 2026-08-21). Reference: k3_identity_reference_int8_v1.txt = 40-token greedy
# raw continuation of the prompt below, champion env + full loop stack
# (K3_KDA_LOOP=on K3_ROUTE_SIDEQUEUE=on K3_KDA_SHARED_BOUNDARY=1
# K3_KDA_PRECOMMIT=1 K3_KDA_INT8_DIRECT=1, DSpark on). int8-direct differs
# from the fp32-view path at reassociation level (measured 3.1e-07 relL2 per
# layer), so this is a SEPARATE recorded reference; the v2 fp32 gate remains
# authoritative for the default (flags-off) engine.
#
# Exit 0 = byte-identical, 1 = mismatch (prints diff), 2 = run failed.
set -u
ROOT=$K3_DIR
export DELTAFIN_ROOT=/Volumes/K3A/deltafin-root
export K3_EXPERT_DIR_B=/Volumes/K3B/deltafin-root-b/k3-experts
export K3_SPINE_RESIDENT_GB=16 K3_PROVIDER_RESIDENT_LAYERS=93
export K3_HOST_RESERVE_GB=8 K3_SPINE_LOAD_RESERVE_GB=8
export K3_EXPERT_HOT_DIR=$ROOT/k3-experts-hot
# Watermark: default 0.70 (recipe parity) but honour an outer override —
# retention-ON gates (K3_EXPERT_RETAIN_GB=14 holds ~13.9 GiB on the MPS
# pool) OOM under 0.70 and must run at 0.80 like every ladder arm
# (2026-08-26; wm alone is allocator-only, numerics unaffected).
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=${PYTORCH_MPS_HIGH_WATERMARK_RATIO:-0.70}
export PYTORCH_MPS_LOW_WATERMARK_RATIO=${PYTORCH_MPS_LOW_WATERMARK_RATIO:-0.5}
export K3_MEMORY_PATIENCE_SECONDS=300
export K3_DSPARK=on K3_ROUTE_ASYNC=1
export K3_KDA_LOOP=on K3_ROUTE_SIDEQUEUE=on
export K3_KDA_SHARED_BOUNDARY=1 K3_KDA_PRECOMMIT=1 K3_KDA_INT8_DIRECT=1
# CB-fusion pair joined the recipe 2026-08-22 (user-approved promotion);
# proven byte-identical against this same reference before promotion, so
# the recorded reference file is unchanged. This gate now regression-tests
# the engine exactly as the recipe runs it.
export K3_CB_FUSION=1 K3_LOOP_CAT=1
# Speculative governor (promoted 2026-08-23, 51e5e13) — recipe parity;
# inert on this 40-token raw run (no drafting, window never fills).
export K3_SPEC_MIN_TRAILING_ACCEPT=20
export K3_EXPERT_PREFETCH_GENERATIONS=4
unset K3_MLA_METAL_ATTENTION
DF=$ROOT/engines/deltafin/target/release/deltafin
OUT=/tmp/k3_identity_check_int8.log
caffeinate -is "$DF" run --model-root /Volumes/K3A/deltafin-root --stats --max-new 40 \
  --prompt "The three main financial statements are" > "$OUT" 2>&1 || { echo "RUN FAILED"; tail -5 "$OUT"; exit 2; }
grep -vE "^\[|^deltafin:|^$" "$OUT" | tr -d '\n' > /tmp/k3_identity_check_int8_text.txt
if cmp -s /tmp/k3_identity_check_int8_text.txt "$ROOT/k3_identity_reference_int8_v1.txt"; then
  echo "IDENTITY: BYTE-IDENTICAL (int8 v1 reference)"
  exit 0
else
  echo "IDENTITY: MISMATCH vs k3_identity_reference_int8_v1.txt"
  echo "--- got ---";      cat /tmp/k3_identity_check_int8_text.txt; echo
  echo "--- expected ---"; cat "$ROOT/k3_identity_reference_int8_v1.txt"; echo
  exit 1
fi
