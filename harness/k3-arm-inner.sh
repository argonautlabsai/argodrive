#!/bin/sh
# Inner half of k3-arm.sh: prints the arm banner INTO the arm log, then execs
# the engine. Split out so k3_measure captures the banner and the engine output
# in one stream — a banner printed by the outer script lands outside the .log
# and an arm then cannot be attributed to its overrides.
#
# Every K3_ARM_* variable is exported by k3-arm.sh. Do not run this directly.
set -u
DF=$K3_DIR/engines/deltafin/target/release/deltafin

echo "ARM $K3_ARM_TAG START $(date '+%H:%M:%S') overrides: $K3_ARM_OV tokens=$K3_ARM_TOKENS chat=$K3_ARM_CHAT pressure_gb=${K3_ARM_PRESSURE_GB:-60} prompt=[$K3_ARM_PROMPT]"

# K3_ARM_TRACE=<name> captures the native expert-route JSONL for this arm.
# The engine resolves --router-trace RELATIVE TO THE MODEL ROOT, so the file
# lands under $DELTAFIN_ROOT and the caller copies it back beside the arm.
TRACE=""
if [ -n "${K3_ARM_TRACE:-}" ]; then
    TRACE="--router-trace $K3_ARM_TRACE --router-trace-mode sync"
fi

# shellcheck disable=SC2086
if [ "$K3_ARM_CHAT" = "1" ]; then
    exec caffeinate -is "$DF" run --model-root "$DELTAFIN_ROOT" --stats --chat \
        $TRACE --max-new "$K3_ARM_TOKENS" --prompt "$K3_ARM_PROMPT"
else
    exec caffeinate -is "$DF" run --model-root "$DELTAFIN_ROOT" --stats \
        $TRACE --max-new "$K3_ARM_TOKENS" --prompt "$K3_ARM_PROMPT"
fi
