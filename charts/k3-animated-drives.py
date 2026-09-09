#!/usr/bin/env python3
"""Animated per-drive read gauges (SVG/SMIL, no JavaScript) replaying the real 100 ms
sampler record of the 2026-09-08 200-token record arm at 8x speed, looping. Same
per-drive hues as drive-draw.svg. Browsers animate SMIL inside <img>, so GitHub
READMEs play it. Writes drives-live{,-dark}.svg to the package charts and the tools."""
import csv, collections, statistics as st
SRC = "k3-soak-logs/2026-09-08-dash200/D200.csv"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
THEMES = {
  "light": dict(surface="#fcfcfb", t1="#0b0b0b", t2="#52514e", muted="#8a8880", grid="#eceae4", base="#cfcdc5", tile="#ffffff", tileline="#e6e5df",
                drives=["#2f5d8a", "#c2683b", "#1f8a7a", "#b8860b"]),
  "dark":  dict(surface="#0d1117", t1="#e6edf3", t2="#b1bac4", muted="#7d8590", grid="#21262d", base="#30363d", tile="#161b22", tileline="#30363d",
                drives=["#7fa6cf", "#e08a5e", "#4fbfa5", "#e0b84a"]),
}
order = ["disk0", "disk4", "disk5", "disk8"]
names = {"disk0": ("internal SSD", "2 TB · primary"), "disk4": ("WD SN8100 1 TB", "direct · hot replica"),
         "disk5": ("WD SN8100 2 TB", "direct · full second base"), "disk8": ("WD SN7100 1 TB", "TB5 hub · cold replica")}
ceil = {"disk0": 13.55, "disk4": 7.09, "disk5": 7.04, "disk8": 5.73}
SPEED, WIN, T_END = 8.0, 0.2, 185.0

rows = collections.defaultdict(list)
for r in csv.DictReader(open(SRC)):
    if r["dev"].startswith("disk") and not r["dev"].endswith("w"):
        rows[r["dev"]].append((float(r["t_s"]), int(r["v1"])))
series = {}; t0 = None
for dev in order:
    s = rows[dev]; xs, ys = [], []; i = 0
    while i < len(s):
        j = i
        while j < len(s) and s[j][0] - s[i][0] < WIN: j += 1
        if j < len(s): xs.append(s[i][0]); ys.append((s[j][1] - s[i][1]) / (s[j][0] - s[i][0]) / 1e9)
        i = j
    if t0 is None: t0 = next((x for x, y in zip(xs, ys) if y > 0.3), xs[0])
    pts = [(x - t0, max(0.0, y)) for x, y in zip(xs, ys) if 0 <= x - t0 <= T_END]
    if pts[0][0] > 0: pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < T_END: pts.append((T_END, pts[-1][1]))      # SMIL keyTimes must run exactly 0 -> 1
    series[dev] = pts
dur = T_END / SPEED

def build(mode):
    T = THEMES[mode]; W, H = 960, 352
    top, base_y, gh = 92, 268, 168            # gauge geometry
    scale = gh / 14.0                          # px per GB/s, common 0-14 scale
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
           f'<rect width="{W}" height="{H}" fill="{T["surface"]}"/>',
           f'<text x="24" y="34" font-size="17" font-weight="700" fill="{T["t1"]}">Four drives under the engine — replay of the record arm</text>',
           f'<text x="24" y="54" font-size="11.5" fill="{T["t2"]}">Real per-drive read throughput from the 100 ms sampler, 200-token completion, played at {SPEED:.0f}× and looped · dashed line = each drive\'s standalone ceiling</text>']
    # y grid 0..14 by 2 (GB/s)
    for v in range(0, 15, 2):
        y = base_y - v * scale
        out.append(f'<line x1="60" x2="{W-24}" y1="{y:.1f}" y2="{y:.1f}" stroke="{T["grid"]}" stroke-width="1"/>')
        out.append(f'<text x="52" y="{y+4:.1f}" font-size="10" text-anchor="end" fill="{T["t2"]}">{v}</text>')
    out.append(f'<text x="14" y="{base_y - gh/2:.0f}" font-size="10" fill="{T["t2"]}" transform="rotate(-90 14 {base_y - gh/2:.0f})" text-anchor="middle">GB/s read</text>')
    slot = (W - 24 - 70) / 4
    for k, dev in enumerate(order):
        cx = 70 + slot * (k + 0.5); bw = 64; hue = T["drives"][k]
        pts = series[dev]; med = st.median([y for _, y in pts if y > 0.3]); pk = max(y for _, y in pts)
        vals = ";".join(f"{min(y, 14.0) * scale:.1f}" for _, y in pts)
        times = ";".join(f"{x / T_END:.4f}" for x, _ in pts)
        # ceiling dash
        yc = base_y - ceil[dev] * scale
        out.append(f'<line x1="{cx - bw/2 - 18:.1f}" x2="{cx + bw/2 + 18:.1f}" y1="{yc:.1f}" y2="{yc:.1f}" stroke="{hue}" stroke-width="1.4" stroke-dasharray="4 3"/>')
        out.append(f'<text x="{cx + bw/2 + 22:.1f}" y="{yc + 4:.1f}" font-size="10" fill="{hue}">{ceil[dev]:.1f}</text>')
        # animated bar (anchored at the baseline; height animates along the recorded series)
        out.append(f'<g transform="translate({cx - bw/2:.1f},{base_y}) scale(1,-1)">'
                   f'<rect x="0" y="0" width="{bw}" height="{pts[0][1]*scale:.1f}" rx="3" fill="{hue}" opacity="0.92">'
                   f'<animate attributeName="height" dur="{dur:.2f}s" repeatCount="indefinite" calcMode="linear" keyTimes="{times}" values="{vals}"/></rect></g>')
        # labels
        n1, n2 = names[dev]
        out.append(f'<text x="{cx:.1f}" y="{base_y + 20}" font-size="12" font-weight="600" text-anchor="middle" fill="{T["t1"]}">{n1}</text>')
        out.append(f'<text x="{cx:.1f}" y="{base_y + 36}" font-size="10.5" text-anchor="middle" fill="{T["t2"]}">{n2}</text>')
        out.append(f'<text x="{cx:.1f}" y="{base_y + 52}" font-size="10.5" text-anchor="middle" fill="{T["muted"]}">median {med:.1f} · peak {pk:.1f} GB/s</text>')
    out.append(f'<line x1="60" x2="{W-24}" y1="{base_y}" y2="{base_y}" stroke="{T["base"]}" stroke-width="1"/>')
    # replay progress marker along the bottom
    py = H - 12
    out.append(f'<line x1="60" x2="{W-24}" y1="{py}" y2="{py}" stroke="{T["grid"]}" stroke-width="2"/>')
    out.append(f'<circle cy="{py}" r="3.5" fill="{T["t2"]}"><animate attributeName="cx" dur="{dur:.2f}s" repeatCount="indefinite" values="60;{W-24}"/></circle>')
    out.append(f'<text x="60" y="{py - 8}" font-size="9.5" fill="{T["muted"]}">replay position, 0 → {T_END:.0f} s of the run · the first ~12 s is model load and the 6-token prompt, then decode</text>')
    out.append('</svg>')
    return "\n".join(out)

for mode in ("light", "dark"):
    svg = build(mode); suffix = "" if mode == "light" else "-dark"
    for d in ("k3-public-bench/results/charts", "argodrive-tools/charts"):
        p = f"{d}/drives-live{suffix}.svg"; open(p, "w").write(svg); print("wrote", p, f"{len(svg)/1024:.0f} KB")
