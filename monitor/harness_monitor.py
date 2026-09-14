"""Read existing GLM harness and ds4-bench streams. Never starts collectors or an engine."""
from collections import defaultdict, deque
import json
import math
from pathlib import Path
import re
import time

from argodrive_core import CounterBuckets


def tail_text(path, limit=65536):
    try:
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size-limit))
            return f.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return ''


def json_file(path):
    try:
        if path.stat().st_size > 2_000_000:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class Lines:
    """Incremental, bounded reader. A partial final line waits for its newline."""
    def __init__(self):
        self.identity = None
        self.offset = 0
        self.pending = b''

    def read(self, path):
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            reset = identity != self.identity or stat.st_size < self.offset
            if reset:
                self.identity, self.offset, self.pending = identity, 0, b''
            with path.open('rb') as f:
                f.seek(self.offset)
                data = f.read(2_000_000)
                self.offset = f.tell()
            parts = (self.pending+data).split(b'\n')
            self.pending = parts.pop()[-65536:]
            return [p.decode('utf-8', errors='replace') for p in parts], reset, stat.st_mtime, self.offset == stat.st_size
        except OSError:
            return [], False, None, False


class EngineStages:
    """Last reported stage, not a phase guessed from disk utilization or elapsed time."""
    def __init__(self):
        self.reader = Lines()
        self.current = 'unknown'
        self.seen = set()
        self.progress = None
        self.timings = {}

    def sample(self, path, chunks, done, exit_code, fresh):
        rows, reset, _, caught_up = self.reader.read(path)
        if reset:
            self.current = 'unknown'; self.seen.clear(); self.progress = None; self.timings = {}
        for line in rows:
            if line.startswith('ds4: Metal device '):
                self.current = 'setup'; self.seen.add('setup')
            progress = re.search(r'processing (\d+) input tokens: (\d+)/(\d+)', line)
            if progress:
                total, processed, denominator = map(int, progress.groups())
                if total > 0 and total == denominator and 0 <= processed <= total:
                    self.current = 'prefill'; self.seen.add('prefill')
                    self.progress = {'processed': processed, 'total': total}
            if re.search(r'ds4: prefill \d+/\d+ done|Metal memory after GLM prefill:', line):
                self.current = 'prefill'; self.seen.add('prefill')
            counts = re.search(r'generation counts: generated=(\d+) requested=(\d+) decode_seconds=([\d.eE+-]+) prompt_tokens=(\d+) prefill_seconds=([\d.eE+-]+)', line)
            if counts:
                generated, requested, decode, prompt, prefill = counts.groups()
                try: decode, prefill = float(decode), float(prefill)
                except ValueError: continue
                if all(math.isfinite(v) and v >= 0 for v in (decode, prefill)):
                    self.timings = {'generated_tokens': int(generated), 'requested_tokens': int(requested),
                                    'prompt_tokens': int(prompt), 'decode_seconds': decode, 'prefill_seconds': prefill}
                    self.seen.update(('prefill', 'decode')); self.current = 'finalizing'
        if chunks and self.current != 'finalizing':
            self.current = 'decode'; self.seen.add('decode')
        current = ('complete' if exit_code == '0' else 'failed') if done else self.current
        return {'current': current, 'reported': self.current, 'seen': sorted(self.seen),
                'live': bool(fresh and caught_up and not done), 'progress': self.progress,
                'timings': self.timings, 'source': 'ds4 stderr and response capture',
                'note': 'Short prompts may report prefill only at completion. Stage timestamps are not recorded.'}


class ArmStream:
    def __init__(self, base):
        self.base = base
        self.stages = EngineStages()
        self.csv = Lines()
        self.chunks = Lines()
        self.chunk_rows = deque(maxlen=4096)
        self.count = 0
        self.maps = {}
        self.windows = defaultdict(lambda: deque(maxlen=900))
        self.buckets = CounterBuckets([])

    def sample(self, now):
        text = tail_text(Path(str(self.base)+'.map'))
        maps = {dev: name for name, dev in re.findall(r'([\w-]+)=(disk\d+)\b', text)}
        rows, reset, modified, caught_up = self.csv.read(Path(str(self.base)+'.csv'))
        if reset or maps != self.maps:
            self.maps = maps
            self.windows.clear()
            self.buckets = CounterBuckets(maps)
        for row in rows:
            try:
                p = row.split(',')
                if len(p) < 3 or p[1] not in maps:
                    continue
                t, value = float(p[0]), int(p[2])
                if not math.isfinite(t) or t < 0 or value < 0:
                    continue
                old = self.buckets.last.get(p[1])
                if old and (t <= old[0] or value < old[1]):
                    # A new counter epoch cannot share an average with the old one.
                    self.windows.clear()
                    self.buckets = CounterBuckets(maps)
                item = self.buckets.add(p[1], t, value)
                if item:
                    point = [item['end'], item['rate'], item['seconds']]
                    self.windows[maps[p[1]]].append(point)
                    if item['total'] is not None:
                        self.windows['TOTAL'].append([item['end'], item['total'], item['seconds']])
            except (ValueError, IndexError):
                continue
        chunks, reset_chunks, _, _ = self.chunks.read(Path(str(self.base)+'.chunks'))
        if reset_chunks:
            self.chunk_rows.clear()
            self.count = 0
        for line in chunks:
            try:
                if line.startswith('#'):
                    continue
                p = line.split()
                mono, wall = float(p[0]), float(p[1])
                if not all(math.isfinite(v) for v in (mono, wall)):
                    continue
                self.chunk_rows.append((mono, wall))
                self.count += 1
            except (ValueError, IndexError):
                continue
        log = tail_text(Path(str(self.base)+'.log'))
        ends = re.findall(r'^ARM .+ END .* rc=(\d+)', log, re.M)
        done = bool(ends)
        age = max(0, now-modified) if modified else None
        fresh = not done and caught_up and age is not None and age < 5
        recent = [p for p in self.chunk_rows if 0 <= now-p[1] < 10]
        duration = recent[-1][0]-recent[0][0] if len(recent) > 1 else 0
        rate = (len(recent)-1)/duration if duration > 0 and fresh else None
        state = ('Completed' if ends[-1] == '0' else 'Failed') if done else (
            'Receiving responses' if fresh and recent else 'Collecting · waiting for responses' if fresh else 'Waiting / stale')
        summary = {}
        for line in log.splitlines():
            if line.startswith('{'):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get('tag') == self.base.name:
                        summary = obj
                except ValueError:
                    pass
        wanted = re.search(r'\btokens=(\d+)', log)
        end = max((s[-1][0] for s in self.windows.values() if s), default=0)
        windows = {k: [p for p in s if p[0] > end-120] for k, s in self.windows.items()}
        memory = None
        for line in reversed(tail_text(Path(str(self.base)+'.sys')).splitlines()):
            try:
                v = [float(x) for x in line.split()]
                if len(v) >= 8 and all(math.isfinite(x) for x in v):
                    memory = dict(cpu_percent=v[0], used_gib=v[1], available_gib=v[2], gpu_percent=v[5], swap_mb=v[4])
                    break
            except ValueError:
                pass
        stage = self.stages.sample(Path(str(self.base)+'.err'), self.count, done, ends[-1] if done else None, fresh)
        return {'state': state, 'done': done, 'fresh': fresh, 'sample_age_s': age, 'stage': stage,
                'catching_up': not caught_up and modified is not None,
                'chunks': self.count, 'response_rate': rate, 'requested_tokens': int(wanted[1]) if wanted else None,
                'summary': {k: summary.get(k) for k in ('ds4_gen_tps', 'ds4_prefill_tps', 'first_byte_s', 'valid')},
                'memory': memory, 'read_windows': windows, 'seconds': end,
                'devices': [{'id': name, 'label': name, 'device': dev} for dev, name in maps.items()]}


def benchmark_record(log):
    for line in reversed(log.splitlines()):
        for prefix, done in [('ARGODRIVE_BENCH_RESULT ', True), ('ARGODRIVE_BENCH_START ', False)]:
            if not line.startswith(prefix):
                continue
            try:
                record = json.loads(line[len(prefix):])
                if not isinstance(record, dict):
                    continue
                if any(type(record.get(k)) is not int or record[k] <= 0 for k in ('prompt_tokens','generated_tokens')):
                    continue
                if done and any(type(record.get(k)) not in (int,float) or not math.isfinite(record[k]) or record[k] <= 0
                                for k in ('generation_tok_s','steady_tok_s','prefill_tok_s','first_decode_step_ms')):
                    continue
                return record, done
            except (ValueError, TypeError):
                continue
    return None, False


class BenchmarkStream(ArmStream):
    def sample(self, now):
        sample = super().sample(now)
        record, done = benchmark_record(tail_text(Path(str(self.base)+'.log')))
        if not record:
            return sample
        status = json_file(Path(str(self.base)+'.run.json'))
        if not status and self.base.name == 'baseline':
            status = json_file(self.base.parent/'run.json')
        stopped = status.get('status') == 'stopped'
        stage = self.stages.sample(Path(str(self.base)+'.engine.txt'), 0, done or stopped,
                                  '0' if done else '1' if stopped else None, sample['fresh'])
        if done:
            stage.update(current='complete', seen=['setup','prefill','decode'], live=False,
                         timings={'prompt_tokens':record['prompt_tokens'], 'generated_tokens':record['generated_tokens'],
                                  'prefill_tok_s':record['prefill_tok_s'], 'generation_tok_s':record['generation_tok_s'],
                                  'steady_tok_s':record['steady_tok_s']})
        stage['source'] = 'ds4-bench engine output and final benchmark record'
        stage['note'] = 'Benchmark reports final token counts and rates; live tokenizer progress is unavailable.'
        return {**sample, 'kind':'ds4-bench', 'state':'Completed' if done else 'Failed' if stopped else 'Benchmark running · timing pending',
                'done':done or stopped, 'fresh':bool(sample['fresh'] and not done and not stopped), 'stage':stage, 'chunks':None, 'response_rate':None,
                'generated_tokens':record['generated_tokens'] if done else None, 'requested_tokens':record['generated_tokens'],
                'model_path':record.get('model_path'),
                'summary':{'ds4_gen_tps':record.get('generation_tok_s'), 'ds4_steady_tps':record.get('steady_tok_s'),
                           'ds4_prefill_tps':record.get('prefill_tok_s'), 'first_byte_s':record.get('first_response_seconds'),
                           'first_decode_step_ms':record.get('first_decode_step_ms'), 'valid':'Measured; qualification pending'},
                'timeline_note':None if sample['devices'] else 'No SSD timeline was recorded for this benchmark. Timing results are available; drive activity cannot be reconstructed.'}


def task_record(base):
    r=json_file(Path(str(base)+'.task.json'))
    if r.get('schema')!=1 or r.get('kind') not in ('correctness','build','waiting','diagnostic') or r.get('status') not in ('running','completed','failed','interrupted','unavailable'):
        return None
    return r

class TaskStream(ArmStream):
    def sample(self,now):
        sample=super().sample(now);r=task_record(self.base)
        if not r:return sample
        status=r['status'];done=status in ('completed','failed','interrupted')
        labels={'correctness':'Correctness test','build':'Build','waiting':'Waiting','diagnostic':'Diagnostic'}
        phase=r.get('phase') or labels[r['kind']]
        try:heartbeat_fresh=0<=now-Path(str(self.base)+'.task.json').stat().st_mtime<10
        except OSError:heartbeat_fresh=False
        shown=status if status!='running' or heartbeat_fresh else 'status unconfirmed'
        return {**sample,'kind':'task','task':r,'state':labels[r['kind']]+' · '+shown,
          'done':done,'fresh':bool(sample['fresh'] and status=='running' and heartbeat_fresh),'arm':r.get('label'),
          'chunks':None,'response_rate':None,'requested_tokens':None,'generated_tokens':None,'summary':{},
          'stage':{'current':status,'seen':[],'live':status=='running' and heartbeat_fresh,'source':'Task recorder','note':phase},
          'timeline_note':r.get('sampling') or 'No SSD samples were recorded for this task.'}


class HarnessMonitor:
    def __init__(self):
        self.stream = None

    def snapshot(self, root, selected='', now=None):
        now = time.time() if now is None else now
        root = Path(root)
        logs = []
        try:
            for path in root.glob('*/*.log'):
                if path.is_file() and (path.with_suffix('.map').is_file() or benchmark_record(tail_text(path))[0] or task_record(path.with_suffix(''))):
                    # Exclude unrelated logs. Existing GLM harness banners are explicit.
                    with path.open() as f:
                        if f.readline().startswith('ARM '):
                            logs.append((max(path.stat().st_mtime,path.with_suffix('.task.json').stat().st_mtime) if task_record(path.with_suffix('')) else path.stat().st_mtime, path))
        except OSError:
            pass
        logs.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
        choices = [{'id': str(p.relative_to(root)), 'label': f'{p.parent.name} / {p.stem}'} for _, p in logs]
        found = next((p for _, p in logs if str(p.relative_to(root)) == selected), None) if selected else (logs[0][1] if logs else None)
        result = {'runs_path': str(root), 'choices': choices, 'collection': 'Existing harness files · no additional hardware sampler'}
        if found is None:
            return {**result, 'state': 'No matching arm', 'arm': None}
        base = found.with_suffix('')
        stream_type = TaskStream if task_record(base) else BenchmarkStream if benchmark_record(tail_text(found))[0] else ArmStream
        if self.stream is None or self.stream.base != base or type(self.stream) is not stream_type:
            self.stream = stream_type(base)
        sample = self.stream.sample(now)
        report = json_file(found.parent/'report.json')
        report_arms = report.get('arms')
        report_arms = report_arms if isinstance(report_arms, list) else []
        remote = next((r for r in report_arms if isinstance(r, dict) and r.get('tag') == base.name), {})
        node = remote.get('node_delta') or {}
        return {**result, **sample, 'arm': sample.get('arm') or base.name, 'id': str(found.relative_to(root)),
                'log': str(found), 'block': found.parent.name,
                'remote': {'mode': remote.get('mode'), 'gets': node.get('gets'),
                           'bytes_sent': node.get('bytes_sent'), 'cache_hits': node.get('cache_hits'),
                           'cache_misses': node.get('cache_misses'), 'read_bytes': node.get('read_bytes'),
                           'interface_tx_bytes': remote.get('m1_iface_tx_delta'),
                           # The GLM runner's legacy "read" field comes from
                           # iostat's combined read+write MB column.
                           'device_io_mb': remote.get('m1_device_io_mb_delta', remote.get('m1_disk_read_mb_delta')),
                           'physical_disk_read_mb': None,
                           'disk_counter_source': 'iostat cumulative device traffic: reads + writes',
                           'node_stopped': report.get('node_stopped'), 'scope': 'Completed arm totals; not live rates'}}
