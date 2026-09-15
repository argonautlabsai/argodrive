#!/usr/bin/env python3
"""Qualify a storage source at the engine's own read size, before building on it.

A split read completes when its slowest slice lands, so a candidate source is
judged by latency at the block size the engine issues (256 KiB), not by
sequential throughput. On 2026-09-15 this one number -- 2.59 ms on an M1 Max
over NFS/Thunderbolt against 0.33 ms on the internal SSD, even with the data in
the M1's RAM -- closed a remote-expert design in ten minutes. Run it first.

    source-probe.py FILE [--against REF] [--reads N] [--seed S]

Random offsets, F_NOCACHE + no read-ahead so the local page cache cannot
flatter the result. Reports p50/p90 at 256 KiB and 8 MiB and the effective GB/s.
"""
import argparse, fcntl, os, random, statistics, sys, time

F_NOCACHE, F_RDAHEAD = 48, 45
SIZES = ((256 << 10, 'engine block'), (8 << 20, 'large request'))


def probe(path, reads, seed):
    size = os.path.getsize(path)
    rng = random.Random(seed)
    fd = os.open(path, os.O_RDONLY)
    nocache = True
    try:
        fcntl.fcntl(fd, F_NOCACHE, 1)
        fcntl.fcntl(fd, F_RDAHEAD, 0)
    except OSError:
        nocache = False
    out = {'path': path, 'bytes': size, 'nocache': nocache, 'rows': []}
    try:
        for blk, label in SIZES:
            if size <= blk:
                continue
            n = reads if blk < (1 << 20) else max(8, reads // 2)
            lat = []
            for _ in range(n):
                off = rng.randrange(0, size - blk) & ~0xFFFF
                t0 = time.perf_counter()
                got = os.pread(fd, blk, off)
                lat.append((time.perf_counter() - t0) * 1000)
                if len(got) != blk:
                    raise IOError('short read at %d' % off)
            lat.sort()
            p50 = statistics.median(lat)
            p90 = lat[min(len(lat) - 1, int(len(lat) * 0.9))]
            out['rows'].append({'block': blk, 'label': label, 'n': n, 'p50_ms': p50, 'p90_ms': p90,
                                'gbs_at_p50': (blk / 1e9) / (p50 / 1000)})
    finally:
        os.close(fd)
    return out


def fmt(r):
    return '  %7d KiB  %-14s p50 %7.2f ms   p90 %7.2f ms   %6.2f GB/s' % (
        r['block'] // 1024, r['label'], r['p50_ms'], r['p90_ms'], r['gbs_at_p50'])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('file')
    ap.add_argument('--against', metavar='REF', help='a known-good local file to compare with')
    ap.add_argument('--reads', type=int, default=40)
    ap.add_argument('--seed', type=int, default=20260915)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    cand = probe(a.file, a.reads, a.seed)
    ref = probe(a.against, a.reads, a.seed) if a.against else None
    if a.json:
        import json
        print(json.dumps({'candidate': cand, 'reference': ref}, indent=2))
        return
    print('candidate: %s%s' % (a.file, '' if cand['nocache'] else '   (F_NOCACHE unsupported: page cache may flatter this)'))
    for r in cand['rows']:
        print(fmt(r))
    if ref:
        print('reference: %s' % a.against)
        for r in ref['rows']:
            print(fmt(r))
        c0, r0 = cand['rows'][0], ref['rows'][0]
        ratio = c0['p50_ms'] / r0['p50_ms']
        print()
        print('verdict at the engine block size: candidate is %.1fx %s than the reference (p50 %.2f vs %.2f ms).' % (
            ratio if ratio >= 1 else 1 / ratio, 'slower' if ratio >= 1 else 'faster', c0['p50_ms'], r0['p50_ms']))
        if ratio > 1.5:
            print('A split read waits for its slowest slice, so this source would set the barrier at any weight above zero.')
        elif ratio > 1.1:
            print('Usable as a low-weight tail source; it will not lead.')
        else:
            print('Comparable to the reference; worth a full interleaved arm.')


if __name__ == '__main__':
    sys.exit(main())
