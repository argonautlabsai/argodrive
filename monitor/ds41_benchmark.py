"""One explicit V4.1 baseline; no auto-start, replica routing, or promotion."""
import argparse
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import re
import os
from pathlib import Path
import signal
import plistlib
import subprocess
import time

from model_support import DS41_Q4_BYTES, DS41_Q4_SHA256, DS41_REVISION


@contextmanager
def campaign_lock(already_held=False):
    """Serialize standalone runs, while allowing Monitor's worker lock."""
    if already_held:
        yield
        return
    with Path('/tmp/argodrive-glm-campaign.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(16 << 20), b''):
            h.update(block)
    return h.hexdigest()


def shader_identity(engine):
    root = Path(engine).parent/'metal'
    return {str(p.relative_to(root)):digest(p) for p in sorted(root.rglob('*.metal'))}


def parse_result(path, prompt_tokens, output_tokens):
    with Path(path).open() as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 1:
        raise ValueError('Expected exactly one benchmark frontier.')
    row = rows[0]
    for key, expected in [('ctx_tokens', prompt_tokens), ('prefill_tokens', prompt_tokens), ('gen_tokens', output_tokens), ('gen_steady_tokens', output_tokens-1)]:
        if int(row[key]) != expected:
            raise ValueError('Unexpected engine token count: '+key)
    for key in ('prefill_tps', 'gen_tps', 'gen_steady_tps', 'gen_first_ms'):
        value = float(row[key])
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid engine timing: '+key)
    return {'prompt_tokens': prompt_tokens, 'generated_tokens': output_tokens,
            'prefill_tok_s': float(row['prefill_tps']), 'generation_tok_s': float(row['gen_tps']),
            'steady_tokens': output_tokens-1, 'steady_tok_s': float(row['gen_steady_tps']),
            'first_decode_step_ms': float(row['gen_first_ms']),
            'first_response_seconds': None, 'engram_read_bytes': None, 'expert_read_bytes': None,
            'rate_source': 'ds4-bench CSV: actual engine counts; rounded upstream timers',
            'quality_verified': False, 'publication_ready': False}


def plan(engine, model, prompt, out, prompt_tokens=512, output_tokens=128):
    # 40 tokens is a fast screening rung. It is intentionally excluded from
    # publication/qualification guidance; those still use 60/128/512.
    if prompt_tokens not in (116, 128, 512, 2048) or output_tokens not in (40, 60, 128, 512):
        raise ValueError('Supported preparation rungs: 116/128/512/2048 prompt, 40/60/128/512 generation.')
    paths = [Path(x).expanduser().resolve() for x in (engine, model, prompt, out)]
    engine, model, prompt, out = paths
    if not engine.is_file() or not os.access(engine, os.X_OK) or not prompt.is_file():
        raise ValueError('Build ds4-bench and select an existing benchmark prompt first.')
    if any(any(ord(c) < 32 for c in str(p)) for p in paths):
        raise ValueError('Control characters in paths are unsupported.')
    return {'schema': 1, 'engine': 'ds4-bench', 'model_id': 'deepseek41', 'model_path': str(model),
            'reference_commit': DS41_REVISION, 'engine_sha256': digest(engine),
            'runtime_shader_sha256':shader_identity(engine),
            'prompt_path': str(prompt), 'prompt_sha256': digest(prompt), 'prompt_tokens': prompt_tokens, 'generated_tokens': output_tokens,
            'output_directory': str(out), 'method': 'single-source', 'cache': 'automatic',
            'context_allocation': 4096, 'speculation': False, 'replica_streaming': False,
            'argv': [str(engine), '--metal', '-m', str(model), '--ssd-streaming',
                     '--prompt-file', str(prompt), '--ctx-start', str(prompt_tokens), '--ctx-max', str(prompt_tokens),
                     '--ctx-alloc', '4096', '--gen-tokens', str(output_tokens), '--show-output', '--csv', str(out/'bench.csv')],
            'clears_environment_prefixes': ['DS4_', 'GLM_', 'K3_'],
            'model_ready': model.is_file() and model.stat().st_size == DS41_Q4_BYTES,
            'checksum_verified': False, 'publication_ready': False,
            'notes': ['Run mode verifies the complete model SHA-256 before loading it.',
                      'Benchmark uses a raw fixed text frontier, not chat/tool quality qualification.',
                      'First decode-step latency is not startup or time to first response.',
                      'No per-drive or Engram counters are fabricated from generation speed.',
                      'This baseline does not implement two-enclosure replica streaming.']}


def file_identity(path):
    stat = Path(path).stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def extract_generated(raw, prompt_tokens):
    """Extract one quoted output while accepting the engine's known footer lines.

    A run can emit counters from both the prefill and decode teardown paths, so
    the footer is not necessarily one contiguous block.  We validate every
    line after a candidate closing quote instead of assuming one fixed order.
    """
    marker = ('ds4-bench: gen[ctx='+str(prompt_tokens)+'] decoded text: "').encode()
    start = raw.find(marker)
    if start < 0 or raw.count(marker) != 1:
        raise ValueError('Cannot extract exact generated text from benchmark log.')
    known = re.compile(rb'(?:'
        rb'ds4: Argodrive source\[\d+\] bytes=\d+|'
        rb'ds4: Argodrive Engram source\[\d+\] bytes=\d+|'
        rb'ds4: Argodrive (?:resident_gate|precommit)_layers=\d+|'
        rb'ds4: Argodrive flat_read_batches=\d+|'
        rb'ds4: Argodrive (?:norm_bf16_calls|q8_rows1_calls|whole_requests)=\d+|'
        rb'ds4: Argodrive prefetch issued=\d+ source_bytes=\d+ copied_components=\d+ copied_bytes=\d+ busy=\d+ late=\d+ failed=\d+|'
        rb'ds4: Argodrive router observation records=[1-9]\d* failed=0 write_ok=1|'
        rb'ds4: Metal memory at cleanup:.*|ds4: +counters .*'
    rb')$')
    body = raw[start+len(marker):]
    # A generated answer may itself contain quotes.  Choose the first closing
    # quote whose complete suffix is a recognized diagnostic footer.
    for match in re.finditer(rb'"\n', body):
        output, suffix = body[:match.start()], body[match.end():]
        lines = suffix.splitlines()
        if all(not line or known.fullmatch(line) for line in lines):
            return output
    # Some ds4 builds append an expanded multi-line diagnostics block whose
    # fields are intentionally open-ended.  The first stable footer is the
    # cleanup line; use the final quote before it as the record delimiter.
    # This keeps embedded quotes in the answer intact while avoiding a list of
    # every diagnostic field emitted by each engine revision.
    cleanup = body.find(b'ds4: Metal memory at cleanup:')
    if cleanup >= 0:
        end = body.rfind(b'"\n', 0, cleanup)
        diagnostic_tail = body[end + 2:] if end >= 0 else b''
        # A footer is only accepted when every following non-empty line is
        # an engine diagnostic.  This preserves the rejection of truncated or
        # unknown text while allowing new ds4 diagnostic fields.
        footer_lines_are_diagnostics = all(
            (not line) or line.startswith(b'ds4:')
            for line in diagnostic_tail.splitlines()
        )
        if end >= 0 and footer_lines_are_diagnostics:
            return body[:end]
    raise ValueError('Cannot extract exact generated text from benchmark log.')


def check_verification_receipt(receipt_path, model, replicas):
    receipt = json.loads(Path(receipt_path).read_text())
    entries = [receipt['source'], *receipt['replicas']]
    verified = []
    for path in [model, *replicas]:
        path = Path(path).resolve()
        entry = next((x for x in entries if Path(x['path']).resolve() == path), None)
        if not entry or entry.get('sha256') != DS41_Q4_SHA256:
            raise ValueError('No full-checksum receipt for '+str(path))
        if not path.is_file() or path.stat().st_size != DS41_Q4_BYTES or file_identity(path) != entry.get('identity'):
            raise ValueError('Model file changed since its full-checksum verification: '+str(path))
        if path != Path(model).resolve():
            mount = path.parent
            while mount.parent != mount and not os.path.ismount(mount):
                mount = mount.parent
            volume = plistlib.loads(subprocess.check_output(['/usr/sbin/diskutil','info','-plist',str(mount)]))
            if not entry.get('volume_uuid') or volume.get('VolumeUUID') != entry['volume_uuid']:
                raise ValueError('Replica volume identity changed')
        verified.append(entry)
    return verified


def process_guard():
    result = subprocess.run(['ps', '-A', '-o', 'pid=,comm='], text=True, capture_output=True, timeout=10, check=True)
    if not result.stdout.strip():
        raise RuntimeError('Process inventory unavailable.')
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and Path(fields[1]).name in ('ds4', 'ds4-bench', 'ds4-server', 'deltafin'):
            raise RuntimeError('Another inference engine is present; no benchmark started.')


def swap_used_mb():
    raw = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'vm.swapusage'], text=True, timeout=2)
    found = re.search(r'used\s*=\s*([0-9.]+)([KMG])', raw)
    if not found:
        raise RuntimeError('Swap guard cannot read current swap usage.')
    return float(found[1]) * {'K':1/1024, 'M':1, 'G':1024}[found[2]]


def run(p, timeout=1800, replicas=(), verification_receipt=None, primary_weight=2, sampler_binary=None, devices=None, experimental_env=None, max_swap_growth_mb=None, lock_held=False):
    if not 30 <= timeout <= 1800:
        raise ValueError('Timeout must be 30–1800 seconds.')
    if max_swap_growth_mb is not None and not 0 < max_swap_growth_mb <= 1024:
        raise ValueError('Swap-growth guard must be between 0 and 1024 MiB.')
    replicas = [str(Path(x).resolve()) for x in replicas]
    if len(replicas)>2 or len(set([p['model_path'], *replicas])) != len(replicas)+1:
        raise ValueError('Choose up to two different replica paths.')
    if any(any(c in x for c in ',*\n\r') for x in replicas) or not 1 <= primary_weight <= 100:
        raise ValueError('Invalid replica path grammar or primary weight.')
    if replicas and not verification_receipt:
        raise ValueError('Replica runs require completed full-file verification receipts.')
    devices = devices or {}
    experimental_env = dict(experimental_env or {})
    allowed = {'DS4_ARGODRIVE_NORM_BF16', 'DS4_ARGODRIVE_TIMELINE', 'DS4_ARGODRIVE_PREFETCH', 'DS4_ARGODRIVE_READ_MODE', 'DS4_ARGODRIVE_ROUTER_TRACE', 'DS4_ARGODRIVE_ENGRAM_READERS', 'DS4_ARGODRIVE_ENGRAM_REPLICAS', 'DS4_ARGODRIVE_DECODE_CACHE_PCT',
               'DS4_ARGODRIVE_QUEUE_LAYERS', 'DS4_ARGODRIVE_EARLY_EXPERTS',
               'DS4_ARGODRIVE_PHASES', 'DS4_ARGODRIVE_PREFILL_REPLICA_SEED', 'DS4_METAL_ENCODER_TIMELINE',
               'DS4_SSD_AUTO_CACHE_PCT', 'DS4_METAL_Q8_MV_NSG',
               'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS', 'DS4_ARGODRIVE_PRIMARY_NOCACHE', 'DS4_ARGODRIVE_RESIDENT_GATE', 'DS4_ARGODRIVE_PRECOMMIT', 'DS4_ARGODRIVE_FLAT_READS', 'DS4_ARGODRIVE_Q8_BF16', 'DS4_ARGODRIVE_Q8_ROWS', 'DS4_ARGODRIVE_EARLY_EVENT',
               'DS4_METAL_CB_TIMES', 'DS4_METAL_GPU_BUSY_PROFILE',
               'DS4_METAL_DISABLE_STREAMING_EXPERT_READAHEAD',
               'DS4_METAL_STREAMING_EXPERT_TIMING_SUMMARY'}
    if any(k not in allowed or not isinstance(v, str) or '\0' in v
           for k, v in experimental_env.items()):
        raise ValueError('Unsupported experimental environment setting.')
    if sampler_binary and (not Path(sampler_binary).is_file() or not devices or any(not re.fullmatch(r'disk[0-9]+', d) for d in devices.values())):
        raise ValueError('Sampler requires a verified binary and physical whole-disk map.')
    p = {**p, 'sampler':str(sampler_binary) if sampler_binary else None, 'physical_devices':devices, 'method':'expert split reads' if replicas else 'single-source',
         'replica_streaming':bool(replicas), 'replicas':replicas, 'primary_weight':primary_weight,
         'verification_receipt':str(verification_receipt) if verification_receipt else None,
         'experimental_environment':experimental_env}
    p['max_swap_growth_mb'] = max_swap_growth_mb
    if replicas:
        p['notes'] = ['Experimental expert split reads and opt-in Engram row striping across verified replicas.', 'All replica files require full-checksum receipts. Speed and output qualification remain separate.']
    out = Path(p['output_directory'])
    if out.exists():
        raise ValueError('Use a new output directory; previous evidence will not be overwritten.')
    model = Path(p['model_path'])
    if not p['model_ready'] or not model.is_file() or model.stat().st_size != DS41_Q4_BYTES:
        raise ValueError('The complete, assembled Q4 model is not ready.')
    with campaign_lock(lock_held):
        process_guard()
        if verification_receipt:
            check_verification_receipt(verification_receipt, model, replicas)
            verification_method = 'Full SHA-256 copy receipt + unchanged dev/inode/size/mtime/ctime and replica volume UUID'
        else:
            print('Verifying full Q4 SHA-256 before inference…', flush=True)
            before = file_identity(model)
            if digest(model) != DS41_Q4_SHA256:
                raise ValueError('Model SHA-256 mismatch; no inference started.')
            if file_identity(model) != before:
                raise ValueError('Model changed during verification.')
            verification_method = 'Full SHA-256 before this launch'
        if digest(p['argv'][0]) != p['engine_sha256']:
            raise ValueError('Engine changed after preparation.')
        if shader_identity(p['argv'][0]) != p.get('runtime_shader_sha256', {}):
            raise ValueError('Runtime Metal sources changed after preparation.')
        if digest(p['prompt_path']) != p['prompt_sha256']:
            raise ValueError('Prompt changed after preparation.')
        if verification_receipt:
            check_verification_receipt(verification_receipt, model, replicas)
        process_guard()
        out.mkdir(parents=True)
        (out/'plan.json').write_text(json.dumps(p, indent=2)+'\n')
        env = {k:v for k,v in os.environ.items() if not k.startswith(('DS4_', 'GLM_', 'K3_'))}
        env.update(experimental_env)
        if replicas:
            env['DS4_ARGODRIVE_REPLICAS'] = ','.join(x+'*1' for x in replicas)
            env['DS4_ARGODRIVE_PRIMARY_WEIGHT'] = str(primary_weight)
        state = {'verification_method':verification_method, 'status':'running', 'started_at':datetime.now(timezone.utc).isoformat(),
                 'model_sha256': DS41_Q4_SHA256, 'publication_ready':False}
        process = None
        sampler_process = None
        sampler_log = None
        swap_samples = []
        (out/'baseline.log').write_text('ARM baseline START '+state['started_at']+' overrides: tokens='+str(p['generated_tokens'])+'\n'+'ARGODRIVE_BENCH_START '+json.dumps({'model_path':str(model),'prompt_tokens':p['prompt_tokens'],'generated_tokens':p['generated_tokens']})+'\n')
        try:
            if max_swap_growth_mb is not None:
                swap_samples.append({'time':time.time(), 'used_mb':swap_used_mb()})
            if sampler_binary:
                (out/'baseline.map').write_text('[ds41-bench] device map: '+' '.join(k+'='+v for k,v in devices.items())+'\n')
                sampler_log = (out/'sampler.out').open('x')
                sampler_process = subprocess.Popen([str(sampler_binary),'100',str(timeout+5),str(out/'baseline.csv'),*devices.values()],stdout=sampler_log,stderr=subprocess.STDOUT,start_new_session=True)
            with (out/'baseline.engine.txt').open('w') as log:
                process = subprocess.Popen(p['argv'], cwd=Path(p['argv'][0]).parent, env=env,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                state['active_pid'] = process.pid
                (out/'run.json').write_text(json.dumps(state, indent=2)+'\n')
                if max_swap_growth_mb is None:
                    rc = process.wait(timeout=timeout)
                else:
                    end = time.monotonic() + timeout
                    while True:
                        left = end - time.monotonic()
                        if left <= 0:
                            raise subprocess.TimeoutExpired(p['argv'], timeout)
                        try:
                            rc = process.wait(timeout=min(2, left))
                        except subprocess.TimeoutExpired:
                            rc = None
                        current = swap_used_mb()
                        swap_samples.append({'time':time.time(), 'used_mb':current})
                        if current - swap_samples[0]['used_mb'] > max_swap_growth_mb:
                            raise RuntimeError('Swap growth exceeded the experimental memory guard.')
                        if rc is not None:
                            break
            if rc:
                raise RuntimeError('ds4-bench exited '+str(rc))
            result = parse_result(out/'bench.csv', p['prompt_tokens'], p['generated_tokens'])
            if shader_identity(p['argv'][0]) != p.get('runtime_shader_sha256', {}):
                raise ValueError('Runtime Metal sources changed during the arm.')
            if verification_receipt:
                check_verification_receipt(verification_receipt, model, replicas)
            raw = (out/'baseline.engine.txt').read_bytes()
            marker = ('ds4-bench: gen[ctx='+str(p['prompt_tokens'])+'] decoded text: "').encode()
            traffic = re.findall(rb'^ds4: Argodrive source\[(\d+)\] bytes=(\d+)$', raw, re.M)
            if replicas:
                expected_marker = ('experimental Argodrive expert reader sources='+str(len(replicas)+1)).encode()
                if expected_marker not in raw or len(traffic)!=len(replicas)+1 or {int(i) for i,_ in traffic} != set(range(len(replicas)+1)) or any(int(n)<=0 for _,n in traffic):
                    raise ValueError('Requested replica reader did not report traffic on every source.')
                result['expert_application_bytes_by_source'] = {str(int(i)):int(n) for i,n in traffic}
            if pf := re.search(rb'^ds4: Argodrive prefetch issued=(\d+) source_bytes=(\d+) copied_components=(\d+) copied_bytes=(\d+) busy=(\d+) late=(\d+) failed=(\d+)$', raw, re.M):
                result['prefetch_staging'] = dict(zip(('issued_experts','source_bytes','copied_components','copied_bytes','busy_skips','not_ready_components','failed_components'), map(int,pf.groups())))
            output = extract_generated(raw, p['prompt_tokens'])
            (out/'generated.txt').write_bytes(output)
            result['output_sha256'] = hashlib.sha256(output).hexdigest()
            state.update(status='complete', result=result)
            # Dedicated format: engine tokens must never masquerade as response chunks.
            record = {**result, 'started_at':state['started_at'], 'model_path': str(model), 'context': p['context_allocation'],
                      'engine_sha256': p['engine_sha256'], 'prompt_sha256': p['prompt_sha256'],
                      'replicas':replicas, 'method':p['method']}
            (out/'baseline.log').write_text(
                'ARM baseline START '+state['started_at']+' overrides: tokens='+str(p['generated_tokens'])+'\n'
                +'ARGODRIVE_BENCH_RESULT '+json.dumps(record)+'\n')
        except BaseException as exc:
            state.update(status='stopped', error=str(exc) or type(exc).__name__)
            raise
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
            if sampler_process is not None and sampler_process.poll() is None:
                sampler_process.terminate()
                try: sampler_process.wait(timeout=5)
                except subprocess.TimeoutExpired: sampler_process.kill(); sampler_process.wait()
            if sampler_log is not None: sampler_log.close()
            if swap_samples:
                (out/'swap-samples.json').write_text(json.dumps(swap_samples, indent=2)+'\n')
            state.update(active_pid=None, finished_at=datetime.now(timezone.utc).isoformat())
            (out/'run.json').write_text(json.dumps(state, indent=2)+'\n')
    return state


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument('action', choices=('plan', 'run'))
    for name in ('engine', 'model', 'prompt', 'out'):
        a.add_argument('--'+name, required=True, type=Path)
    a.add_argument('--prompt-tokens', type=int, default=512)
    a.add_argument('--tokens', type=int, default=128)
    a.add_argument('--timeout', type=int, default=1800)
    a.add_argument('--replica', action='append', default=[], type=Path)
    a.add_argument('--verification-receipt', type=Path)
    a.add_argument('--primary-weight', type=int, default=2)
    a.add_argument('--sampler', type=Path,
                   help='Optional k3-diskscope binary; records physical disk counters beside the arm.')
    a.add_argument('--device', action='append', default=[], metavar='NAME=DISK',
                   help='Physical whole-disk mapping for --sampler (repeat, e.g. internal=disk0).')
    args = a.parse_args()
    p = plan(args.engine, args.model, args.prompt, args.out, args.prompt_tokens, args.tokens)
    devices = {}
    for item in args.device:
        if '=' not in item:
            a.error('--device must use NAME=DISK (for example internal=disk0)')
        name, disk = item.split('=', 1)
        if not name or not re.fullmatch(r'disk[0-9]+', disk):
            a.error('--device must use NAME=diskN')
        if name in devices:
            a.error('duplicate --device name: '+name)
        devices[name] = disk
    if args.action == 'plan':
        if args.sampler or devices:
            p['sampler'] = str(args.sampler) if args.sampler else None
            p['physical_devices'] = devices
        print(json.dumps(p, indent=2))
    else:
        print(json.dumps(run(p, args.timeout, args.replica, args.verification_receipt,
                             args.primary_weight, args.sampler, devices), indent=2))


if __name__ == '__main__': main()
