#!/usr/bin/env python3
"""Vertical small-multiple column chart from a JSON spec, in the ladder chart's style. No dependencies.

Spec: {"title": ..., "subtitle": ..., "footnote": [...],
       "panels": [{"name": "Kimi K3 · 2.78T", "note": "17-token prompt, median of 3",
                   "bars": [{"label": "1 drive", "value": 0.55, "base": true}, {"label": "+1", "value": 0.75}, ...]}]}
Each panel has its own scale (the tallest bar fills the panel); the "base" bar is grey and the
reference for the "×" labels; other bars take the blue ramp. Numbers are passed in, never inferred.

usage: make-columns.py spec.json out.svg out-dark.svg
"""
import json
import sys


def chart(spec, dark=False):
    fg = '#e6e8ee' if dark else '#14161c'
    muted = '#9aa1b2' if dark else '#5d6478'
    bg = '#0d1017' if dark else '#ffffff'
    base = '#6b7280' if dark else '#b4bac7'
    track = '#1a1f2b' if dark else '#f1f3f7'
    accents = ['#6f7ee4', '#8c82f0', '#a394f7', '#b0a2ff'] if dark else ['#93a6ec', '#6f7ee4', '#6153dc', '#4338b3']
    panels = spec['panels']
    W, top, gap, pad = 1000, 70, 34, 30
    panel_w = (W - 2 * pad - gap * (len(panels) - 1)) / len(panels)
    bar_area_h, label_h = 250, 58
    H = top + 30 + bar_area_h + label_h + 30 + 16 * len(spec['footnote']) + 12
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">',
           f'<rect width="{W}" height="{H}" fill="{bg}"/>',
           f'<text x="{pad}" y="30" font-size="18" font-weight="700" fill="{fg}">{spec["title"]}</text>',
           f'<text x="{pad}" y="48" font-size="12.5" fill="{muted}">{spec["subtitle"]}</text>']
    for pi, panel in enumerate(panels):
        x0 = pad + pi * (panel_w + gap)
        bars = panel['bars']
        n = len(bars)
        ref = next((b['value'] for b in bars if b.get('base')), bars[0]['value'])
        mx = max(b['value'] for b in bars)
        slot = panel_w / n
        bw = min(56, slot * 0.62)
        base_y = top + 30 + bar_area_h
        out.append(f'<text x="{x0}" y="{top + 18}" font-size="14" font-weight="700" fill="{fg}">{panel["name"]}</text>')
        out.append(f'<text x="{x0}" y="{top + 34}" font-size="11.5" fill="{muted}">{panel.get("note", "")}</text>')
        # baseline track
        out.append(f'<rect x="{x0}" y="{base_y}" width="{panel_w:.1f}" height="2" rx="1" fill="{track}"/>')
        ai = 0
        for bi, b in enumerate(bars):
            is_base = bool(b.get('base'))
            colour = base if is_base else accents[min(ai, len(accents) - 1)]
            if not is_base:
                ai += 1
            v = b['value']
            h = (bar_area_h - 40) * (v / mx)
            cx = x0 + slot * (bi + 0.5)
            x = cx - bw / 2
            y = base_y - h
            # rounded top only: draw a pill and clip the bottom with a rect over the baseline
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h + bw / 2:.1f}" rx="{bw / 2:.1f}" fill="{colour}"/>')
            out.append(f'<rect x="{x - 1:.1f}" y="{base_y + 1}" width="{bw + 2:.1f}" height="{bw / 2 + 2:.1f}" fill="{bg}"/>')
            out.append(f'<rect x="{x0}" y="{base_y}" width="{panel_w:.1f}" height="2" rx="1" fill="{track}"/>')
            out.append(f'<text x="{cx:.1f}" y="{y - 8:.1f}" font-size="13.5" font-weight="700" text-anchor="middle" fill="{fg}">{v:.2f}</text>')
            if not is_base and ref:
                out.append(f'<text x="{cx:.1f}" y="{y - 24:.1f}" font-size="12" font-weight="600" text-anchor="middle" fill="{colour}">{v / ref:.2f}×</text>')
            out.append(f'<text x="{cx:.1f}" y="{base_y + 20}" font-size="12" text-anchor="middle" fill="{muted if is_base else fg}">{b["label"]}</text>')
            if b.get('sub'):
                out.append(f'<text x="{cx:.1f}" y="{base_y + 36}" font-size="10.5" text-anchor="middle" fill="{muted}">{b["sub"]}</text>')
    fy = H - 12 - 16 * (len(spec['footnote']) - 1)
    for i, note in enumerate(spec['footnote']):
        out.append(f'<text x="{pad}" y="{fy + 16 * i}" font-size="11.5" fill="{muted}">{note}</text>')
    out.append('</svg>')
    return '\n'.join(out)


if __name__ == '__main__':
    spec = json.load(open(sys.argv[1]))
    for dark, name in [(False, sys.argv[2]), (True, sys.argv[3])]:
        open(name, 'w').write(chart(spec, dark))
        print('  wrote', name)
