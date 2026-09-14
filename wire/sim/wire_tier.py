#!/usr/bin/env python3
"""S0 sensitivity replay. Extends placement/k3-placesim.py's layer barrier model.

Whole records choose the lower ETA of local SSD and a striped remote candidate.
The cold node has one shared SSD queue; adding links cannot multiply its rate.
LRU follows the existing GLM cache replay. No lookahead or compute overlap model.
"""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone


class LRU:
    def __init__(self, slots):
        self.slots = slots
        self.entries = collections.OrderedDict()

    def access(self, key):
        hit = key in self.entries
        self.entries[key] = None
        self.entries.move_to_end(key)
        if len(self.entries) > self.slots:
            self.entries.popitem(last=False)
        return hit


def replay(rows, links=1, node_slots=0, hot=False, record_bytes=21233664,
           host_slots=2846, local_gbs=10, link_gbs=4.4, node_gbs=6, latency_ms=1):
    host, node = LRU(host_slots), LRU(node_slots)
    seconds = 0
    counts = collections.Counter()
    for _, layer, experts in rows:
        local_end = ssd_end = wire_end = 0.0
        for expert in experts:
            key = layer, expert
            counts['requests'] += 1
            if host.access(key):
                counts['host_hits'] += 1
                continue
            local_eta = local_end + record_bytes / (local_gbs * 1e9)
            hit = hot or key in node.entries
            disk_eta = ssd_end if hit else ssd_end + record_bytes / (node_gbs * 1e9)
            wire_eta = max(wire_end, disk_eta) + latency_ms / 1000 + record_bytes / (links * link_gbs * 1e9)
            if links and wire_eta < local_eta:
                wire_end = wire_eta
                counts['wire_gets'] += 1
                counts['node_hits'] += hit
                counts['wire_bytes'] += record_bytes
                if not hit:
                    ssd_end = disk_eta
                    counts['node_ssd_bytes'] += record_bytes
                node.access(key)
            else:
                local_end = local_eta
                counts['local_bytes'] += record_bytes
        seconds += max(local_end, wire_end)
    tokens = len({row[0] for row in rows})
    return dict(counts, io_seconds=seconds, tokens=tokens, links=links,
                node_slots=node_slots, ideal_hot=hot,
                io_only_tok_s=tokens / seconds if seconds else None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('trace', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--reference-tok-s', type=float, default=3.70)
    p.add_argument('--compute-ms-per-token', type=float, default=100,
                   help='Assumed non-I/O time, not a measured engine split')
    a = p.parse_args()
    with a.trace.open() as f:
        rows = [(int(r[0]), int(r[1]), [int(x) for x in r[2:] if x])
                for r in csv.reader(f) if r and r[0] != 'step']
    if not rows or a.compute_ms_per_token < 0 or a.reference_tok_s <= 0:
        p.error('Nonempty routes and valid timing assumptions required')
    scenarios = []
    for label, slots, hot in [('SSD only', 0, False), ('48 GiB LRU', int(48*2**30/21233664), False),
                              ('All remote hits (optimistic bound)', 0, True)]:
        for links in (1, 2, 3):
            result = replay(rows, links, slots, hot)
            result['name'] = label
            result['predicted_tok_s'] = result['tokens'] / (result['io_seconds'] + result['tokens']*a.compute_ms_per_token/1000)
            scenarios.append(result)
    report = {
        'stage': 'S0', 'created_at': datetime.now(timezone.utc).isoformat(),
        'source': a.trace.name, 'source_sha256': hashlib.sha256(a.trace.read_bytes()).hexdigest(),
        'barriers': len(rows), 'tokens': len({r[0] for r in rows}), 'measured': False,
        'assumptions': {'record_bytes': 21233664, 'host_slots': 2846, 'local_gbs': 10,
                        'link_gbs': 4.4, 'node_ssd_gbs': 6, 'latency_ms': 1,
                        'non_io_ms_per_token': a.compute_ms_per_token},
        'limitations': ['All rates and non-I/O time are assumptions; these are sensitivity predictions, not benchmark results.',
                       'Cold host and node LRUs; no prefill warming, lookahead, SSD contention, socket copies or compute overlap.',
                       'Perfect knowledge of remote cache contents is optimistic. Whole-record store then forward is conservative.',
                       'Recorded GLM decode routes; variable-sized records are approximated as 20.25 MiB.',
                       '48 GiB is a simulated capacity, not permission to allocate or pin it on the dashboard host.'],
        'reference_tok_s': a.reference_tok_s, 'reference_scope': 'Historical four-drive result; not a matched control for these predictions.',
        'scenarios': scenarios,
    }
    a.output.mkdir(parents=True, exist_ok=True)
    (a.output/'S0-prediction.json').write_text(json.dumps(report, indent=2)+'\n')
    lines = ['# S0: pre-registered sensitivity prediction', '', 'Created '+report['created_at'], '',
             'Written before the S1 transport implementation and benchmark. No measured wire speed is claimed.', '',
             f"Trace: `{report['source']}` · SHA-256 `{report['source_sha256']}` · {report['tokens']} steps · {len(rows)} barriers.", '',
             'Assumptions: internal 10 GB/s; node SSD 6 GB/s; each link 4.4 GB/s; 1 ms per record; 100 ms non-I/O per token.', '',
             '| Node tier | Links | Predicted tok/s | I/O-only ceiling tok/s | Wire gets | Node hits |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in scenarios:
        lines.append(f"| {r['name']} | {r['links']} | {r['predicted_tok_s']:.3f} | {r['io_only_tok_s']:.3f} | {r.get('wire_gets',0)} | {r.get('node_hits',0)} |")
    lines += ['', *['- '+s for s in report['limitations']], '', 'The 3.70 tok/s historical champion is context only. A matched wire control still has to be run.']
    (a.output/'S0-prediction.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'tokens':report['tokens'], 'predictions':[(r['name'],r['links'],round(r['predicted_tok_s'],3)) for r in scenarios]}))


if __name__ == '__main__':
    main()
