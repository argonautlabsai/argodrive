#!/bin/sh
# One line of memory truth: "cpu used avail mps swap_mb gpu free inactive" (GiB)
#
# macOS accounting, established 2026-08-29 by measurement:
#   USED  = active + wired + compressor      (Activity Monitor "Memory Used")
#   AVAIL = free + inactive + speculative    (macOS holds `free` near 0 by
#           design; inactive is reclaimable cache, so `free` alone is NOT
#           headroom and reading it as such understates available RAM ~40x)
# The IOAccelerator "In use system memory" value is a SUBSET of those pages,
# NOT an addition: vm_stat's own categories already sum to physical RAM
# (measured 126.5 of 128 GiB). Adding it double-counts and produced impossible
# >128 GiB totals. Emitted as the GPU share for attribution only.
#
# 2026-08-31: columns 7 and 8 add the RAW `Pages free` and `Pages inactive`.
# Requested explicitly. `free` on its own is NOT headroom and must not be read
# as such — but it IS the number that goes to zero first under pressure, so it
# is the leading indicator, and AVAIL is the one that says whether the machine
# is actually in trouble. Reported side by side so the gap between them is
# visible rather than argued about. APPENDED, never inserted: k3-table.py,
# k3_memparse_shim and k3-live all index these columns positionally, and older
# .sys files carry only six.
ps -A -o %cpu | awk '{s+=$1} END {printf "%.1f ", s}'
vm_stat | awk '/Pages active/{a=$3}/Pages wired/{w=$4}/occupied by compressor/{k=$5}\
/Pages free/{f=$3}/Pages inactive/{i=$3}/Pages speculative/{s=$3}\
END{printf "%.2f %.2f ",(a+w+k)*16384/1073741824,(f+i+s)*16384/1073741824}'
io=$(ioreg -r -d 1 -w 0 -c IOAccelerator 2>/dev/null)
gm=$(printf '%s' "$io" | grep -oE '"In use system memory"=[0-9]+' | head -1 | cut -d= -f2)
g=$(printf '%s' "$io" | grep -oE '"Device Utilization %"=[0-9]+' | head -1 | cut -d= -f2)
sw=$(sysctl -n vm.swapusage | awk '{gsub("M","",$6); print $6}')
fi=$(vm_stat | awk '/Pages free/{f=$3}/Pages inactive/{i=$3}\
END{printf "%.2f %.2f",f*16384/1073741824,i*16384/1073741824}')
awk -v b="${gm:-0}" -v s="${sw:-0}" -v g="${g:-0}" -v fi="$fi" \
    'BEGIN{printf "%.2f %s %s %s\n",b/1073741824,s,g,fi}'
