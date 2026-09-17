#!/usr/bin/env python3
"""Storage ladder chart: upstream vs our fork, prefill and decode, by drive count.
Emits light and dark SVG. No dependencies. Numbers are passed in, never inferred."""
import sys, json

def chart(data, dark=False):
    fg      = '#e6e8ee' if dark else '#14161c'
    muted   = '#9aa1b2' if dark else '#5d6478'
    grid    = '#2b3040' if dark else '#e3e6ee'
    bg      = '#0d1017' if dark else '#ffffff'
    base    = '#6b7280' if dark else '#b4bac7'
    track   = '#1a1f2b' if dark else '#f1f3f7'
    accents = ['#6f7ee4', '#8c82f0', '#b0a2ff'] if dark else ['#93a6ec', '#6f7ee4', '#6153dc']
    W, panel_h, pad_l, pad_r, top = 1000, 204, 196, 26, 56
    rows = []
    for pi, (metric, unit) in enumerate([('prefill', 'prompt processing tok/s'), ('decode', 'steady decode tok/s')]):
        bars = [('Upstream ds4 · internal only', data[metric]['upstream'], base, True)]
        for i, (label, key) in enumerate([('Our fork · internal only', '1'),
                                          ('Our fork · +1 external', '2'),
                                          ('Our fork · +2 external', '3')]):
            v = data[metric].get(key)
            if v is not None:
                bars.append((label, v, accents[i], False))
        rows.append((metric, unit, bars))
    H = top + sum(panel_h for _ in rows) + 46
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">',
           f'<rect width="{W}" height="{H}" fill="{bg}"/>',
           f'<text x="{pad_l}" y="30" font-size="18" font-weight="700" fill="{fg}">'
           f'DeepSeek V4.1-Flash Q4 (518 GB) streamed from SSD — M5 Max, 128 GB</text>',
           f'<text x="{pad_l}" y="48" font-size="12.5" fill="{muted}">'
           f'512-token prompt, 200 generated · every arm byte-identical output SHA-256 · higher is better</text>']
    y = top
    for metric, unit, bars in rows:
        mx = max(v for _, v, _, _ in bars) * 1.34
        track_w = W - pad_l - pad_r - 132
        out.append(f'<text x="{pad_l}" y="{y+22}" font-size="13.5" font-weight="700" fill="{fg}">{unit}</text>')
        by = y + 38
        for label, v, colour, is_base in bars:
            bw = (W - pad_l - pad_r - 132) * (v / mx)
            gain = v / bars[0][1]
            out.append(f'<text x="{pad_l-12}" y="{by+16.5}" font-size="12" text-anchor="end" '
                       f'fill="{muted if is_base else fg}">{label}</text>')
            out.append(f'<rect x="{pad_l}" y="{by}" width="{track_w:.1f}" height="24" rx="12" fill="{track}"/>')
            out.append(f'<rect x="{pad_l}" y="{by}" width="{max(bw, 24):.1f}" height="24" rx="12" fill="{colour}"/>')
            out.append(f'<text x="{pad_l+bw+10:.1f}" y="{by+16.5}" font-size="13" font-weight="700" '
                       f'fill="{fg}">{v:.2f}</text>')
            if not is_base:
                out.append(f'<text x="{pad_l+bw+59:.1f}" y="{by+16.5}" font-size="12.5" font-weight="600" '
                           f'fill="{colour}">{gain:.2f}×</text>')
            by += 36
        y += panel_h
    out.append(f'<text x="{pad_l}" y="{H-16}" font-size="11.5" fill="{muted}">'
               f'Upstream control: pinned ds4 bd66c40 on the internal SSD, median of four interleaved arms. '
               f'“+1 / +2 external” add byte-identical replicas of the 518 GB file.</text>')
    out.append('</svg>')
    return '\n'.join(out)

if __name__ == '__main__':
    data = json.load(open(sys.argv[1]))
    for dark, name in [(False, sys.argv[2]), (True, sys.argv[3])]:
        open(name, 'w').write(chart(data, dark))
        print('  wrote', name)
