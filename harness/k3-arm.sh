#!/bin/sh
# k3-arm.sh — one measured arm on the CURRENT champion env.
#
#   k3-arm.sh <outdir> <tag> <tokens> <chat 0|1> <prompt> [KEY=VAL ...]
#
# Rebuilt 2026-08-31 after the CS_KILLED panic wiped the previous session's
# scratchpad copy. The base env below is the champion of record from
# K3-FULL-HANDOVER-2026-08-30.md lines 55-80, cross-checked field by field
# against the `[config] resolved:` line of the 2026-08-31-resume arms — the
# most recent known-good invocation on this host.
#
# NOT derived from k3-run-recipe.sh or k3_fusion_rung.sh: both carry the stale
# pre-08-28 base (READ16/READ8, k3-experts-hot, MPS watermark 0.70) and would
# silently measure a different engine.
#
# The drafter pair (K3_QWEN_ROUNDTRIP_SOFT / K3_QWEN_REARM) is deliberately NOT
# in the base. It is unpromoted: measured +19.0% on the financial-statements
# prompt but -14% to -25% on p2/p3/chat (2026-08-31-resume). Pass it as an
# explicit override on any arm that wants it.
set -u
ROOT=$K3_DIR

if [ $# -lt 5 ]; then
    echo "usage: k3-arm.sh <outdir> <tag> <tokens> <chat 0|1> <prompt> [KEY=VAL ...]" >&2
    exit 2
fi
OUTDIR=$1; TAG=$2; TOKENS=$3; CHAT=$4; PROMPT=$5
shift 5

# ---- champion env of record ------------------------------------------------
export DELTAFIN_ROOT=$K3_DIR/deltafin-root-local   # PROMOTED 2026-09-03: 4h-argu (usage-weighted 4-drive Argonaut)
export K3_EXPERT_DIR_B=/Volumes/Green/k3-experts-full          # PROMOTED 2026-09-06 (mirror + balanced plan path): Green holds all 82,432 as the second base; census = primary ∪ dir_b trivially complete
export K3_EXPERT_DIR_C=/Volumes/Yellow/serve3-green            # PROMOTED 2026-09-06: Yellow band WIDENED to 26,684 files (66.5% of recorded reads; +11,397 from Green); rollback in k3-layout-snapshots/2026-09-06-1534-wider-*
export K3_EXPERT_HOT_DIR=/Volumes/White/k3-experts-b          # PROMOTED 2026-09-06: White hot band WIDENED to 50,265 files (88.9% of recorded reads; +14,247 usage-ranked from Green on the freed space) — +5.9% at 200 tok (1.0515/1.0558 vs 0.9949/0.9976)
# I/O concurrency — the biggest single win
export K3_EXPERT_READ_THREADS=64 K3_EXPERT_PREFETCH_THREADS=8   # PROMOTED 2026-09-06: 12 → 8 prefetch threads = +2.0% at 200 tok (1.1260 vs 1.1044, uncovered wait 70.9 → 58.6 s), +2.2% at 60; the five-knob stack was +1.9% and this knob carries it
export K3_EXPERT_PREFETCH_GENERATIONS=4
# drafting
export K3_UAG_DRAFT=on K3_DSPARK=off K3_NGRAM_DRAFT=off
export K3_SPEC_DEPTH=8 K3_QWEN_PREFIX_COMMIT=1
# memory
export K3_SPINE_RESIDENT_GB=16 K3_PROVIDER_RESIDENT_LAYERS=93
export K3_HOST_RESERVE_GB=8 K3_SPINE_LOAD_RESERVE_GB=8
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.95 PYTORCH_MPS_LOW_WATERMARK_RATIO=0.5
export K3_MEMORY_PATIENCE_SECONDS=300
# engine
# K3_MIRROR_SCHED=1 promoted into the base env 2026-08-31. It is the FOUR-WAY
# per-device scheduler (storage.rs: four clocks at 1502/2478/3122/3025 us,
# load-sorted probe order), not the old two-way internal-vs-enclosures clock.
#
# Evidence for promoting it despite measuring null:
#   - 4-arm ABBA with one home per expert: -0.46%, inside the noise band, and
#     output BYTE-IDENTICAL across all four arms (md5 978217a9). It cannot
#     regress a layout where each expert has exactly one home, because the
#     probe order is a permutation and resolution stays total.
#   - It makes the per-device split observable for the first time
#     ([mirror-split]); the previous mirror_schedule_split() was dead code.
#     Measured 39%/23%/19%/19% against capability shares 38.7/23.5/18.6/19.2.
#   - It is the prerequisite for any replicated layout. Replication under the
#     OLD two-way clock measured -22.1% (every enclosure-side candidate
#     resolved on the first probe, so K3C absorbed everything).
#
# CAVEAT for cross-block work: arms recorded BEFORE this date ran with the
# scheduler off. The delta is null, but they are not the same configuration.
# REVERTED 2026-08-31, same day it was promoted. It went in on a 40-token ABBA
# reading -0.46%, which a review then showed was NOT a null control: the
# overlay chain (hot 33,580 + dir_b 41,216 + dir_c 20,516 files against 82,432
# expert pairs) already overlaps, so the scheduler had choice and moved 42 GB
# between devices — off the two fast drives onto the two slow ones, the same
# direction as the losing replication arm. At 200 tokens the amplitude looks
# larger: controls WITH it average 0.5754 against 0.6055 for five same-day arms
# without it. That is confounded with drift and needs its own 200-token ABBA,
# but promoting on a misread null was premature, so it comes back out until
# measured properly at the champion rung.
# export K3_MIRROR_SCHED=1
export K3_ROUTE_ASYNC=1 K3_KDA_LOOP=on K3_ROUTE_SIDEQUEUE=on
export K3_KDA_SHARED_BOUNDARY=1 K3_KDA_PRECOMMIT=1 K3_KDA_INT8_DIRECT=1
export K3_TRUE_ROUTE_TOPUP=1 K3_PILOT_EARLY=1 K3_CB_FUSION=1 K3_LOOP_CAT=1
# K3_SPINE_FP32_ARENA=0 PROMOTED 2026-09-02 (operator: "promote"). Skips the
# per-layer int8->fp32 execution-arena materialize (~2.3 GB of GPU writes per
# layer per token) that no consumer on this host needs — found by the
# attention-timer split (K3-ATTENTION-SPLIT-RESULTS-2026-09-02.md). Evidence:
# byte-identical text at 60 and 200 tokens; 200-tok A/C/A/C bracket on the
# 3-drive rig +8.23% fused / +8.25% steady (0.5962/0.6125 vs 0.5509/0.5658),
# pair spreads <1%. Arms recorded BEFORE this line ran with the arena ON.
# The 4-drive champion layout has not been re-measured with it yet.
export K3_SPINE_FP32_ARENA=0
# DRAFTER-SURVIVAL PAIR PROMOTED 2026-09-02 (operator: "once we get
# champion promote it"). Fixes the two latches in series that killed the Qwen
# drafter at generated token ~117 on the champion prompt (memory:
# drafter-death-two-latches): the round-trip guard returns an empty proposal
# instead of a hard error, and the policy re-arms after 4 steps. Evidence on
# the arena-off engine, 3-drive rig, 200 tok: DRAFT200 0.6923 vs CTRL200b
# 0.5530 (+25.2%), byte-identical, zero latches; F/C/F/C bracket in
# k3-soak-logs/2026-09-03-draftbracket (see K3-RESUME-2026-09-03-MORNING.md).
# CAVEAT: on the 2026-08-31 engine this pair lost 14-25% on p2/p3/chat; the
# re-measurement on the current engine is k3-drafter-xprompt-post.sh.
# Controls that must exclude it: K3_QWEN_ROUNDTRIP_SOFT=0 K3_QWEN_REARM=0.
export K3_QWEN_ROUNDTRIP_SOFT=1 K3_QWEN_REARM=4
# ACCEPTANCE-GATED RE-ARM PROMOTED 2026-09-03 02:2x (session, under
# the operator's "promote the champion" mandate; makes the pair above prompt-safe).
# Re-arm only while the drafter's rolling acceptance EWMA >= 500 permille;
# a measured-low prompt keeps the historical one-way latch. 200-tok results
# (k3-soak-logs/2026-09-03-xprompt + -night2): champion 0.6908 (gain kept,
# byte-identical), p2 0.4966 vs control 0.4980 (null, text identical to
# control), p3 0.4818 vs 0.4997 (-3.6%, was -21% ungated). A bar of 600 may
# trim p3's residual; 700 would risk the champion prompt (~680 acceptance).
# Controls that exclude the whole drafter change:
#   K3_QWEN_ROUNDTRIP_SOFT=0 K3_QWEN_REARM=0 K3_QWEN_REARM_MIN_ACCEPT=0
export K3_QWEN_REARM_MIN_ACCEPT=500
export K3_RESIDENCY_SET=1   # PROMOTED 2026-09-04 (residency): pin the spine heaps in one MTLResidencySet on every queue; removes ~5 ms/layer of per-command-buffer residency validation; byte-identical
export K3_ARRIVAL_GROUPS=8   # PROMOTED 2026-09-04: arrival-driven expert compute — tile dispatched in 8 waves as experts land, provider accumulates, rows complete on the last wave; byte-identical (md5 + token-equivalence gate)
export K3_TIER_BALANCE=1   # PROMOTED 2026-09-04 (E): enclosure-side chunk probes the lightest tier first; with argu4 +2.2% on arrival (0.8411/0.8392 vs 0.8213/0.8225), +1.5% pre-arrival
export K3_SPLIT_READ=2   # PROMOTED 2026-09-03: split each expert read across two homes
export K3_SPLIT_ETA=1 K3_SPLIT_ETA_GBPS=7.1,5.5,6.5,13.5   # PROMOTED 2026-09-06: in-flight ETA router for chunked reads; rates hot(White),dir_c(Yellow),dir_b(Green),primary(internal)
export K3_PLAN_BALANCE=1   # PROMOTED 2026-09-06: the plan path (45% of expert bytes) picks the tier with the lowest expected completion among holders — mirror layout pair 1.0586 / 1.0725 vs 0.9645 / 0.9562 (+11.0%), md5 identical; first champion above 1.0 tok/s

# ---- per-arm overrides -----------------------------------------------------
OV="$*"
for kv in "$@"; do export "$kv"; done

export K3_ARM_TRACE=${K3_ARM_TRACE:-}
export K3_ARM_TAG=$TAG K3_ARM_TOKENS=$TOKENS K3_ARM_CHAT=$CHAT
export K3_ARM_PROMPT=$PROMPT K3_ARM_OV=$OV

# REFUSE to measure while a persistent `deltafin serve` is resident. Serve holds
# the ~50 GiB spine in RAM for the life of the process, so a cold-process arm
# running alongside it measures a machine with ~50 GiB less headroom, a warm
# allocator and a warm Metal pool. That is a different machine, and the arm
# would look perfectly valid. Build with serve; measure without it.
if [ "${K3_ALLOW_WARM:-0}" != "1" ] && pgrep -x deltafin >/dev/null 2>&1; then
    echo "[k3-arm] FATAL: a deltafin process is already resident (likely 'serve')." >&2
    echo "[k3-arm] Promotable arms must run cold. Stop it:  pkill -x deltafin" >&2
    echo "[k3-arm] Or run warm deliberately:  K3_ALLOW_WARM=1 sh k3-arm.sh ..." >&2
    echo "[k3-arm] RUN INVALID — NOT RECORDED" >&2
    exit 1
fi

# Settle the page cache and the enclosures before the arm. Constraint of
# record: ./k3-pressure 60 before each arm, AC power, one variable per run.
#
# K3_PRESSURE_GB overrides the size, default 60 so every existing caller is
# unchanged. The 60 was chosen while k3-pressure was a NO-OP (2026-08-22 to
# 2026-08-31), so it has never been justified by measurement; 0 skips the step
# entirely. Whatever is used is recorded in the arm log so a block can never
# again be read without knowing what pressure preceded each arm.
K3_PRESSURE_GB=${K3_PRESSURE_GB:-60}
if [ "$K3_PRESSURE_GB" != "0" ]; then
    # Machine-state guard (2026-09-07, review): swap in use or a low memory
    # baseline means memory compression between command buffers and a slower
    # host — the unexplained 4–8% of 09-06. Abort unless K3_ALLOW_SWAP=1.
    if [ "${K3_ALLOW_SWAP:-0}" != "1" ]; then
      swap_mb=$(sysctl -n vm.swapusage 2>/dev/null | sed -n 's/.*used = \([0-9.]*\)M.*/\1/p' | cut -d. -f1)
      if [ -n "$swap_mb" ] && [ "$swap_mb" -gt 100 ]; then
        echo "[k3-arm] FATAL: swap in use (${swap_mb} MB > 100 MB) — close Chrome/heavy apps, wait for swap to drain, or K3_ALLOW_SWAP=1" >&2
        echo "[k3-arm] RUN INVALID — NOT RECORDED" >&2
        exit 3
      fi
    fi
    "$ROOT/k3-pressure" "$K3_PRESSURE_GB" >/dev/null 2>&1
fi
export K3_ARM_PRESSURE_GB=$K3_PRESSURE_GB

K3_OUT=$OUTDIR
export K3_OUT
# shellcheck disable=SC1090
. "$ROOT/k3-measure.sh"
k3_measure "$TAG" sh "$ROOT/k3-arm-inner.sh"
