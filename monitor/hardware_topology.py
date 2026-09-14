"""Read-only macOS connection inventory. No collection at import time.

Topology is discovered on demand, never in the 100 ms sampler. Hardware UUIDs
are used transiently to join the Thunderbolt and storage trees, then discarded.
"""
import json
import hashlib
import re
import subprocess
import threading
import time
from argodrive_core import discover_drives


def uid(value):
    value = str(value or '').lower().removeprefix('0x')
    return value.zfill(16) if re.fullmatch(r'[0-9a-f]{1,16}', value) else None


def storage_inventory(text):
    """Join whole IOMedia disks to the nearest PCI tunnel endpoint GUID.

    Only parse short, allow-listed properties; IORegistry contains large binary
    properties and private metadata that must not enter the UI or its exports.
    """
    stack, disks = [], []
    allowed = {'BSD Name', 'Whole', 'Model Number', 'Serial Number', 'Size', 'Tunnel Endpoint GUID',
               'Physical Interconnect Location', 'Physical Interconnect'}
    for line in text.splitlines():
        if len(line) > 2048:
            continue
        if '+-o ' in line:
            m = re.match(r'^(.*?)\+-o (.+?)  <class ([^,]+),', line)
            if m:
                depth = len(m[1])
                stack = [n for n in stack if n['depth'] < depth]
                stack.append({'depth': depth, 'name': m[2], 'class': m[3], 'props': {}})
            continue
        m = re.match(r'^[ |]*"([^"]+)" = (.+)$', line)
        if not m or not stack or m[1] not in allowed:
            continue
        stack[-1]['props'][m[1]] = m[2].strip('"')
        # BSD Name arrives before some other media properties. The strict
        # whole-disk name and IOMedia class also exclude partition/APFS slices.
        if m[1] == 'BSD Name' and re.fullmatch(r'disk\d+', m[2].strip('"')) and stack[-1]['class'] == 'IOMedia' and len(stack)>1 and stack[-2]['class'] == 'IOBlockStorageDriver':
            tunnel = next((n['props']['Tunnel Endpoint GUID'] for n in reversed(stack)
                           if 'Tunnel Endpoint GUID' in n['props']), None)
            model = next((n['props']['Model Number'] for n in reversed(stack)
                          if 'Model Number' in n['props']), stack[-1]['name'].removesuffix(' Media'))
            if 'disk image' in model.lower() or any('HDIX' in n['class'] for n in stack):
                continue
            endpoint = None
            if tunnel and re.fullmatch(r'<[0-9a-fA-F]{16}>', tunnel):
                endpoint = uid(bytes.fromhex(tunnel[1:-1])[::-1].hex())
            serial = next((n['props']['Serial Number'].strip() for n in reversed(stack)
                           if 'NVMe' in n['class'] and 'Serial Number' in n['props']), None)
            # A physical identity survives BSD-number, mount-name and port changes.
            # Do not expose raw hardware serials in the public map or SVG export.
            identity = 'nvme-serial-sha256:' + hashlib.sha256(serial.encode()).hexdigest() if serial else None
            disks.append({'device': m[2].strip('"'), 'model': model, 'identity':identity,
                          '_endpoint': endpoint,
                          'internal': any('EmbeddedNVMe' in n['class'] for n in stack)})
    return disks


def build_topology(profiler, storage, drives):
    hw = (profiler.get('SPHardwareDataType') or [{}])[0]
    host = {'id': 'mac', 'kind': 'mac', 'label': hw.get('machine_name', 'This Mac'),
            'model': hw.get('chip_type') or hw.get('machine_model', ''),
            'memory': hw.get('physical_memory'), 'parent': None, 'drive_ids': [],
            'source': 'macOS System Information'}
    nodes, edges = [host], []
    endpoints = {}

    def add(node, parent, speed=None, status='connected', link_kind='cable'):
        node.update(parent=parent, drive_ids=[], source='macOS System Information')
        nodes.append(node)
        edges.append({'id': parent+'--'+node['id'], 'source': parent, 'target': node['id'],
                      'speed_label': speed, 'status': status, 'kind': link_kind,
                      'latency_ms': None})

    def port_info(item):
        ports = []
        for key, val in item.items():
            if key.startswith('receptacle') and isinstance(val, dict):
                ports.append({'label': 'Upstream' if 'upstream' in key else 'Port '+str(val.get('receptacle_id_key') or re.sub(r'\D', '', key) or '?'),
                              'speed': val.get('current_speed_key'),
                              'connected': val.get('receptacle_status_key') == 'receptacle_connected'})
        return ports

    def device(item, parent, path):
        label = item.get('device_name_key') or item.get('_name') or 'Thunderbolt device'
        ports = port_info(item)
        kind = 'hub' if re.search(r'hub|dock', label, re.I) or item.get('_items') else 'device'
        mode = {'usb_four_v2': 'USB4 v2', 'usb_four': 'USB4', 'thunderbolt': 'Thunderbolt'}.get(item.get('mode_key'), 'Thunderbolt / USB4')
        upstream = next((p for p in ports if p['label'] == 'Upstream'), {})
        node = {'id': path, 'kind': kind, 'label': label, 'vendor': item.get('vendor_name_key'),
                'protocol': mode, 'ports': ports, 'route': item.get('route_string_key')}
        add(node, parent, upstream.get('speed'))
        identity = uid(item.get('switch_uid_key'))
        if identity:
            endpoints.setdefault(identity, []).append(node)
        for i, child in enumerate(item.get('_items') or []):
            device(child, path, path+'-'+str(i+1))

    buses = profiler.get('SPThunderboltDataType') or []
    for i, bus in enumerate(sorted(buses, key=lambda b: b.get('_name', ''))):
        ports = port_info(bus)
        p = next((p for p in ports if p['label'] != 'Upstream'), {})
        ident = 'port-'+str(i+1)
        present = bool(bus.get('_items'))
        node = {'id': ident, 'kind': 'port', 'label': p.get('label', 'Bus '+str(i+1)),
                'bus': bus.get('_name'), 'ports': ports, 'connected': present,
                'protocol': 'Thunderbolt / USB4'}
        add(node, 'mac', p.get('speed') if present else None,
            'connected' if present else 'empty', 'host-port')
        for j, child in enumerate(bus.get('_items') or []):
            device(child, ident, ident+'-'+str(j+1))

    described = {d['device']: d for d in drives if d.get('present') and d.get('device')}
    seen = set()
    for disk in storage:
        dev = disk['device']
        if dev in seen:
            continue
        seen.add(dev)
        d = described.get(dev, {})
        matches = endpoints.get(disk['_endpoint'], []) if disk['_endpoint'] else []
        if len(matches) == 1:
            node = matches[0]
            if node['kind'] == 'device': node['kind'] = 'enclosure'
            node.setdefault('storage', []).append({'device': dev, 'model': disk['model'],
                 'label': d.get('label', dev), 'path': d.get('path'), 'identity':disk.get('identity'),
                 'source': 'IORegistry tunnel identity'})
        else:
            internal = disk['internal'] or d.get('path') == '/'
            node = {'id': 'storage-'+dev, 'kind': 'ssd', 'label': d.get('label', 'Internal SSD' if internal else dev),
                    'model': disk['model'], 'storage': [{'device': dev, 'model': disk['model'],
                    'label': d.get('label', dev), 'path': d.get('path'), 'identity':disk.get('identity'),
                    'source': 'IORegistry whole disk'}],
                    'unresolved': not internal}
            add(node, 'mac', None, 'internal' if internal else 'unresolved', 'internal' if internal else 'unresolved')
        if d: node['drive_ids'].append(d['id'])
        node['mapping'] = 'IORegistry tunnel identity' if len(matches)==1 else 'Internal storage' if not node.get('unresolved') else 'Enclosure association unavailable'
    for dev, d in described.items():
        if dev in seen: continue
        node = {'id': 'storage-'+dev, 'kind': 'ssd', 'label': d['label'], 'unresolved': d.get('path') != '/',
                'storage': [{'device': dev, 'label': d['label'], 'path': d.get('path')}], 'mapping': 'Enclosure association unavailable'}
        internal = d.get('path') == '/'
        add(node, 'mac', None, 'internal' if internal else 'unresolved', 'internal' if internal else 'unresolved')
        node['drive_ids'] = [d['id']]
    return {'nodes': nodes, 'edges': edges, 'ports': len(buses), 'shared_uplinks':shared_uplinks(nodes, edges),
            'source': 'macOS System Information + IORegistry', 'collected_at': time.time()}


def shared_uplinks(nodes, edges):
    """Detected ancestry establishes sharing; it does not establish throughput.

    Nested hubs retain every constraint. A port and its downstream hub can have
    the same members; these are nested links, never additive bandwidth pools.
    Unresolved storage, matching SSD models and volume colours imply no sharing.
    """
    children = {}
    for n in nodes: children.setdefault(n.get('parent'), []).append(n)
    incoming = {e['target']:e for e in edges}
    groups = []
    for parent in nodes:
        if parent['kind'] not in ('hub','port'): continue
        pending, visited, drives, devices = list(children.get(parent['id'], [])), set(), set(), set()
        while pending:
            n = pending.pop()
            if n['id'] in visited: continue
            visited.add(n['id'])
            if n.get('unresolved'): continue
            drives.update(n.get('drive_ids', []))
            devices.update(s['device'] for s in n.get('storage', []) if s.get('device'))
            pending.extend(children.get(n['id'], []))
        if len(devices) < 2 or len(drives) < 2: continue
        groups.append(dict(id='uplink-'+parent['id'], node_id=parent['id'], kind=parent['kind'],
            label=parent['label'], drive_ids=sorted(drives), physical_devices=sorted(devices),
            reported_link=incoming.get(parent['id'],{}).get('speed_label'),
            calibrated_gbps=None, source='Discovered ancestry of mapped physical drives',
            note='Calibrate members simultaneously. Individual drive peaks are not an uplink limit.'))
    return groups


class TopologyInventory:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None
        self.signature = None

    def snapshot(self, drives, reports_only=False, force=False, config=None):
        # Opening Topology is an on-demand metadata request. Reports mode must
        # not start the sampler, but it can discover wiring and retain it until
        # the user explicitly rescans. Do not modify the live collector's map.
        signature = (reports_only,
                     tuple(sorted((d['id'], d.get('device'), d.get('path')) for d in drives)),
                     json.dumps(config or {}, sort_keys=True) if reports_only else None)
        with self.lock:
            if (self.cached and not force and self.signature == signature and
                    (reports_only or time.time()-self.cached['collected_at'] < 60)):
                return self.cached
            errors = []
            if reports_only:
                _, drives, discovery_errors = discover_drives(config or {})
                errors.extend(discovery_errors)
            try:
                result = subprocess.run(['/usr/sbin/system_profiler', 'SPThunderboltDataType', 'SPHardwareDataType', '-json'],
                                        capture_output=True, text=True, timeout=20, check=True)
                profiler = json.loads(result.stdout)
                if not profiler.get('SPHardwareDataType'):
                    errors.append('macOS hardware details are unavailable; the connection map may be incomplete.')
            except (OSError, ValueError, subprocess.SubprocessError):
                profiler = {}; errors.append('macOS connection inventory is unavailable.')
            try:
                result = subprocess.run(['/usr/sbin/ioreg', '-l', '-w', '0'], capture_output=True, text=True, timeout=15, check=True)
                storage = storage_inventory(result.stdout)
            except (OSError, ValueError, subprocess.SubprocessError):
                storage = []; errors.append('Storage-to-enclosure associations are unavailable.')
            self.cached = build_topology(profiler, storage, drives)
            self.cached.update(mode='snapshot' if reports_only else 'live', errors=errors,
                               sampling_enabled=not reports_only)
            self.signature = signature
            return self.cached
