#!/bin/sh
# Drop-in measurement wrapper. Source it, then wrap any benchmark command:
#
#   . $K3_DIR/k3-measure.sh
#   k3_measure my-arm-name  <your benchmark command here>
#
# Produces, per arm, in $K3_OUT (default ./k3-measure-logs):
#   <arm>.csv   per-device byte counters at 200 ms   (k3-diskscope)
#   <arm>.sys   cpu / ram-used / ram-avail / gpu-mem / swap / gpu%  at 1 s
#   <arm>.log   whatever your command printed
#
# The device map is DERIVED AT RUN TIME from each volume's APFS physical store.
# Never hardcode disk numbers: they change on replug. K3A and K3C swapped on us
# after a reconnect, which is exactly how a perfectly healthy drive can appear
# to be doing nothing.
K3_DIR=$K3_DIR

# Fail loudly. An unreadable sampler yields a 0-byte .sys and no .csv with a
# success return code -- an arm that looks measured and is not.
for _t in k3-diskscope k3-memsample.sh; do
    if [ ! -x "$K3_DIR/$_t" ]; then
        echo "[k3-measure] FATAL: $K3_DIR/$_t missing or not executable" >&2
        return 1 2>/dev/null || exit 1
    fi
done
K3_OUT=${K3_OUT:-./k3-measure-logs}

k3_devices() {
    # echo the physical-store device for each mounted volume we care about
    for v in "$@"; do
        if [ "$v" = "/" ]; then p=$(diskutil info / 2>/dev/null | awk -F': *' '/APFS Physical Store/{print $2}')
        else p=$(diskutil info "/Volumes/$v" 2>/dev/null | awk -F': *' '/APFS Physical Store/{print $2}'); fi
        [ -n "$p" ] && echo "$p" | sed 's|s[0-9]*$||'
    done
}

k3_map() {   # human-readable map, print it into your results so logs stay interpretable
    for v in "$@"; do
        if [ "$v" = "/" ]; then n=internal; p=$(diskutil info / 2>/dev/null | awk -F': *' '/APFS Physical Store/{print $2}')
        else n=$v; p=$(diskutil info "/Volumes/$v" 2>/dev/null | awk -F': *' '/APFS Physical Store/{print $2}'); fi
        [ -n "$p" ] && printf "%s=%s " "$n" "$(echo "$p" | sed 's|s[0-9]*$||')"
    done; echo
}

k3_write_marker() {
    # One JSON object for the live dashboard, rewritten in place.
    # state=running|done. Overrides get their quotes squashed so the file
    # stays valid JSON without a real encoder.
    printf '{"arm":"%s","log":"%s","out":"%s","tokens":"%s","chat":"%s","pressure_gb":"%s","overrides":"%s","start":%s,"state":"%s","rc":%s}\n' \
        "$1" "$K3_OUT/$1.log" "$K3_OUT" "${K3_ARM_TOKENS:-}" "${K3_ARM_CHAT:-}" \
        "${K3_ARM_PRESSURE_GB:-}" "$(printf %s "${K3_ARM_OV:-}" | tr '"' "'")" \
        "$2" "$3" "${4:-null}" > "$K3_DIR/k3-live-marker.json" 2>/dev/null || true
}

k3_measure() {
    arm=$1; shift
    mkdir -p "$K3_OUT"
    devs=$(k3_devices / Yellow Green White | tr '\n' ' ')
    echo "[k3-measure] $arm  device map: $(k3_map / Yellow Green White)" | tee "$K3_OUT/$arm.map"
    _mk_start=$(date +%s)
    k3_write_marker "$arm" "$_mk_start" running
    # Launch through `sh -c` so the device list word-splits regardless of the
    # caller's shell. zsh does NOT split unquoted $vars, which silently hands
    # k3-diskscope one bogus device name and produces no CSV at all.
    sh -c "\"$K3_DIR/k3-diskscope\" 200 999999 \"$K3_OUT/$arm.csv\" $devs" >/dev/null 2>&1 & _s=$!
    ( while :; do "$K3_DIR/k3-memsample.sh"; sleep 1; done ) > "$K3_OUT/$arm.sys" 2>/dev/null & _p=$!
    sleep 1
    "$@" > "$K3_OUT/$arm.log" 2>&1; rc=$?
    kill $_s $_p 2>/dev/null; sleep 1
    k3_write_marker "$arm" "$_mk_start" done "$rc"
    echo "[k3-measure] $arm done rc=$rc -> $K3_OUT/$arm.{csv,sys,log}"
    return $rc
}
