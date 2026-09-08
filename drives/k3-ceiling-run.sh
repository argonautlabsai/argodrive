#!/bin/sh
# k3-ceiling-run.sh — standalone read ceiling of every expert directory, then
# Yellow's concurrency curve. Engine must be idle. Written as an sh script
# because the inline zsh loop hit the `set -- $var` no-word-split trap
# (memory: measurement-traps) and every run got an empty seconds argument.
set -u
ROOT=$K3_DIR
OUT=$ROOT/k3-soak-logs/2026-09-08-ceiling
mkdir -p "$OUT"
LOG=$OUT/CEILING.log

if pgrep -x deltafin >/dev/null 2>&1; then
    echo "[ceiling] engine busy — refusing" | tee -a "$LOG"; exit 3
fi
echo "[ceiling] start $(date '+%H:%M:%S') sampler=$(pgrep -f k3-diskscope | wc -l | tr -d ' ')" | tee -a "$LOG"

run() {  # run <dir> <seconds> <threads> <label>
    python3 "$ROOT/k3-drive-ceiling.py" "$1" "$2" "$3" "$4" 2>&1 | tee -a "$LOG"
    sleep 3
}

run /Volumes/Yellow/serve3-green            20 32 Yellow
run /Volumes/White/k3-experts-b             20 32 White
run /Volumes/Green/k3-experts-full          20 32 Green
run "$ROOT/deltafin-root-local/k3-experts"  20 32 internal
run /Volumes/Yellow/serve3-green            15  8 Yellow
run /Volumes/Yellow/serve3-green            15 16 Yellow
run /Volumes/Yellow/serve3-green            15 64 Yellow
run /Volumes/Yellow/serve3-green            15 128 Yellow
run /Volumes/Green/k3-experts-full          15 64 Green
run /Volumes/White/k3-experts-b             15 64 White

echo "[ceiling] done $(date '+%H:%M:%S')" | tee -a "$LOG"
