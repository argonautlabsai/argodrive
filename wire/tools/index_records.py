#!/usr/bin/env python3
"""Index existing weight bytes; never repack or rewrite weights.

k3 selects a bounded sample of the existing L<layer>-E<expert>.bin store.
spans accepts [{layer,expert,spans:[{path,offset,length}]}], in gate/up/down order.
The manifest binds the logical record hashes; transport reads only these spans.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import re


def write_index(records, output):
    if not records:
        raise ValueError('Empty index')
    output.mkdir(parents=True, exist_ok=True)
    seen = set()
    lines, manifest = [], []
    sources = set()
    for r in sorted(records, key=lambda x:(x['layer'], x['expert'])):
        key = r['layer'], r['expert']
        if key in seen or any(type(x) is not int or not 0 <= x <= 0xffffffff for x in key):
            raise ValueError('Duplicate or invalid record ID')
        seen.add(key)
        digest = hashlib.sha256()
        length = 0
        if not 1 <= len(r['spans']) <= 16:
            raise ValueError('A record must contain 1–16 spans')
        for s in r['spans']:
            path = Path(s['path']).resolve(strict=True)
            offset, size = s['offset'], s['length']
            if any(type(x) is not int for x in (offset,size)) or offset < 0 or size <= 0 or offset+size > path.stat().st_size:
                raise ValueError('Invalid source range')
            if any(c in str(path) for c in '\t\n\r'):
                raise ValueError('Tabs and newlines are unsupported in source paths')
            length += size
            if length > 64*1024**2:
                raise ValueError('Record exceeds S1 64 MiB limit')
            sources.add(path)
            with path.open('rb') as f:
                f.seek(offset)
                remaining = size
                while remaining:
                    chunk = f.read(min(4*1024**2, remaining))
                    if not chunk:
                        raise ValueError('Source shortened during index creation')
                    digest.update(chunk)
                    remaining -= len(chunk)
            lines.append(f"{key[0]}\t{key[1]}\t{offset}\t{size}\t{path}")
        manifest.append({'layer':key[0], 'expert':key[1], 'length':length, 'sha256':digest.hexdigest()})
    if len(sources) > 240:
        raise ValueError('S1 supports at most 240 source files; use a bounded pack sample')
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    (output/'records.index').write_text('AWRINDEX2\t'+identity+'\n'+'\n'.join(lines)+'\n')
    (output/'manifest.json').write_text(json.dumps({'identity':identity, 'records':manifest}, indent=2)+'\n')
    return {'identity':identity, 'records':len(manifest), 'source_files':len(sources), 'bytes':sum(r['length'] for r in manifest)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['k3','spans'])
    p.add_argument('source', type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--limit', type=int, default=200)
    a=p.parse_args()
    if a.mode == 'spans':
        records=json.loads(a.source.read_text())
    else:
        files=sorted(a.source.glob('L*-E*.bin'))
        random.Random(20260911).shuffle(files)
        records=[]
        for path in files[:a.limit]:
            match=re.fullmatch(r'L(\d+)-E(\d+)\.bin',path.name)
            if match:
                layer,expert=map(int,match.groups())
                records.append({'layer':layer,'expert':expert,'spans':[{'path':str(path),'offset':0,'length':path.stat().st_size}]})
    print(json.dumps(write_index(records,a.output)))


if __name__=='__main__':main()
