#!/usr/bin/env python3
"""Per-drive read timeline from the 100 ms sampler CSV (record arm 2026-09-08), in the
package chart theme (light + dark), one hue per drive shared with drive-draw.svg.
Writes argodrive-tools/charts/read-timeline{,-dark}.svg (+ png) and the package copy."""
import csv, collections, statistics as st, importlib.util, re, sys
spec = importlib.util.spec_from_file_location("mc", "k3-public-bench/results/charts/make-charts.py")
mc = importlib.util.module_from_spec(spec); spec.loader.exec_module(mc)
import matplotlib.pyplot as plt

rows = collections.defaultdict(list)
for r in csv.DictReader(open("k3-soak-logs/2026-09-08-dash200/D200.csv")):
    if r["dev"].startswith("disk") and not r["dev"].endswith("w"):
        rows[r["dev"]].append((float(r["t_s"]), int(r["v1"])))
name = {"disk0": "internal SSD", "disk4": "SN8100 1 TB (hot replica)", "disk5": "SN8100 2 TB (full second base)", "disk8": "SN7100 1 TB (hub, cold replica)"}
order = ("disk0", "disk4", "disk5", "disk8")

for mode in ("light", "dark"):
    T = mc.THEMES[mode]; mc.rc(T)
    fig, ax = plt.subplots(figsize=(11, 4.6)); fig.subplots_adjust(left=0.06, right=0.99, top=0.82, bottom=0.2)
    mc.style(ax, T, "GB/s read (1-second windows)"); ax.set_ylim(0, 15); ax.set_xlim(0, 232)
    t0 = None; ends = []
    for k, dev in enumerate(order):
        s = rows[dev]; xs, ys = [], []; i = 0
        while i < len(s):
            j = i
            while j < len(s) and s[j][0] - s[i][0] < 1.0: j += 1
            if j < len(s): xs.append(s[i][0]); ys.append((s[j][1] - s[i][1]) / (s[j][0] - s[i][0]) / 1e9)
            i = j
        if t0 is None: t0 = next((x for x, y in zip(xs, ys) if y > 0.3), xs[0])
        keep = [(x - t0, y) for x, y in zip(xs, ys) if 0 <= x - t0 <= 185]
        xs, ys = [q[0] for q in keep], [q[1] for q in keep]
        ax.plot(xs, ys, color=T["drives"][k], linewidth=1.5, solid_capstyle="round", solid_joinstyle="round")
        med = st.median([y for y in ys if y > 0.3]) if ys else 0
        ends.append((k, dev, xs[-1], ys[-1], med))
    ends.sort(key=lambda e: e[3]); placed = []
    for k, dev, xe, ye, med in ends:
        y = ye
        if placed and y - placed[-1] < 0.8: y = placed[-1] + 0.8
        placed.append(y)
        ax.plot([xe, xe + 3], [ye, y], color=T["base"], linewidth=0.8)
        ax.text(xe + 4.5, y, f"{name[dev]}  ·  median {med:.1f}", va="center", fontsize=8, color=T["t2"])
    ax.set_xlabel("seconds from engine start (200-token completion, four drives)", color=T["t2"], fontsize=8.5)
    ax.set_yticks([0, 5, 10, 15])
    mc.titles(ax, T, "What the read monitor sees",
              "Per-drive throughput during one 200-token run, all four drives sampled together at 100 ms",
              "The first ~12 s is model load and the 6-token prompt; decode follows. Source: the per-device sampler (k3-diskscope) on the 2026-09-08 record arm.")
    for out in ("argodrive-tools/charts", "k3-public-bench/results/charts"):
        suffix = "" if mode == "light" else "-dark"
        path = f"{out}/read-timeline{suffix}.svg"
        fig.savefig(path, bbox_inches="tight", facecolor=T["surface"], pad_inches=0.18)
        if mode == "light": fig.savefig(f"{out}/read-timeline.png", bbox_inches="tight", facecolor=T["surface"], dpi=160, pad_inches=0.18)
        t = open(path).read(); t = re.sub(r"font-family:\s*'?DejaVu Sans'?", f"font-family: {mc.FONT_STACK}", t); open(path, "w").write(t)
        print("wrote", path)
    plt.close(fig)
