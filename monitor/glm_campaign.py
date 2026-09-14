"""Bounded GLM five-drive campaign using the existing local harness.

Planning is read-only. Execution is explicit, runs one arm at a time, and owns
only its output directory and child process group. No weight or engine edits.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import statistics
import subprocess

from optimizer_storage import BLOCK, split_pieces

MODEL = 'GLM-5.3-UD-Q4_K_XL-RoutedQ4K.gguf'
PROMPT = 'The three main financial statements are'
LAYOUTS = {'A4': [10, 5, 5, 4], 'B5': [10, 5, 5, 4, 4], 'C5': [10, 5, 5, 2, 2]}
ROLES = ['internal', 'Green', 'White', 'Yellow', 'Blue']
HARNESS_FILES = ['tools/harness/glm-arm.sh', 'tools/harness/glm-capture.py',
                 'tools/harness/glm-summarize.py', 'tools/bin/k3-diskscope',
                 'tools/bin/k3-pressure', 'tools/argodrive/monitor/k3-memsample.sh',
                 'tools/drives/k3-drive-map.py']


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def read_exports(path):
    """Read literal assignments only. Never source or execute a settings file."""
    values = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip().startswith('export '):
            continue
        parts = shlex.split(line, comments=True)
        if len(parts) != 2 or not re.fullmatch(r'DS4_[A-Z0-9_]+=.*', parts[1]):
            raise ValueError('Champion settings must contain literal DS4 export assignments.')
        name, value = parts[1].split('=', 1)
        if any(x in value for x in ('$(', '`', '\n', '\r')):
            raise ValueError('Executable substitutions are not settings.')
        values[name] = value
    if not values:
        raise ValueError('No DS4 settings were found in champion.env.')
    return values


def plan(project, engine, phase='screen', candidate='C5', threads=48, hub_cap=0):
    project, engine = Path(project).resolve(), Path(engine).resolve()
    common = read_exports(project / 'tools/harness/champion.env')
    # The baseline must remain the recorded four-drive champion. Candidate
    # dimensions change only candidate arms, including in qualification.
    for name in list(common):
        if name.startswith('DS4_ARGODRIVE_'):
            common.pop(name)
    common.pop('DS4_MODEL_REPLICA_INFLIGHT', None)
    required = {'DS4_MODEL_PRIMARY_WEIGHT': '10', 'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS': '48',
                'DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH': '2', 'DS4_METAL_GLM_STREAM_PRECOMMIT': '2'}
    if any(common.get(k) != v for k, v in required.items()):
        raise ValueError('Champion settings changed; review the campaign before running it.')
    if phase not in ('screen', 'qualify') or candidate not in ('B5', 'C5'):
        raise ValueError('Choose screening or qualification and a five-drive candidate.')
    if threads not in (36, 48, 64) or hub_cap not in (0, 4, 8):
        raise ValueError('Unsupported candidate worker/cap setting.')
    sources = [str(project / 'models' / MODEL)] + [str(Path('/Volumes') / role / 'GLM-5.3' / MODEL) for role in ROLES[1:]]
    for path in sources:
        if any(c in path for c in (',', '*', '\n', '\r')):
            raise ValueError('Replica paths cannot contain commas, stars or line breaks.')
    sequence = [(60, role, None) for role in ('A4', 'B5', 'C5', 'C5', 'B5', 'A4')] if phase == 'screen' else [
        (n, role, pair) for n in (128, 512) for pair, order in enumerate((('A4', candidate), (candidate, 'A4'), ('A4', candidate)), 1) for role in order]
    arms = []
    for index, (tokens, role, pair) in enumerate(sequence, 1):
        weights = LAYOUTS[role]
        env = {**common, 'DS4_MODEL_PRIMARY_WEIGHT': str(weights[0]),
               'DS4_MODEL_REPLICAS': ','.join(f'{path}*{weight}' for path, weight in zip(sources[1:], weights[1:])),
               'DS4_MODEL_REPLICA_PIECES_FD': ','.join(['2'] + ['1'] * (len(weights) - 1))}
        if role != 'A4':
            env['DS4_METAL_STREAMING_EXPERT_PREAD_THREADS'] = str(threads)
            if hub_cap:
                env['DS4_MODEL_REPLICA_INFLIGHT'] = f'0,0,0,{hub_cap},{hub_cap}'
        geometry = split_pieces(27 * BLOCK, weights, [2] + [1] * (len(weights) - 1))
        arms.append({'tag': f'{phase}-{index:02}-{role}-{tokens}', 'layout': role, 'tokens': tokens, 'pair': pair,
                     'component_bytes': 27 * BLOCK, 'actual_blocks': [sum(n for _, n in row) // BLOCK for row in geometry],
                     'piece_bytes': [[n for _, n in row] for row in geometry], 'ds4_environment': env})
    return {'version': 1, 'phase': phase, 'candidate': candidate, 'project': str(project), 'engine': str(engine),
            'model_sources': sources, 'weights_content_identity': 'not verified by planning or file size',
            'prompt': PROMPT, 'context': 4096, 'temperature': 0, 'speculation': False,
            'champion_sha256': sha256(project / 'tools/harness/champion.env'), 'arms': arms,
            'publication_ready': False, 'note': 'Measured results and output/token-count checks are required; this plan predicts no speed gain.'}


def arm_weights(arm):
    """Bind custom experiment geometry to the environment actually launched."""
    weights = arm.get('weights', LAYOUTS.get(arm['layout']))
    if (not isinstance(weights, list) or len(weights) not in (4, 5) or
            any(not isinstance(w, int) or isinstance(w, bool) or w <= 0 for w in weights) or sum(weights) > 64):
        raise ValueError('A four/five-drive plan needs positive integer weights within the engine slot limit.')
    if 'weights' in arm:
        env = arm['ds4_environment']
        actual = [int(env['DS4_MODEL_PRIMARY_WEIGHT'])] + [int(s.rsplit('*', 1)[1]) for s in env['DS4_MODEL_REPLICAS'].split(',')]
        if actual != weights:
            raise ValueError('Planned weights differ from the launched replica environment.')
    return weights


def clean_environment(arm, runtime, engine, pressure_gb, allow_existing_swap=False):
    # Inherited experimental flags and debug source overrides cannot leak into
    # the control. Keep normal OS environment; execute arguments as an argv list.
    arm_weights(arm)
    cache_budget = arm.get('cache_budget', 'auto')
    if cache_budget not in ('auto', '70GB'):
        raise ValueError('Unreviewed cache budget; the memory guard must remain enabled.')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DS4_', 'GLM_'))}
    env.update(arm['ds4_environment'])
    env.update(GLM_DS4=str(engine), GLM_MODEL=runtime['model_sources'][0], GLM_CTX='4096', GLM_CACHE=cache_budget,
               GLM_RAW='1', GLM_TEMP='0', GLM_COLD='1', GLM_MTP='0', GLM_NOSEED='1',
               GLM_READAHEAD='0', GLM_EVICT_LRU='1', GLM_FULL_LAYER_PREFILL='0',
               GLM_PRESSURE_GB=str(pressure_gb), GLM_SCOPE_MS='100', GLM_TRACE='0',
               GLM_LAYER_STATS='1', GLM_ALLOW_WARM='0', GLM_ALLOW_SWAP='1' if allow_existing_swap else '0', GLM_SKIP_MAP='0',
               GLM_ENGINE_SHA256=runtime['engine_sha256'], GLM_ENGINE_REVISION=runtime['engine_revision'])
    return env


def parse_verdict(base, arm, expected_output=None):
    """Separate mechanism consistency from the stricter publication gate."""
    base = Path(base)
    weights = arm_weights(arm)
    failures, publication_gaps = [], []
    try:
        log = base.with_suffix('.log').read_text()
        err = base.with_suffix('.err').read_text(errors='replace')
        capture = json.loads(base.with_suffix('.capture.json').read_text())
        summaries = [json.loads(line) for line in log.splitlines() if line.startswith('{"tag"')]
        summary = summaries[-1] if summaries else {}
        output = base.with_suffix('.txt').read_bytes()
    except (OSError, ValueError) as exc:
        return {'tag': arm['tag'], 'passed': False, 'publication_ready': False, 'failures': [f'Missing or unreadable arm evidence: {exc}']}
    if capture.get('rc') != 0 or summary.get('rc') != 0 or not re.search(r'^ARM .* END .* rc=0$', log, re.M):
        failures.append('Engine/harness did not complete successfully.')
    if not output:
        failures.append('No generated output.')
    if summary.get('tag') != arm['tag'] or summary.get('tokens_req') != arm['tokens']:
        failures.append('Summary identity or requested length does not match the plan.')
    if expected_output is not None and output != expected_output:
        failures.append('Generated text differs from the matched control.')
    if summary.get('valid') != 'ok':
        failures.append('Harness validity is not ok.')
    cache_budget = arm.get('cache_budget', 'auto')
    if cache_budget != 'auto' and not re.search(r'^CONFIG .*\bcache=' + re.escape(cache_budget) + r'\s', log, re.M):
        failures.append('Harness did not confirm the requested cache budget.')
    growth = summary.get('swap_growth_mb')
    if not isinstance(growth, (float, int)) or not math.isfinite(growth) or growth > 1:
        failures.append('Swap-growth evidence is missing or exceeds 1 MB.')
    try:
        samples = []
        for line in base.with_suffix('.sys').read_text().splitlines():
            fields = line.split()
            if len(fields) != 8:
                raise ValueError('Expected eight memory-sampler fields.')
            values = list(map(float, fields))
            if any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError('Invalid memory sample.')
            samples.append(values)
        if len(samples) < 2 or max(v[4] for v in samples) - samples[0][4] > 1:
            raise ValueError('Missing memory interval or swap growth exceeded 1 MB.')
        if min(v[2] for v in samples) <= 0:
            raise ValueError('No available RAM in the recorded interval.')
    except (OSError, ValueError) as exc:
        failures.append('Raw memory evidence failed: ' + str(exc))
    try:
        device_map = dict(re.findall(r'(\w+)=(disk\d+)\b', base.with_suffix('.map').read_text()))
        expected_devices = [device_map[role] for role in ROLES[:len(weights)]]
        if len(set(expected_devices)) != len(expected_devices):
            raise ValueError('Multiple roles resolve to the same sampled device.')
        seen = {device: 0 for device in expected_devices}
        with base.with_suffix('.csv').open() as file:
            for line in file:
                fields = line.strip().split(',')
                if len(fields) >= 3 and fields[1] in seen:
                    stamp, value = float(fields[0]), int(fields[2])
                    if not math.isfinite(stamp) or value < 0:
                        raise ValueError('Invalid device counter sample.')
                    seen[fields[1]] += 1
        if any(count < 2 for count in seen.values()):
            raise ValueError('Missing counter interval for a selected drive.')
    except (OSError, ValueError, KeyError) as exc:
        failures.append('Raw drive evidence failed: ' + str(exc))
    rate = summary.get('ds4_gen_tps')
    if not isinstance(rate, (float, int)) or not math.isfinite(rate) or rate <= 0:
        failures.append('Engine generation rate is missing or invalid.')
        rate = None
    replicas = re.search(r'expert pread striping across (\d+) fds, (\d+) weighted slots \(primary weight (\d+), mode=split-read\)', err)
    expected_fds = len(weights)
    if not replicas or tuple(map(int, replicas.groups())) != (expected_fds, sum(weights), weights[0]):
        failures.append('Engine did not confirm the requested replica count, weights and split-read mode.')
    for source in arm['ds4_environment']['DS4_MODEL_REPLICAS'].split(','):
        path, weight = source.rsplit('*', 1)
        if f'ds4: model replica: {path} weight={weight}\n' not in err:
            failures.append('Engine did not confirm replica source/weight: ' + path)
    nocache = re.search(r'F_NOCACHE set on (\d+) model fd', err)
    if not nocache or int(nocache[1]) != expected_fds:
        failures.append('F_NOCACHE was not confirmed on every model descriptor.')
    pieces = arm['ds4_environment']['DS4_MODEL_REPLICA_PIECES_FD']
    if f'expert pread per-fd sub-pieces: {pieces}' not in err:
        failures.append('Engine did not confirm the requested piece subdivision.')
    caps = arm['ds4_environment'].get('DS4_MODEL_REPLICA_INFLIGHT')
    if caps and any(int(v) for v in caps.split(',')):
        expected = ' '.join(f'fd{i}={v}' for i, v in enumerate(caps.split(',')))
        if 'expert pread per-fd in-flight caps: ' + expected not in err:
            failures.append('Engine did not confirm the candidate in-flight caps.')
    chunks = capture.get('chunks')
    if chunks != arm['tokens']:
        # This alone is not a token count. It detects a departure from the
        # expected non-speculative flush pattern, which needs investigation.
        failures.append('Output chunk count differs from the requested length; inspect completion/token counts.')
    token_records = re.findall(r'^ds4: generation counts: generated=(\d+) requested=(\d+)\b', err, re.M)
    actual = int(token_records[-1][0]) if token_records else None
    reported_rate = rate
    precise = re.findall(r'^ds4: generation counts: generated=\d+ requested=\d+ decode_seconds=([\d.eE+-]+) prompt_tokens=(\d+) prefill_seconds=([\d.eE+-]+)$', err, re.M)
    decode_seconds, input_tokens, prefill_seconds = None, None, None
    if precise:
        decode_seconds, input_tokens, prefill_seconds = float(precise[-1][0]), int(precise[-1][1]), float(precise[-1][2])
        if not all(math.isfinite(v) and v > 0 for v in (decode_seconds, prefill_seconds)) or input_tokens <= 0:
            failures.append('Invalid final engine timing or input-token count.')
        elif actual is not None:
            rate = actual / decode_seconds
            if reported_rate is None or abs(rate - reported_rate) > .011:
                failures.append('Final engine duration disagrees with its reported generation rate.')
    else:
        publication_gaps.append('Precise final engine duration is missing; a rounded rate cannot establish the target threshold.')
    if actual is None:
        publication_gaps.append('Actual generated-token count was not recorded; chunks are not tokens.')
    elif actual != arm['tokens'] or int(token_records[-1][1]) != arm['tokens']:
        failures.append('Engine token count does not match the requested generation length.')
    if expected_output is None:
        publication_gaps.append('This arm established the output reference; matched comparisons are required.')
    return {'tag': arm['tag'], 'layout': arm['layout'], 'tokens': arm['tokens'], 'pair': arm['pair'],
            'passed': not failures, 'publication_ready': not failures and not publication_gaps,
            'failures': failures, 'publication_gaps': publication_gaps, 'engine_decode_tok_s': rate,
            'engine_reported_tok_s': reported_rate, 'engine_decode_seconds': decode_seconds,
            'input_tokens': input_tokens, 'engine_prefill_seconds': prefill_seconds,
            'harness_chunk_rate': summary.get('decode_tok_s'), 'actual_generated_tokens': actual,
            'first_response_s': capture.get('first_byte_s'), 'output_sha256': hashlib.sha256(output).hexdigest(),
            'summary': summary, 'log_sha256': sha256(base.with_suffix('.log'))}


def qualification(plan_data, results):
    reasons = []
    if plan_data['phase'] != 'qualify':
        reasons.append('Screening arms cannot qualify a public headline.')
    if len(results) != len(plan_data['arms']) or [r.get('tag') for r in results] != [r['tag'] for r in plan_data['arms']]:
        reasons.append('The planned campaign is incomplete or its arm order differs.')
    if any(not r.get('passed') for r in results):
        reasons.append('At least one arm failed its checks.')
    if any(r.get('actual_generated_tokens') != r.get('tokens') for r in results):
        reasons.append('Actual engine token counts are incomplete.')
    if any(not isinstance(r.get('engine_decode_seconds'), (int, float)) or isinstance(r.get('engine_decode_seconds'), bool)
           or not math.isfinite(r['engine_decode_seconds']) or r['engine_decode_seconds'] <= 0 for r in results):
        reasons.append('Precise engine decode durations are incomplete.')
    if any(not re.fullmatch(r'[a-f0-9]{64}', r.get('output_sha256') or '') for r in results):
        reasons.append('Output identities are missing.')
    if any(not isinstance(r.get('engine_decode_tok_s'), (float, int)) or isinstance(r.get('engine_decode_tok_s'), bool)
           or not math.isfinite(r['engine_decode_tok_s']) or r['engine_decode_tok_s'] <= 0 for r in results):
        reasons.append('Engine rates are missing or invalid.')
    groups = []
    for tokens in (128, 512):
        controls = [r for r in results if r.get('tokens') == tokens and r.get('layout') == 'A4']
        candidates = [r for r in results if r.get('tokens') == tokens and r.get('layout') == plan_data['candidate']]
        if len(controls) != 3 or len(candidates) != 3:
            reasons.append(f'{tokens}-token qualification needs three matched pairs.')
            continue
        if len({r.get('output_sha256') for r in controls + candidates}) != 1:
            reasons.append(f'{tokens}-token outputs differ.')
        if any(not isinstance(r.get('engine_decode_tok_s'), (float, int)) or not math.isfinite(r['engine_decode_tok_s']) or r['engine_decode_tok_s'] <= 0 for r in controls + candidates):
            continue
        a = [r['engine_decode_tok_s'] for r in controls]
        b = [r['engine_decode_tok_s'] for r in candidates]
        matched = {r['pair']: r for r in controls}
        if set(matched) != {1, 2, 3} or {r.get('pair') for r in candidates} != {1, 2, 3}:
            reasons.append(f'{tokens}-token pair identities are incomplete.')
            continue
        gains = [(r['engine_decode_tok_s'] / matched[r['pair']]['engine_decode_tok_s'] - 1) * 100 for r in candidates]
        groups.append({'tokens': tokens, 'control_median': statistics.median(a), 'candidate_median': statistics.median(b),
                       'control_range': [min(a), max(a)], 'candidate_range': [min(b), max(b)],
                       'paired_gains_pct': gains, 'worst_pair_gain_pct': min(gains),
                       'target_5_met': statistics.median(b) >= 5, 'target_6_met': statistics.median(b) >= 6})
    return {'measurement_checks_passed': not reasons, 'reasons': reasons, 'groups': groups,
            'publication_ready': False, 'publication_note': 'Replica content identity, realistic-prompt coverage, collector policy and public artifact review are separate requirements.'}


def process_guard():
    try:
        result = subprocess.run(['ps', '-A', '-o', 'pid=,comm='], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Cannot verify the process guard. Run from a GPU-capable local Terminal.') from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError('Cannot verify the process guard. Run from a GPU-capable local Terminal.')
    active = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and re.fullmatch(r'(ds4(?:[-.].*)?|deltafin)', Path(fields[1]).name):
            active.append(line.strip())
    if active:
        raise RuntimeError('Another inference process is active: ' + '; '.join(active))


def stop_owned_group(process):
    """Only the new session created for this arm; never process-name kills."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def snapshot(plan_data, out):
    project, original = Path(plan_data['project']), Path(plan_data['engine'])
    runtime = out / 'runtime'
    runtime.mkdir()
    engine_dir = runtime / 'engine'
    engine_dir.mkdir()
    shutil.copy2(original, engine_dir / 'ds4')
    shutil.copytree(original.parent / 'metal', engine_dir / 'metal')
    frozen_project = runtime / 'GLM53'
    hashes = {}
    for relative in HARNESS_FILES:
        source, dest = project / relative, frozen_project / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        before = sha256(source)
        shutil.copy2(source, dest)
        if before != sha256(dest) or before != sha256(source):
            raise RuntimeError('A harness file changed during snapshot: ' + relative)
        hashes[relative] = before
    script = frozen_project / 'tools/harness/glm-arm.sh'
    text = script.read_text()
    old = '$(cd "$(dirname "$GLM_DS4")" && git rev-parse --short HEAD 2>/dev/null)'
    if old not in text:
        raise RuntimeError('Harness ENGINE identity line changed; review before running.')
    text = text.replace(old, 'source-revision=$GLM_ENGINE_REVISION binary-sha256=$GLM_ENGINE_SHA256')
    script.write_text(text)
    # The legacy map script otherwise writes a baseline when one is missing.
    # Only its frozen copy is changed: map validation must be read-only here.
    mapper = frozen_project / 'tools/drives/k3-drive-map.py'
    map_text = mapper.read_text()
    mapping_root = re.search(r'^R = "([^"\n]+)";', map_text, re.M)
    if not mapping_root:
        raise RuntimeError('Drive-map registry location changed; review before running.')
    rig = runtime / 'drive-registry'
    rig.mkdir()
    for name in ('k3-drive-map.json', 'k3-drive-names.json'):
        registry = Path(mapping_root[1]) / name
        if not registry.is_file():
            raise RuntimeError('An accepted drive registry is required: ' + str(registry))
        shutil.copy2(registry, rig / name)
    map_text = map_text.replace(mapping_root[0], f'R = {str(rig)!r};', 1)
    auto_baseline = 'if "--baseline" in sys.argv or old is None:'
    if auto_baseline not in map_text:
        raise RuntimeError('Drive-map baseline behavior changed; review before running.')
    map_text = map_text.replace(auto_baseline, 'if old is None:\n    raise SystemExit("No accepted drive map exists; no baseline was written.")\nif "--baseline" in sys.argv:')
    mapper.write_text(map_text)
    # A frozen copy nested in Argodrive must not claim Argodrive's git HEAD is
    # the engine revision. Record the original checkout revision and content.
    provenance_file = original.parent / 'engine-provenance.json'
    provenance = None
    if provenance_file.is_file():
        provenance = json.loads(provenance_file.read_text())
        revision = provenance.get('source_revision', '')
        if (provenance.get('status') != 'built' or provenance.get('binary_sha256') != sha256(original)
                or not re.fullmatch(r'[a-f0-9]{40,64}', revision)):
            raise RuntimeError('Isolated engine build identity is missing or inconsistent.')
        shutil.copy2(provenance_file, engine_dir / 'engine-provenance.json')
    else:
        revision = subprocess.check_output(['git', '-C', str(original.parent), 'rev-parse', 'HEAD'], text=True).strip()
    identity = {**plan_data, 'engine_sha256': sha256(engine_dir / 'ds4'), 'engine_revision': revision,
                'engine_build_provenance': provenance,
                'runtime_hashes': {str(p.relative_to(runtime)): sha256(p) for p in runtime.rglob('*') if p.is_file()},
                'original_harness_hashes': hashes}
    if identity['engine_sha256'] != sha256(original):
        raise RuntimeError('The engine changed during snapshot.')
    return identity, script, engine_dir / 'ds4'


def runtime_unchanged(runtime, directory):
    return all((directory / relative).is_file() and sha256(directory / relative) == digest for relative, digest in runtime['runtime_hashes'].items())


def models_unchanged(records):
    for expected in records:
        try:
            current = Path(expected['path']).stat()
        except OSError:
            return False
        if (current.st_size, current.st_mtime_ns, current.st_dev, current.st_ino) != tuple(expected[k] for k in ('size', 'mtime_ns', 'device', 'inode')):
            return False
    return True


def execute(plan_data, output, pressure_gb=100, arm_timeout=600, allow_existing_swap=False, deadline=None, output_references=None):
    if not 0 <= pressure_gb <= 100 or not 30 <= arm_timeout <= 1800:
        raise ValueError('Pressure must be 0–100 GiB and arm timeout 30–1800 seconds.')
    # A cooperating-session lock supplements, but does not replace, explicit
    # ownership and the OS process guard. Failure to inspect is never idle.
    lock_path = Path('/tmp/argodrive-glm-campaign.lock')
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another Argodrive GLM campaign owns the lock.') from exc
        process_guard()
        out = Path(output).resolve()
        out.mkdir(parents=True, exist_ok=False)
        atomic_json(out / 'plan.json', plan_data)
        state = {'status': 'preflight', 'started_at': datetime.now(timezone.utc).isoformat(), 'arms': [], 'publication_ready': False,
                 'existing_swap_allowed': allow_existing_swap, 'swap_growth_limit_mb': 1}
        atomic_json(out / 'campaign.json', state)
        process = None
        try:
            # Resolve no raw disks and create no replicas. File equality is a
            # separate qualification requirement, explicitly unresolved here.
            files = [Path(p) for p in plan_data['model_sources']]
            stats = [p.stat() for p in files]
            if any(not p.is_file() for p in files) or len({s.st_size for s in stats}) != 1:
                raise RuntimeError('Every model replica must be a readable regular file of identical size.')
            state['model_file_stats'] = [{'path': str(p), 'size': s.st_size, 'mtime_ns': s.st_mtime_ns, 'device': s.st_dev, 'inode': s.st_ino} for p, s in zip(files, stats)]
            runtime, harness, engine = snapshot(plan_data, out)
            atomic_json(out / 'runtime.json', runtime)
            refs = dict(output_references or {})
            for arm in plan_data['arms']:
                remaining = (deadline - datetime.now(timezone.utc)).total_seconds() if deadline else None
                if remaining is not None and remaining < 1:
                    raise RuntimeError('Campaign deadline reached; no new arm started.')
                process_guard()
                if not runtime_unchanged(runtime, out / 'runtime'):
                    raise RuntimeError('Frozen runtime changed; campaign stopped.')
                if not models_unchanged(state['model_file_stats']):
                    raise RuntimeError('A model file changed; campaign stopped.')
                env = clean_environment(arm, runtime, engine, pressure_gb, allow_existing_swap)
                argv = ['sh', str(harness), str(out), arm['tag'], str(arm['tokens']), plan_data['prompt']]
                atomic_json(out / (arm['tag'] + '.request.json'), {'argv': argv, 'environment': {k:v for k,v in env.items() if k.startswith(('DS4_', 'GLM_'))}, 'binary_sha256': runtime['engine_sha256']})
                state.update(status='running', active_arm=arm['tag'])
                print('Running ' + arm['tag'], flush=True)
                with (out / (arm['tag'] + '.runner.out')).open('w') as log:
                    if deadline and datetime.now(timezone.utc) >= deadline:
                        raise RuntimeError('Campaign deadline reached; no new arm started.')
                    process = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    state['active_pid'] = process.pid
                    atomic_json(out / 'campaign.json', state)
                    try:
                        remaining = (deadline - datetime.now(timezone.utc)).total_seconds() if deadline else arm_timeout
                        rc = process.wait(timeout=max(.1, min(arm_timeout, remaining)))
                    finally:
                        stop_owned_group(process)
                process = None
                result = parse_verdict(out / arm['tag'], arm, refs.get(arm['tokens']))
                if rc != 0:
                    result['failures'].append(f'Harness process exited {rc}.')
                    result['passed'] = False
                if not runtime_unchanged(runtime, out / 'runtime'):
                    result['failures'].append('Frozen runtime changed during the arm.')
                    result['passed'] = False
                if not models_unchanged(state['model_file_stats']):
                    result['failures'].append('A model file changed during the arm.')
                    result['passed'] = False
                state['arms'].append(result)
                atomic_json(out / (arm['tag'] + '.verdict.json'), result)
                atomic_json(out / 'campaign.json', state)
                if not result['passed']:
                    raise RuntimeError('Arm failed: ' + '; '.join(result['failures']))
                # Ordered screen/tune/qualification plans always lead with the
                # matching control. A same-layout tuning control need not be A4.
                if arm['tokens'] not in refs:
                    refs[arm['tokens']] = (out / (arm['tag'] + '.txt')).read_bytes()
            state.update(status='complete', active_arm=None, active_pid=None, qualification=qualification(plan_data, state['arms']))
        except BaseException as exc:
            state.update(status='stopped', active_arm=None, active_pid=None, error=str(exc) or type(exc).__name__)
            raise
        finally:
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            atomic_json(out / 'campaign.json', state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'run'))
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--phase', choices=('screen', 'qualify'), default='screen')
    parser.add_argument('--candidate', choices=('B5', 'C5'), default='C5')
    parser.add_argument('--threads', type=int, choices=(36,48,64), default=48)
    parser.add_argument('--hub-cap', type=int, choices=(0,4,8), default=0)
    parser.add_argument('--out', type=Path, required=True, help='New campaign directory; existing directories are never overwritten')
    parser.add_argument('--pressure-gb', type=int, default=100)
    parser.add_argument('--arm-timeout', type=int, default=600)
    parser.add_argument('--allow-existing-swap', action='store_true', help='Explicitly permit existing swap, retaining the 1 MB growth failure gate; recorded in campaign.json')
    args = parser.parse_args()
    prepared = plan(args.project, args.engine, args.phase, args.candidate, args.threads, args.hub_cap)
    if args.action == 'plan':
        print(json.dumps({**prepared, 'output': str(args.out.resolve()), 'execution': 'not started'}, indent=2))
    else:
        execute(prepared, args.out, args.pressure_gb, args.arm_timeout, args.allow_existing_swap)
