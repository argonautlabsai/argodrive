#!/bin/sh
# K3 identity gate — reproducible, with the invocation RECORDED (the original
# k3_identity_reference.txt's generating prompt was never written down; this
# v2 gate fixes that). Created 2026-08-18 .
#
# Reference: k3_identity_reference_v2.txt = 40-token greedy raw continuation of
# the prompt below, champion env (full spine residency, DSpark on, route-async
# on, MLA Metal kernel OFF), verified byte-identical across: old binary
# (2026-08-18) / clang-21 rebuild, kernel on/off, DSpark on/off.
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
unset K3_MLA_METAL_ATTENTION
DF=$ROOT/engines/deltafin/target/release/deltafin
OUT=/tmp/k3_identity_check.log
caffeinate -is "$DF" run --model-root /Volumes/K3A/deltafin-root --stats --max-new 40 \
  --prompt "The three main financial statements are" > "$OUT" 2>&1 || { echo "RUN FAILED"; tail -5 "$OUT"; exit 2; }
grep -vE "^\[|^deltafin:|^$" "$OUT" | tr -d '\n' > /tmp/k3_identity_check_text.txt
if cmp -s /tmp/k3_identity_check_text.txt "$ROOT/k3_identity_reference_v2.txt"; then
  echo "IDENTITY: BYTE-IDENTICAL (v2 reference)"
  exit 0
else
  echo "IDENTITY: MISMATCH vs k3_identity_reference_v2.txt"
  echo "--- got ---";      cat /tmp/k3_identity_check_text.txt; echo
  echo "--- expected ---"; cat "$ROOT/k3_identity_reference_v2.txt"; echo
  exit 1
fi
