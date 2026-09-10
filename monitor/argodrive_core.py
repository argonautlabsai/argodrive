"""Portable configuration and measurement primitives. No collection at import."""
from __future__ import annotations
import json
import math
import os
import plistlib
import re
import subprocess
from pathlib import Path

VERSION = '0.2.0-beta.1'


def load_config(path=None):
    if not path:
        return {}
    data = json.loads(Path(path).expanduser().read_text())
    if not isinstance(data, dict):
        raise ValueError('Configuration must be a JSON object')
    for field in ('runs', 'marker'):
        if field in data and not isinstance(data[field], str):
            raise ValueError(f'{field} must be a path string')
    drives = data.get('drives', [])
    if not isinstance(drives, list):
        raise ValueError('drives must be a list')
    ids = set()
    for drive in drives:
        if not isinstance(drive, dict):
            raise ValueError('Each drive must be an object')
        key = drive.get('id', '')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', key) or key in ids or key in ('TOTAL', 'M1'):
            raise ValueError('Each drive needs a unique id using letters, numbers, _ or - (not TOTAL/M1)')
        ids.add(key)
        if not isinstance(drive.get('path'), str) or not drive['path']:
            raise ValueError(f'{key}: a mount path is required')
        cap = drive.get('ceiling_gbps')
        if cap is not None and (not isinstance(cap, (float, int)) or isinstance(cap, bool) or not math.isfinite(cap) or cap <= 0):
            raise ValueError(f'{key}: ceiling_gbps must be positive or omitted')
    return data


def disk_info(path):
    out = subprocess.run(['diskutil', 'info', '-plist', str(path)], capture_output=True, timeout=5, check=True)
    info = plistlib.loads(out.stdout)
    # Some diskutil versions expose the physical store only in the text view.
    if info.get('APFSContainerReference') and not info.get('APFSPhysicalStores'):
        text = subprocess.run(['diskutil', 'info', str(path)], capture_output=True, text=True, timeout=5, check=True).stdout
        stores = re.findall(r'APFS Physical Store:\s*(disk\d+(?:s\d+)*)', text)
        info['APFSPhysicalStores'] = [{'DeviceIdentifier': d} for d in stores]
    return info


def physical_disk(info):
    stores = info.get('APFSPhysicalStores') or []
    if len(stores) > 1:
        return None  # A multi-store volume cannot be assigned to one physical disk.
    if info.get('APFSContainerReference') and not stores:
        return None
    value = (stores[0].get('DeviceIdentifier') if isinstance(stores[0],dict) else stores[0]) if stores else info.get('ParentWholeDisk') or info.get('DeviceIdentifier')
    return re.sub(r'(s\d+)+$', '', value) if value and re.fullmatch(r'disk\d+(s\d+)*', value) else None


def discover_drives(config, info_fn=disk_info, volume_paths=None):
    """Dedupe physical stores; discovery alone never reads model files."""
    specified = config.get('drives')
    if specified:
        candidates = specified
    else:
        if volume_paths is None:
            volume_paths = sorted(Path('/Volumes').iterdir()) if Path('/Volumes').exists() else []
        candidates = [{'id': 'internal', 'path': '/', 'label': 'Internal SSD'}]
        candidates += [{'path': str(p), 'label': p.name} for p in volume_paths]
    devices, descriptions, errors = {}, [], []
    for entry in candidates:
        path = os.path.expanduser(entry['path'])
        key = entry.get('id')
        info = {}
        try:
            info = info_fn(path)
            disk = physical_disk(info)
            if not disk:
                raise ValueError('No single physical disk resolved')
            if disk in devices:
                continue
            # Preserve known historical roles where possible; other drives use BSD ids.
            if not key:
                label = entry.get('label', disk)
                key = label if label in ('Green', 'White', 'Yellow') else disk
            devices[disk] = key
        except (OSError, ValueError, subprocess.SubprocessError, plistlib.InvalidFileException) as exc:
            if not specified and path != '/':
                continue
            key = key or 'internal'
            errors.append(f"{entry.get('label', key)}: {exc}")
            disk = None
        descriptions.append({'id': key, 'label': entry.get('label', key), 'path': path,
                             'device': disk, 'present': disk is not None,
                             'connection': entry.get('connection') or info.get('BusProtocol') or 'connection unknown',
                             'ceiling_gbps': entry.get('ceiling_gbps')})
    return devices, descriptions, errors


class CounterBuckets:
    """Whole, common time windows from cumulative device counters.

    Each device contributes bytes / its actual elapsed time. An aggregate is
    emitted only when EVERY expected disk has identical interval boundaries.
    Missing ticks, counter resets and sampler restarts never become fake zeros
    or sums of independent peaks. A bucket crossing a boundary is not split.
    """
    def __init__(self, devices, width=.2):
        self.devices = set(devices)
        self.width = width
        self.start = {}
        self.last = {}
        self.pending = {}

    def add(self, dev, t, value):
        if dev not in self.devices or not math.isfinite(t) or value < 0:
            return None
        prev = self.last.get(dev)
        if prev and (t <= prev[0] or value < prev[1]):
            self.start.pop(dev, None)
            self.pending.clear()
        self.last[dev] = (t, value)
        if dev not in self.start:
            self.start[dev] = (t, value)
            return None
        t0, b0 = self.start[dev]
        dt = t-t0
        if dt + 1e-6 < self.width:
            return None
        self.start[dev] = (t, value)
        rate = (value-b0)/1e9/dt
        # Sampler writes all disks with one timestamp; no nearest-neighbour matching.
        window = (round(t0, 6), round(t, 6))
        bucket = self.pending.setdefault(window, {})
        bucket[dev] = rate
        total = sum(bucket.values()) if set(bucket) == self.devices else None
        if total is not None:
            del self.pending[window]
        for old in list(self.pending):
            if t-old[1] > 2:
                del self.pending[old]
        return {'start': t0, 'end': t, 'seconds': dt, 'rate': rate, 'total': total}


def engine_processes(ps_text):
    out = []
    for line in ps_text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, rss, executable = parts
        name = os.path.basename(executable)
        if name in ('ds4', 'deltafin'):
            try:
                out.append({'pid': int(pid), 'engine': name, 'rss_gib': int(rss)*1024/2**30})
            except ValueError:
                pass
    return out


def chunk_progress(path, now=None):
    """ds4 harness chunks are response writes, not necessarily tokenizer tokens."""
    try:
        rows = []
        with open(path) as fh:
            for line in fh:
                if line.startswith('#'):
                    continue
                p = line.split()
                if len(p) >= 2:
                    rows.append((float(p[0]), float(p[1])))
        if not rows:
            return None
        latest = rows[-1][0]
        first = next((i for i, r in enumerate(rows) if latest-r[0] <= 10), 0)
        window = latest-rows[first][0]
        return {'source': 'ds4 response chunks', 'unit': 'chunks/s', 'count': len(rows),
                'rate': (len(rows)-first-1)/window if window > 0 else None,
                'first_wall': rows[0][1], 'last_wall': rows[-1][1]}
    except (OSError, ValueError):
        return None
