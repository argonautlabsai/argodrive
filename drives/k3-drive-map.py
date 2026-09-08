#!/usr/bin/env python3
"""k3-drive-map: which physical drive is which — RUN AFTER ANY UNPLUG/REPLUG and before any layout/rebalance work.
Prints colour name | model | serial | disk | volumes (engine roles) | Thunderbolt bus | direct/hub | link | free,
diffs against the last snapshot (k3-drive-map.json) and exits 1 on drift or a missing engine role volume.
Usage: k3-drive-map.py [--baseline]   (--baseline accepts the current map as the new reference)"""
import json, os, re, subprocess, sys, datetime
R = "$K3_DIR"; SNAP = f"{R}/k3-drive-map.json"; NAMES = f"{R}/k3-drive-names.json"
ROLES = {"Yellow": "K3A role: dir_c (serve3-argu4, ~14% of reads)", "Green": "K3B role: hot (serve-b-argu4, ~14%)", "White": "K3C role: dir_b (k3-experts-b, ~22%, census slot)"}
def sh(cmd): return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
names = json.load(open(NAMES)) if os.path.exists(NAMES) else {}
# 1. NVMe drives: model / serial / disk
drives = {}; cur = {}
for line in sh("system_profiler SPNVMeDataType").split("\n"):
    m = re.match(r"\s*(Model|Serial Number|BSD Name): (.+)$", line)
    if not m: continue
    k, v = m.group(1), m.group(2).strip()
    if k == "Model": cur = {"model": v}
    elif k == "Serial Number": cur["serial"] = v
    elif k == "BSD Name" and re.fullmatch(r"disk\d+", v) and "serial" in cur and "disk" not in cur:
        cur["disk"] = v; drives[v] = cur
# 2. Thunderbolt bus + hub depth from the I/O registry PCI chain
reg = sh("ioreg -p IOService -l -w0").split("\n"); node = re.compile(r"^([| ]*)\+-o (.+?)\s+<class ([^,]+)")
stack = []; chains = {}
for i, line in enumerate(reg):
    m = node.match(line)
    if not m: continue
    depth = len(m.group(1)) // 2; name = m.group(2); cls = m.group(3)
    while stack and stack[-1][0] >= depth: stack.pop()
    stack.append((depth, name, cls))
    if "IONVMeController" in cls:
        serial = ""
        for j in range(i, min(i + 600, len(reg))):
            mm = re.search(r'"Serial Number" = "([^"]+)"', reg[j])
            if mm: serial = mm.group(1).strip(); break
        host = [n for d, n, c in stack if re.match(r"pcic\d+-bridge", n)]
        bridges = sum(1 for d, n, c in stack if c == "IOPCI2PCIBridge")
        chains[serial] = {"bus": (host[0][4] if host else "?"), "bridges": bridges}
# 3. Thunderbolt tree: which bus has a hub and the enclosure link speed behind it
# per bus: is there a hub, and what is the ENCLOSURE's own negotiated link (the first Speed line after
# its Device Name), never assumed from the hub type — a TB4 device such as the M1 on the same hub shows
# 40 Gb/s on its own entry and must not be attributed to the enclosure.
tb = sh("system_profiler SPThunderboltDataType"); buses = {}; bus = None; pending = None
for line in tb.split("\n"):
    m = re.match(r"\s*Thunderbolt/USB4 Bus (\d+):", line)
    if m: bus = m.group(1); buses[bus] = {"hub": False, "enclosure_link": "?", "others": []}; pending = None; continue
    if bus is None: continue
    m = re.match(r"\s*Device Name: (.+)$", line)
    if m:
        name = m.group(1).strip()
        if "Hub" in name: buses[bus]["hub"] = True; pending = None
        elif "Express" in name or "1M2" in name: pending = "enclosure"
        elif "MacBook Pro" == name or name.startswith("MacBook"): pending = None
        else: pending = "other:" + name
        continue
    m = re.match(r"\s*Speed: (.+)$", line)
    if m and pending == "enclosure": buses[bus]["enclosure_link"] = m.group(1).strip(); pending = None
    elif m and pending and pending.startswith("other:"): buses[bus]["others"].append(pending[6:] + " @ " + m.group(1).strip()); pending = None
# 4. volumes per disk
mounts = sh("mount")
def volumes_of(disk):
    # physical disk -> APFS container(s) -> volumes
    vols = []; containers = []
    for line in sh(f"diskutil list {disk}").split("\n"):
        m = re.search(r"Apple_APFS Container (disk\d+)", line)
        if m: containers.append(m.group(1))
    for c in containers:
        for line in sh(f"diskutil list {c}").split("\n"):
            m = re.search(r"APFS Volume (\S+)\s", line)
            if m and not m.group(1).startswith("disk"): vols.append(m.group(1))
    return vols
rows = []
for disk, d in sorted(drives.items()):
    if d["model"].startswith("APPLE"): continue
    ch = chains.get(d["serial"], {"bus": "?", "bridges": 0})
    binfo = buses.get(ch["bus"], {})
    behind_hub = ch["bridges"] >= 3 or binfo.get("hub", False)
    link = binfo.get("enclosure_link", "?") + (" via hub" if behind_hub else "")
    if binfo.get("others"): link += " (also on this bus: " + "; ".join(binfo["others"]) + ")"
    vols = volumes_of(disk); free = ""
    for v in vols:
        if f"/Volumes/{v} " in mounts:
            df = sh(f"df -h /Volumes/{v} | tail -1").split(); free = df[3] + " free" if len(df) > 3 else ""
    rows.append({"name": names.get(d["serial"], "?"), "model": d["model"], "serial": d["serial"], "disk": disk,
                 "volumes": vols, "bus": ch["bus"], "attach": "HUB" if behind_hub else "direct", "link": link, "free": free})
print("k3-drive-map  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
print("%-7s %-24s %-13s %-6s %-14s %-4s %-7s %-40s %s" % ("name", "model", "serial", "disk", "volumes", "bus", "attach", "enclosure link", "free"))
for r in rows:
    print("%-7s %-24s %-13s %-6s %-14s %-4s %-7s %-40s %s" % (r["name"], r["model"], r["serial"], r["disk"], ",".join(r["volumes"]) or "(blank)", r["bus"], r["attach"], r["link"], r["free"]))
present = {v for r in rows for v in r["volumes"]}
missing = [k for k in ROLES if k not in present]
problems = []
if missing: problems.append("engine role volume(s) missing: " + ", ".join(f"{k} = {ROLES[k]}" for k in missing))
old = json.load(open(SNAP)) if os.path.exists(SNAP) else None
if old is not None:
    by_serial_old = {r["serial"]: r for r in old["rows"]}; by_serial_new = {r["serial"]: r for r in rows}
    for s, r in by_serial_new.items():
        o = by_serial_old.get(s)
        if o is None: problems.append(f"NEW drive {r['name']} {r['model']} ({s}) on bus {r['bus']} {r['attach']}"); continue
        for k in ("bus", "attach", "volumes"):
            if o[k] != r[k]: problems.append(f"{r['name']} ({r['model']}): {k} changed {o[k]} -> {r[k]}")
    for s, o in by_serial_old.items():
        if s not in by_serial_new: problems.append(f"drive GONE: {o['name']} {o['model']} ({s}) was on bus {o['bus']} {o['attach']}")
if problems:
    print("\nDRIFT / PROBLEMS:"); [print("  - " + p) for p in problems]
else:
    print("\nmap unchanged vs the last snapshot" if old else "\nno previous snapshot")
if "--baseline" in sys.argv or old is None:
    json.dump({"when": datetime.datetime.now().isoformat(timespec="minutes"), "rows": rows}, open(SNAP, "w"), indent=1); print("snapshot written:", SNAP)
hubbed = [r for r in rows if r["attach"] == "HUB"]
print("\nrule: never rebalance or attribute a tail without this map; the hub is TB5 (80 Gb/s links, 120 boost) — read each enclosure's own link above, do not assume. Behind the hub: %s" % (", ".join(f"{r['name']}={','.join(r['volumes']) or 'blank'}" for r in hubbed) or "none"))
sys.exit(1 if problems and "--baseline" not in sys.argv else 0)
