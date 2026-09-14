"""Read-only GGUF inspection and bounded, model-shaped application read tests.

Device counters are a separate measurement. F_NOCACHE is a policy, not proof
that every application byte reached NAND. Nothing in this module writes weights.
"""
import concurrent.futures
import fcntl
import hashlib
import math
import os
from pathlib import Path
import random
import re
import stat
import struct
import sys
import threading
import time

from argodrive_core import disk_info, physical_disk

BLOCK = 256 * 1024
MAX_DRIVES = 8


def calibration_groups(sources, topology):
    """Single-drive, distinct shared-uplink, then simultaneous selected-drive tests.

    Use verified physical membership, never model/name equality. A nested hub
    and port with the same members require one test, not two assumed capacities.
    """
    disks = [s['disk'] for s in sources]
    if not disks or len(disks) > MAX_DRIVES or len(set(disks)) != len(disks):
        raise ValueError('Select one source per physical drive, up to eight drives')
    groups = [dict(kind='single', disks=[disk], reason='Individual application read test') for disk in disks]
    shared = {}
    selected = set(disks)
    for link in topology.get('shared_uplinks', []):
        members = selected.intersection(link.get('physical_devices', []))
        if len(members) < 2: continue
        key = tuple(sorted(members))
        group = shared.setdefault(key, dict(kind='shared_uplink', disks=list(key),
            uplinks=[], reason='Simultaneous read test of members sharing a discovered uplink'))
        group['uplinks'].append(link['node_id'])
    groups.extend(shared.values())
    if len(disks) > 1:
        if tuple(sorted(disks)) in shared:
            shared[tuple(sorted(disks))]['also_all_selected'] = True
        else: groups.append(dict(kind='all_selected', disks=disks,
                                 reason='Simultaneous aggregate; do not sum single-drive peaks'))
    return groups


def file_identity(path):
    p = Path(path).expanduser().resolve(strict=True)
    s = p.stat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('Select a regular file: ' + str(p))
    return dict(path=str(p), bytes=s.st_size, device=s.st_dev, inode=s.st_ino,
                modified_ns=s.st_mtime_ns)


def inspect_gguf(path):
    """Read a bounded v2/v3 GGUF directory; derive Q4_K expert component sizes."""
    identity = file_identity(path)
    limit = min(identity['bytes'], 128 * 1024**2)
    with open(identity['path'], 'rb') as f:
        def read(n):
            if n < 0 or f.tell() + n > limit:
                raise ValueError('GGUF header exceeds the supported inspection bound')
            b = f.read(n)
            if len(b) != n: raise ValueError('Truncated GGUF header')
            return b
        def u32(): return struct.unpack('<I', read(4))[0]
        def u64(): return struct.unpack('<Q', read(8))[0]
        def string(keep=True):
            n = u64()
            if n > 16 * 1024**2: raise ValueError('Invalid GGUF string length')
            b = read(n)
            return b.decode('utf-8', 'strict') if keep else None
        sizes = {0:1, 1:1, 2:2, 3:2, 4:4, 5:4, 6:4, 7:1, 10:8, 11:8, 12:8}
        def value(kind, keep=False, depth=0):
            if depth > 2: raise ValueError('Nested GGUF metadata exceeds inspection bound')
            if kind == 8: return string(keep)
            if kind == 9:
                sub, count = u32(), u64()
                if count > 2_000_000: raise ValueError('GGUF array exceeds inspection bound')
                if sub in sizes: read(count * sizes[sub])
                else:
                    for _ in range(count): value(sub, False, depth+1)
                return None
            if kind not in sizes: raise ValueError('Unsupported GGUF metadata type')
            data = read(sizes[kind])
            return int.from_bytes(data, 'little') if keep else None
        if read(4) != b'GGUF' or u32() not in (2, 3):
            raise ValueError('Choose an unsharded GGUF v2/v3 model')
        nt, nk = u64(), u64()
        if not 0 < nt <= 200000 or nk > 200000: raise ValueError('Invalid GGUF directory counts')
        metadata = {}
        for _ in range(nk):
            key = string()
            keep = key in ('general.architecture', 'general.name', 'general.alignment', 'split.count')
            v = value(u32(), keep)
            if keep: metadata[key] = v
        if metadata.get('split.count', 1) != 1:
            raise ValueError('This adapter needs a single, merged GGUF file')
        tensors = []
        for _ in range(nt):
            name, nd = string(), u32()
            if not 1 <= nd <= 4: raise ValueError('Invalid GGUF tensor dimensions')
            dims = [u64() for _ in range(nd)]
            kind, offset = u32(), u64()
            if re.search(r'ffn_(gate|up|down)_exps\.weight$', name):
                if nd != 3 or kind != 12 or dims[0] % 256 or not all(0 < n <= 1000000 for n in dims):
                    raise ValueError('This first adapter supports uniform routed Q4_K experts')
                component = dims[0] * dims[1] // 256 * 144
                tensors.append(dict(name=name, offset=offset, component_bytes=component, experts=dims[2]))
        alignment = metadata.get('general.alignment', 32)
        if not isinstance(alignment, int) or alignment < 1 or alignment > 65536 or alignment & (alignment-1):
            raise ValueError('Invalid GGUF alignment')
        data_start = (f.tell()+alignment-1)//alignment*alignment
        f.seek(0)
        header_hash = hashlib.sha256(read(data_start)).hexdigest()
    if not tensors: raise ValueError('No supported routed Q4_K expert tensors found')
    for tensor in tensors:
        tensor['offset'] += data_start
        if tensor['offset'] + tensor['component_bytes'] * tensor['experts'] > identity['bytes']:
            raise ValueError('Expert tensor extends past the model file')
    if file_identity(path) != identity: raise ValueError('Model changed during inspection')
    return {**identity, 'header_sha256':header_hash, 'identity_scope':'GGUF header + local file stat; not a full weight hash',
            'architecture':metadata.get('general.architecture'), 'name':metadata.get('general.name'),
            'data_start':data_start, 'tensors':tensors,
            'component_sizes':sorted({t['component_bytes'] for t in tensors})}


def source_identity(path):
    identity = file_identity(path)
    info = disk_info(path)
    disk = physical_disk(info)
    if not disk: raise ValueError('Cannot resolve one physical SSD for ' + str(path))
    # Volume UUID is retained alongside the transient BSD identifier. Topology
    # changes require a new plan; this is not advertised as a hardware serial.
    return {**identity, 'disk':disk, 'volume_uuid':info.get('VolumeUUID'),
            'connection':info.get('BusProtocol'), 'volume':info.get('VolumeName'),
            'identity_scope':'volume UUID + physical BSD mapping + file stat'}


def split_pieces(length, weights, pieces, task_index=0):
    """Mirror the audited ds4 fork's split-read planner, including byte tails.

    Tied remainders rotate by the task's index within its submitted batch.
    Subdivision aligns every piece except the last down to 256 KiB, leaving
    the remainder in the last piece. Empty allocations produce no requests.
    This describes request geometry, not device speed or layer completion.
    """
    if (not 1 <= len(weights) <= MAX_DRIVES or len(weights) != len(pieces)
            or any(type(n) is not int or n < 1 or n > 100 for n in weights)
            or any(type(n) is not int or n < 1 or n > 8 for n in pieces)):
        raise ValueError('Provide positive weights and 1–8 pieces for each of up to eight drives')
    if type(length) is not int or not 0 < length <= (1 << 63) - 1:
        raise ValueError('Component length must be a positive byte count')
    if type(task_index) is not int or task_index < 0:
        raise ValueError('Task index must be a non-negative integer')
    blocks, tail, total = length // BLOCK, length % BLOCK, sum(weights)
    counts = [blocks * w // total for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (-(blocks*weights[i] % total), (i-task_index) % len(weights)))
    for i in order[:blocks-sum(counts)]: counts[i] += 1
    biggest = max(range(len(counts)), key=counts.__getitem__)
    shares = [n * BLOCK + (tail if i == biggest else 0) for i, n in enumerate(counts)]
    out, start = [], 0
    for share, sub in zip(shares, pieces):
        row, consumed = [], 0
        for j in range(sub):
            size = share - consumed if j + 1 == sub else (share // sub) // BLOCK * BLOCK
            if size:
                row.append([start + consumed, size]); consumed += size
        out.append(row)
        start += share
    return out


def uncached(fd):
    if sys.platform != 'darwin': raise ValueError('Uncached calibration currently requires macOS')
    fcntl.fcntl(fd, 48, 1)  # F_NOCACHE; failure aborts the test.


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values)-1, math.ceil(q*len(values))-1)] if values else None


def read_trial(sources, model, piece_plan, seconds, workers, cancel, progress, policy=uncached):
    """All worker starts/ends share one monotonic window. Bounded latency sample.

    Threads issue independent random expert components. This is an application
    read stress test, not an emulation of dependent GLM layers or a NAND ceiling.
    """
    if (not 0.1 <= seconds <= 30 or not 1 <= workers <= 16
            or not 1 <= len(sources) <= MAX_DRIVES or workers*len(sources) > 64):
        raise ValueError('Calibration limits: 0.1–30 seconds, up to eight drives, 16 workers/drive and 64 total')
    start_gate = threading.Event()
    guard = threading.Lock()
    stats = [dict(bytes=0, reads=0, latency=[], latency_seen=0) for _ in sources]
    descriptors, stop, errors = [], threading.Event(), []
    clock = {}
    def worker(i, lane):
        rng = random.Random(1701 + i*101 + lane)
        reservoir_rng = random.Random(2701+i*101+lane)
        start_gate.wait()
        while time.monotonic() < clock['deadline'] and not cancel.is_set() and not stop.is_set():
            tensor = rng.choice(model['tensors'])
            expert = rng.randrange(tensor['experts'])
            relative, size = rng.choice(piece_plan[tensor['component_bytes']][i])
            offset = tensor['offset'] + expert*tensor['component_bytes'] + relative
            try:
                t = time.monotonic()
                payload = os.pread(descriptors[i], size, offset)
                elapsed = (time.monotonic()-t)*1000
                if len(payload) != size: raise OSError(f'Short read on {sources[i]["disk"]}: {len(payload)}/{size}')
                with guard:
                    s = stats[i]; s['bytes'] += size; s['reads'] += 1; s['latency_seen'] += 1
                    if len(s['latency']) < 8192: s['latency'].append(elapsed)
                    else:
                        j = reservoir_rng.randrange(s['latency_seen'])
                        if j < 8192: s['latency'][j] = elapsed
            except Exception as exc:
                with guard: errors.append(str(exc))
                stop.set()
    try:
        for source in sources:
            fd = os.open(source['path'], os.O_RDONLY)
            descriptors.append(fd)
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or
                    (opened.st_size, opened.st_dev, opened.st_ino, opened.st_mtime_ns) !=
                    (source['bytes'], source['device'], source['inode'], source['modified_ns'])):
                raise ValueError('Selected file changed before opening the read descriptor')
            policy(fd)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers*len(sources)) as pool:
            futures = [pool.submit(worker,i,j) for i in range(len(sources)) for j in range(workers)]
            t0 = time.monotonic(); clock['deadline'] = t0+seconds; start_gate.set()
            while not all(f.done() for f in futures):
                with guard:
                    now = time.monotonic()
                    progress(dict(start=t0, elapsed_s=now-t0, devices=[
                        dict(disk=src['disk'], bytes=s['bytes'], reads=s['reads'], gbps=s['bytes']/1e9/max(.001,now-t0))
                        for src,s in zip(sources,stats)]))
                cancel.wait(.2) if not cancel.is_set() else time.sleep(.05)
            for f in futures: f.result()
        t1 = time.monotonic()
        if errors: raise OSError('; '.join(errors[:4]))
        if cancel.is_set(): raise InterruptedError('Calibration stopped; partial result is not valid')
        for source in sources:
            if any(file_identity(source['path'])[k] != source[k] for k in ('bytes','device','inode','modified_ns')):
                raise ValueError('A selected weight file changed during calibration')
        devices = []
        for source,s in zip(sources,stats):
            if not s['reads']: raise ValueError('No successful reads for a selected drive')
            devices.append(dict(disk=source['disk'], bytes=s['bytes'], reads=s['reads'],
                gbps=s['bytes']/1e9/(t1-t0), p50_ms=percentile(s['latency'],.5),
                p95_ms=percentile(s['latency'],.95), latency_sample_n=len(s['latency'])))
        return dict(start=t0, end=t1, seconds=t1-t0, devices=devices,
            aggregate_gbps=sum(s['bytes'] for s in stats)/1e9/(t1-t0),
            source='Application pread payload / shared wall time; includes Python orchestration',
            cache_policy='F_NOCACHE enabled', physical_read_bytes=None,
            physical_note='Device counters are reported separately; no one-to-one byte attribution')
    finally:
        stop.set(); start_gate.set()
        for fd in descriptors: os.close(fd)
