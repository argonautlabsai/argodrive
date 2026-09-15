"""Model capabilities and bounded, read-only readiness checks. Never loads weights."""
import os
from pathlib import Path
import re
import shutil
import stat

DS41_REVISION = 'bd66c402070042bf0a79ad6ece8242de4c93680c'
DS41_Q4_BYTES = 518596067328
DS41_Q4_SHA256 = 'a5e2e2c3ada4b2e98d9f9e4b50f6d9c2a12c2c96f5da165c07e13aff9264984e'
GIB = 2**30
DS41_LOCAL_BENCHMARK = {
    'date': '2026-09-12', 'hardware': 'M5 Max · 128 GiB · internal + Green + White',
    'candidate_sha256': '9dcd2db0873852286695d948e301cefa09ba9561b37581e73c87c0ca5ed610e1',
    'prompt_tokens': 512, 'generated_tokens': 512, 'repetitions': 3,
    'generation_includes_first_step': True,
    'median_tok_s': {'upstream_internal': 9.45, 'fork_internal': 12.91, 'fork_plus_one': 14.04, 'fork_plus_two': 15.47},
    'three_drive_range': [15.46, 15.49], 'public_record': False,
    'scope': 'One raw-completion performance prompt; 30 matched arms across lengths, plus separate held-out output checks. This does not verify your executable or files.'}


def ds41_fork_profile(model_path, replica_paths):
    """Export a reviewable configuration; this is not engine capability detection."""
    env = {
        'DS4_METAL_DISABLE_STREAMING_EXPERT_READAHEAD': '1',
        'DS4_ARGODRIVE_QUEUE_LAYERS': '1',
        'DS4_ARGODRIVE_EARLY_EXPERTS': '1',
        'DS4_ARGODRIVE_PRIMARY_NOCACHE': '1',
        'DS4_ARGODRIVE_RESIDENT_GATE': '1',
        'DS4_ARGODRIVE_ENGRAM_READERS': '8',
        'DS4_ARGODRIVE_PHASES': '1',
        # Prefill staging. These are what the 2026-09-15 prompt-processing result
        # measures, and without them a test reproduces the unfixed layer-major
        # sweep instead. SELECTIVE needs no replicas at all: it reads only the
        # experts the chunk's router selected (187 of 384 at 512 tokens), which
        # is the portable half of the gain.
        'DS4_ARGODRIVE_PREFILL_SPLIT': '1',
        'DS4_ARGODRIVE_PREFILL_SELECTIVE': '1',
        'DS4_ARGODRIVE_PREFILL_AHEAD': '1',
        'DS4_ARGODRIVE_PREFILL_LANES': '8',
        'DS4_ARGODRIVE_PREFILL_PIPE': '256',
    }
    errors = []
    if any(',' in str(p) or '*' in str(p) for p in replica_paths):
        errors.append('Replica paths cannot contain comma or asterisk: the fork uses them as separators.')
    elif replica_paths:
        env['DS4_ARGODRIVE_REPLICAS'] = ','.join(str(p)+'*1' for p in replica_paths)
        env['DS4_ARGODRIVE_PRIMARY_WEIGHT'] = '2'
    return {'id': 'ds41-argodrive-experimental', 'status': 'Experimental · local benchmark available',
            'engine': 'Argodrive ds4 fork', 'upstream_compatible': False,
            'base_commit': DS41_REVISION, 'enabled': False, 'requires_verified_replicas': True,
            'model_path': str(model_path), 'replica_paths': [str(p) for p in replica_paths],
            'environment': env, 'errors': errors, 'cache_policy': 'Automatic expert cache; no reserve expansion',
            'engram_policy': 'Primary SSD only; eight parallel whole-row readers',
            'prefill_policy': ('Staged prefill reading only the experts the chunk routed to. '
                               'Measured 2026-09-15 against pinned upstream bd66c40, identical output hash: '
                               '16.50 to 28.04 tok/s prompt processing on one drive with no replicas, '
                               '16.23 to 43.62 on three.'),
            'expert_policy': ('256 KiB split reads; primary weight 2, each enclosure weight 1'
                              if replica_paths else 'Primary SSD expert reads'),
            'validation_required': ['Engine build capabilities', 'Full model and replica SHA-256',
                                    'Physical SSD identity and shared links', 'Matched benchmark and output checks']}


# Deltafin's published Kimi profile. These are source-reviewed settings from
# the user's fork; they are a candidate until paths, devices and a matched run
# are verified on the current machine.
DELTAFIN_KIMI_REFERENCE = {
    'base_commit': 'd7251b26d95b5ce38e0941dfa3f8e66c43e97586',
    'engine': 'Deltafin native Metal runtime',
    'model': 'Kimi K3',
    'max_sources': 4,
    'recommended_environment': {
        'K3_EXPERT_READ_THREADS': '64', 'K3_EXPERT_PREFETCH_THREADS': '8',
        'K3_EXPERT_PREFETCH_GENERATIONS': '4', 'K3_SPLIT_READ': '2',
        'K3_TIER_BALANCE': '1', 'K3_SPLIT_ETA': '1',
        'K3_SPLIT_ETA_GBPS': '7.1,5.5,6.5,13.5', 'K3_PLAN_BALANCE': '1',
    },
    'drive_ladder': {'one_drive_fraction_of_four': 0.52,
                     'two_drive_fraction_of_four': 0.73,
                     'three_drive_fraction_of_four': 0.90},
    'scope': 'Published Kimi K3 fork result; not a guarantee for another model, build, or topology.',
}


def deltafin_kimi_profile(model_root, replica_paths):
    """Build a reviewable Deltafin Kimi multi-drive candidate.

    This emits a candidate environment only. It never copies weights or
    launches the engine.
    """
    root = str(model_root or '').strip()
    replicas = [str(p).strip() for p in (replica_paths or []) if str(p).strip()]
    errors = []
    paths = [root, *replicas]
    if not root or not root.startswith('/'):
        errors.append('DELTAFIN_ROOT must be an absolute model-root path.')
    if len(replicas) > 3:
        errors.append('Deltafin Kimi profile supports at most three replica directories.')
    if any(not p.startswith('/') for p in replicas):
        errors.append('Replica directories must be absolute paths.')
    if len(set(paths)) != len(paths):
        errors.append('Primary and replica paths must be distinct.')
    env = dict(DELTAFIN_KIMI_REFERENCE['recommended_environment'])
    env['DELTAFIN_ROOT'] = root
    if len(replicas) >= 1: env['K3_EXPERT_DIR_B'] = replicas[0]
    if len(replicas) >= 2: env['K3_EXPERT_HOT_DIR'] = replicas[1]
    if len(replicas) >= 3: env['K3_EXPERT_DIR_C'] = replicas[2]
    return {
        'id': 'kimi3-deltafin-replica-streaming',
        'status': 'Candidate · matched benchmark required',
        'engine': DELTAFIN_KIMI_REFERENCE['engine'],
        'model': DELTAFIN_KIMI_REFERENCE['model'],
        'base_commit': DELTAFIN_KIMI_REFERENCE['base_commit'],
        'enabled': False, 'model_root': root, 'replica_paths': replicas,
        'environment': env, 'errors': errors,
        'method': 'Complete replicas + weighted split-read and per-tier ETA routing',
        'validation_required': ['Exact Deltafin binary and model-root identity',
                                'Full byte-identical expert replicas',
                                'Distinct physical SSDs and shared-uplink calibration',
                                'Interleaved one/two/three-drive run with output identity checks'],
        'scope': DELTAFIN_KIMI_REFERENCE['scope'],
    }


def _gguf_metadata(p):
    fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('The model must be a regular GGUF file.')
        return st.st_size, os.read(fd, 8) == b'GGUF\x03\x00\x00\x00'
    finally:
        os.close(fd)


def model_identity(name, engine=None):
    value = str(name or '').lower()
    if re.search(r'deepseek[-_ ]?v?4[._-]1|deepseek41', value):
        return {'id': 'deepseek41', 'label': 'DeepSeek V4.1 Flash', 'engine': 'ds4'}
    if 'glm' in value:
        return {'id': 'glm', 'label': 'GLM', 'engine': 'ds4'}
    if 'kimi' in value or engine == 'deltafin':
        return {'id': 'kimi3', 'label': 'Kimi K3', 'engine': 'deltafin'}
    if 'deepseek' in value:
        return {'id': 'deepseek', 'label': 'DeepSeek (other version)', 'engine': 'ds4'}
    return {'id': 'unknown', 'label': 'Unspecified model', 'engine': engine or 'unknown'}


def model_catalog():
    return {'models': [
        {'id': 'kimi3', 'label': 'Kimi K3', 'engine': 'Deltafin', 'status': 'Recorded-run support',
         'description': 'Import K3 runs, compare output and inspect expert-streaming evidence.',
         'streaming_method': 'Complete replicas with weighted split-read, tier balance and ETA routing',
         'streaming_reference': DELTAFIN_KIMI_REFERENCE},
        {'id': 'glm', 'label': 'GLM 5.3', 'engine': 'ds4 / Argonaut fork', 'status': 'Recorded-run and profile support',
         'description': 'Review cache, prefetch and replica split reads against matched benchmark arms.'},
        {'id': 'deepseek41', 'label': 'DeepSeek V4.1 Flash', 'engine': 'ds4 / Argodrive fork · Metal', 'status': 'Experimental · 30-arm local check complete',
         'description': 'Separate disk-only Engram lookups from expert streaming. Start with an automatic cache.',
         'reference_commit': DS41_REVISION, 'q4_bytes': DS41_Q4_BYTES,
         'q4_sha256': DS41_Q4_SHA256, 'main_weights_gib': 294.15, 'engram_gib': 188.83,
         'engram_row_bytes': 264, 'replica_streaming_verified': True,
         'local_qualification': True, 'benchmark_evidence': DS41_LOCAL_BENCHMARK,
         'installed_binary_verified': False, 'speculation_supported': False}
    ], 'launches_engine': False}


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('Enter a valid absolute path.')
    p = Path(value)
    if not p.is_absolute():
        raise ValueError('Use an absolute path.')
    return p


def readiness(body):
    if not isinstance(body, dict) or set(body) != {'model_path', 'replica_directories'}:
        raise ValueError('Provide model_path and replica_directories only.')
    p = _path(body['model_path'])
    dirs = body['replica_directories']
    if not isinstance(dirs, list) or len(dirs) > 2:
        raise ValueError('This preparation plan accepts up to two enclosure directories.')
    dirs = [_path(v) for v in dirs]
    if len(set(str(v.resolve()) for v in dirs)) != len(dirs):
        raise ValueError('Use different enclosure directories.')
    checks = []
    size = None
    header_ok = False
    try:
        # Nonblocking open prevents a supplied FIFO/device from hanging this API.
        size, header_ok = _gguf_metadata(p)
    except FileNotFoundError:
        checks.append('Model file is absent or still downloading. Select the final assembled GGUF, not a part file.')
    if size is not None and (size != DS41_Q4_BYTES or not header_ok):
        checks.append('The file does not match the expected Q4 size and GGUF v3 header.')
    checks.append('Full-file SHA-256 and exact engine capability must be verified before inference; size/header alone are not identity.')
    placements = []
    seen_devices = set()
    if p.exists():
        seen_devices.add(p.stat().st_dev)
    for directory in dirs:
        item = {'directory': str(directory), 'required_bytes': DS41_Q4_BYTES, 'headroom_bytes': 20*GIB}
        try:
            if not directory.is_dir():
                raise ValueError('Choose an existing mounted enclosure directory.')
            device = directory.stat().st_dev
            free = shutil.disk_usage(directory).free
            replica = directory / p.name
            existing_match = False
            try:
                replica_size, replica_header = _gguf_metadata(replica)
                existing_match = replica_size == DS41_Q4_BYTES and replica_header
            except FileNotFoundError:
                pass
            copy_bytes = 0 if existing_match else DS41_Q4_BYTES
            item.update(replica_path=str(replica), existing_size_and_header_match=existing_match,
                        checksum_verified=False, copy_bytes_required=copy_bytes,
                        free_bytes=free, additional_bytes_needed=max(0, copy_bytes+20*GIB-free),
                        has_capacity=free >= copy_bytes+20*GIB,
                        distinct_filesystem=device not in seen_devices,
                        physical_ssd_verified=False, shared_uplink_verified=False)
            seen_devices.add(device)
        except (OSError, ValueError) as exc:
            item.update(error=str(exc), has_capacity=False)
        placements.append(item)
    return {'model_id': 'deepseek41', 'model_path': str(p), 'file_bytes': size,
            'expected_bytes': DS41_Q4_BYTES, 'expected_sha256': DS41_Q4_SHA256,
            'size_and_header_match': size == DS41_Q4_BYTES and header_ok,
            'checksum_verified': False, 'ready_to_run': False, 'publication_ready': False,
            'replica_streaming_verified': False, 'placements': placements, 'checks': checks,
            'experimental_profile': ds41_fork_profile(p, [d / p.name for d in dirs]),
            'baseline_argv': ['--metal', '-m', str(p), '--ssd-streaming', '--ctx', '4096', '--think-level', '0', '--power', '100'],
            'notes': ['The Argodrive DeepSeek fork has completed a 30-arm local comparison. Those results qualify only the recorded build, hardware and workloads; this readiness check does not qualify your files or executable. DS4_ARGODRIVE settings are not upstream ds4 or GLM DS4_MODEL settings.',
                      'Engram rows remain disk-only. Main weights, expert cache and Engram tables are separate quantities.',
                      'An existing matching size/header avoids budgeting a second copy, but does not prove identity. Other targets budget a full copy plus 20 GiB reserve. Different filesystems do not prove different SSDs.',
                      'No files were copied or deleted; no engine or sampler was started.']}
