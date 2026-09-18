#!/usr/bin/env python3
"""Horizontal pill-bar chart from a JSON spec (same style as make-ladder.py). No dependencies.

Spec: {"title": ..., "subtitle": ..., "footnote": ...,
       "panels": [{"unit": "steady decode tok/s", "bars": [{"label": ..., "value": 2.02, "base": true}, ...]}]}
The first bar with "base": true in a panel is the reference for the "×" gain labels; bars without
"base" take the blue ramp in order. Numbers are passed in, never inferred.

usage: make-bars.py spec.json out.svg out-dark.svg
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
    W, pad_l, pad_r, top = 1000, int(spec.get('pad_left', 236)), 26, 56
    radius = float(spec.get('radius', 12))
    panels = spec['panels']
    heights = [38 + (16 if p.get('note') else 0) + 36 * len(p['bars']) + 26 for p in panels]
    notes = spec['footnote'] if isinstance(spec['footnote'], list) else [spec['footnote']]
    H = top + sum(heights) + 30 + 16 * len(notes)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">',
           f'<rect width="{W}" height="{H}" fill="{bg}"/>',
           f'<text x="{pad_l}" y="30" font-size="18" font-weight="700" fill="{fg}">{spec["title"]}</text>',
           f'<text x="{pad_l}" y="48" font-size="12.5" fill="{muted}">{spec["subtitle"]}</text>']
    y = top
    for panel, ph in zip(panels, heights):
        bars = panel['bars']
        mx = max(b['value'] for b in bars) * 1.34
        track_w = W - pad_l - pad_r - 132
        out.append(f'<text x="{pad_l}" y="{y + 22}" font-size="13.5" font-weight="700" fill="{fg}">{panel["unit"]}</text>')
        by = y + 38
        if panel.get('note'):
            out.append(f'<text x="{pad_l}" y="{y + 38}" font-size="11.5" fill="{muted}">{panel["note"]}</text>')
            by += 16
        ref = next((b['value'] for b in bars if b.get('base')), bars[0]['value'])
        ai = 0
        for b in bars:
            is_base = bool(b.get('base'))
            colour = base if is_base else accents[min(ai, len(accents) - 1)]
            if not is_base:
                ai += 1
            v = b['value']
            bw = track_w * (v / mx)
            out.append(f'<text x="{pad_l - 12}" y="{by + 16.5}" font-size="12" text-anchor="end" '
                       f'fill="{muted if is_base else fg}">{b["label"]}</text>')
            out.append(f'<rect x="{pad_l}" y="{by}" width="{track_w:.1f}" height="24" rx="{radius}" fill="{track}"/>')
            out.append(f'<rect x="{pad_l}" y="{by}" width="{max(bw, 2 * radius, 8):.1f}" height="24" rx="{radius}" fill="{colour}"/>')
            out.append(f'<text x="{pad_l + bw + 10:.1f}" y="{by + 16.5}" font-size="13" font-weight="700" fill="{fg}">{v:.{dec}f}</text>')
            if not is_base and ref:
                out.append(f'<text x="{pad_l + bw + 59:.1f}" y="{by + 16.5}" font-size="12.5" font-weight="600" '
                           f'fill="{colour}">{v / ref:.2f}×</text>')
            by += 36
        y += ph
    for i, note in enumerate(notes):
        out.append(f'<text x="{pad_l}" y="{H - 16 - 16 * (len(notes) - 1 - i)}" font-size="11.5" fill="{muted}">{note}</text>')
    out.append('</svg>')
    return '\n'.join(out)


if __name__ == '__main__':
    spec = json.load(open(sys.argv[1]))
    for dark, name in [(False, sys.argv[2]), (True, sys.argv[3])]:
        open(name, 'w').write(chart(spec, dark))
        print('  wrote', name)
