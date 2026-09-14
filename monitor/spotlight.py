"""On-demand Spotlight policy checks, scoped to one identified mounted volume.

No checks at import time, no indexing changes on discovery, no passwords handled
by the app. A change uses macOS administrator authentication, then reads back the
setting. This measures indexing policy, not current indexing activity or speed.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time

from argodrive_core import disk_info, physical_disk


def indexing_state(output, returncode=0):
    # A stopped/unreachable service (including the development sandbox) does
    # not establish any individual volume's indexing policy.
    text = output.lower()
    if returncode or any(x in text for x in ('error:', 'unknown indexing state',
            'spotlight server is disabled', 'could not', 'operation not permitted')):
        return 'unavailable'
    if re.search(r'^\s*indexing and searching disabled\.?\s*$', text, re.M):
        return 'search_disabled'
    if re.search(r'^\s*indexing disabled\.?\s*$', text, re.M): return 'disabled'
    if re.search(r'^\s*indexing enabled\.?\s*$', text, re.M): return 'enabled'
    return 'unavailable'


def volume_identity(path):
    p = Path(path).expanduser().resolve(strict=True)
    info = disk_info(p)
    mount = info.get('MountPoint')
    if not mount or Path(mount).resolve() != p:
        raise ValueError('The drive is no longer mounted at the recorded path')
    disk = physical_disk(info)
    uuid = info.get('VolumeUUID', '')
    if not disk or not re.fullmatch(r'[0-9a-fA-F-]{16,64}', uuid):
        raise ValueError('A local physical volume with a stable UUID is required')
    return dict(path=str(p), volume_uuid=uuid, disk=disk,
                label=info.get('VolumeName') or p.name or 'Macintosh HD',
                internal=bool(info.get('Internal', p == Path('/'))),
                writable=info.get('Writable', True) is not False,
                system=p == Path('/'))


def status_for(identity, run=subprocess.run):
    r = run(['/usr/bin/mdutil', '-s', identity['path']], capture_output=True,
            text=True, timeout=8, env={**os.environ, 'LC_ALL':'C', 'LANG':'C'})
    output = (r.stdout + '\n' + r.stderr).strip()
    state = indexing_state(output, r.returncode)
    return {**identity, 'state':state, 'observed_at':time.time(), 'evidence':output[:2000],
            'can_change':state in ('enabled','disabled') and identity['writable']}


def applescript_for(identity, enabled):
    if type(enabled) is not bool: raise ValueError('enabled must be a boolean')
    # Shell and AppleScript are separate quoting layers. Re-check the UUID
    # AFTER the administrator dialog, so a replug while it is open fails closed.
    path, uuid = shlex.quote(identity['path']), shlex.quote(identity['volume_uuid'])
    command = (
        f'argodrive_volume_uuid=$(/usr/sbin/diskutil info -plist {path} | '
        '/usr/bin/plutil -extract VolumeUUID raw -o - -) || exit 73\n'
        f'if [ "$argodrive_volume_uuid" != {uuid} ]; then '
        'echo "The mounted volume changed. Refresh Spotlight status and try again." >&2; exit 73; fi\n'
        f'/usr/bin/mdutil -i {"on" if enabled else "off"} {path}')
    literal = command.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')
    return f'do shell script "{literal}" with administrator privileges'


class Spotlight:
    def __init__(self, state_dir, identify=volume_identity, run=subprocess.run, paths=None):
        self.state_dir = Path(state_dir)
        self.identify, self.run = identify, run
        self.paths = paths or self._paths
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.rows, self.checked_at, self.job = [], None, None
        self.thread = None

    @staticmethod
    def _paths():
        roots = [Path('/')]
        if Path('/Volumes').is_dir(): roots += sorted(Path('/Volumes').iterdir())
        # Spotlight is per volume. Do not deduplicate different APFS volumes
        # on one physical SSD; only aliases of the same mount point.
        seen, out = set(), []
        for p in roots:
            try:
                resolved = str(p.resolve(strict=True))
                if p.is_dir() and resolved not in seen:
                    seen.add(resolved); out.append(resolved)
            except OSError: pass
        return out[:32]

    def _inspect(self, path):
        try:
            row = status_for(self.identify(path), self.run)
            row['id'] = hashlib.sha256((row['path']+'\0'+row['volume_uuid']).encode()).hexdigest()[:24]
            return row
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return dict(id=hashlib.sha256(str(path).encode()).hexdigest()[:24], path=str(path),
                label=Path(path).name or 'Internal SSD', state='unavailable', can_change=False,
                observed_at=time.time(), evidence=str(exc)[:2000])

    def scan(self):
        if not self.scan_lock.acquire(blocking=False): return self.snapshot()
        try:
            with self.lock:
                if self.job and self.job['status'] == 'running': return self.snapshot()
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(self._inspect, self.paths()))
            with self.lock:
                self.rows, self.checked_at = rows, time.time()
            return self.snapshot()
        finally: self.scan_lock.release()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(dict(volumes=self.rows, checked_at=self.checked_at, job=self.job,
                scanning=self.scan_lock.locked(),
                note='Indexing enabled is a policy, not evidence that Spotlight is currently reading the drive.'))

    def change(self, ident, enabled):
        if not isinstance(ident,str) or type(enabled) is not bool:
            raise ValueError('Select a scanned volume and an explicit indexing setting')
        with self.lock:
            if self.scan_lock.locked(): raise ValueError('Wait for the drive check to finish')
            if self.job and self.job['status']=='running': raise ValueError('A Spotlight change is already pending')
            row = next((r for r in self.rows if r['id']==ident), None)
            if not row or not row['can_change']: raise ValueError('Refresh and select a volume with a confirmed indexing status')
            expected = 'enabled' if enabled else 'disabled'
            if row['state'] == expected: raise ValueError('That indexing setting is already reported')
            self.job = dict(id=ident, label=row['label'], path=row['path'], enabled=enabled,
                previous=row['state'], status='running', started_at=time.time(),
                message='Waiting for macOS administrator authentication. Enter your password only in the macOS dialog.')
            self.thread = threading.Thread(target=self._change, args=(copy.deepcopy(row),enabled), daemon=True)
            self.thread.start()
            return self.snapshot()

    def _change(self, row, enabled):
        status, message, observed = 'failed', '', None
        try:
            current = self.identify(row['path'])
            if any(current[k] != row[k] for k in ('path','volume_uuid','disk')):
                raise ValueError('The volume changed since the last check. Refresh before changing indexing.')
            before = status_for(current,self.run)
            if before['state'] != row['state'] or not before['can_change']:
                raise ValueError('Indexing status changed since the last check. Refresh before continuing.')
            result = self.run(['/usr/bin/osascript','-e',applescript_for(current,enabled)],
                capture_output=True, text=True, timeout=180)
            detail = (result.stdout+'\n'+result.stderr).strip()[:2000]
            observed = self._inspect(row['path'])
            if result.returncode:
                status = 'cancelled' if '-128' in detail or 'user canceled' in detail.lower() else 'failed'
                message = 'Administrator authentication was cancelled.' if status=='cancelled' else detail or 'macOS refused the change.'
            elif observed.get('volume_uuid') != row['volume_uuid'] or observed['state'] != ('enabled' if enabled else 'disabled'):
                status, message = 'unverified', 'The command returned, but the requested state could not be confirmed. Refresh to check it.'
            else:
                status = 'complete'
                message = f'Indexing {"enabled" if enabled else "disabled"} on {row["label"]}; confirmed by mdutil.'
        except subprocess.TimeoutExpired:
            status, message = 'unverified', 'Authentication or the command timed out. Refresh to check the drive before retrying.'
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            message = str(exc)
        finally:
            if observed is None: observed = self._inspect(row['path'])
            with self.lock:
                # Replace stale evidence even on cancellation/error; never leave
                # a disconnected or substituted drive with an actionable button.
                self.rows = [observed if r['id']==row['id'] else r for r in self.rows]
                self.checked_at = time.time()
                self.job.update(status=status, message=message, finished_at=time.time())
                record = {**self.job, 'volume_uuid':row['volume_uuid'], 'observed_state':observed['state']}
                try:
                    self.state_dir.mkdir(parents=True,exist_ok=True)
                    with (self.state_dir/'spotlight-changes.jsonl').open('a') as f:
                        f.write(json.dumps(record)+'\n')
                except OSError as exc:
                    self.job['history_error'] = str(exc)
