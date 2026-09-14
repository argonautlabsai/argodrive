"""Bounded, read-only summaries of saved Argodrive tuning campaigns.

This presents the runner's recorded checks; it does not rerun them, open model
files, inspect processes, apply settings or start an engine/sampler.
"""
import json
import math
from pathlib import Path
import re
import time

from glm_evidence import comparisons


def object_file(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Campaign artifact leaves the selected run folder.')
    with path.open('rb') as file:
        raw = file.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Campaign artifact exceeds the monitor limit.')
    value = json.loads(raw, parse_constant=lambda _: None)
    if not isinstance(value, dict):
        raise ValueError('Campaign artifact must contain a JSON object.')
    return value


def recorded_arm(arm):
    n, elapsed = arm.get('actual_generated_tokens'), arm.get('engine_decode_seconds')
    valid = (isinstance(n, int) and not isinstance(n, bool) and n > 0 and n == arm.get('tokens')
             and isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(elapsed) and elapsed > 0)
    rate = n / elapsed if valid else None
    reported = arm.get('engine_decode_tok_s')
    valid = valid and isinstance(reported, (int, float)) and not isinstance(reported, bool) and math.isfinite(reported) and math.isclose(rate, reported, rel_tol=1e-8)
    valid = valid and bool(re.fullmatch(r'[a-f0-9]{64}', arm.get('output_sha256') or ''))
    return {'tag': arm.get('tag'), 'layout': arm.get('layout'), 'pair': arm.get('pair'), 'generated_tokens': n,
            'engine_generation_tok_s': rate, 'derived_decode_evals_per_s': None,
            'checks_passed': bool(valid and arm.get('passed') is True), 'output_sha256': arm.get('output_sha256')}


def read_campaign(directory, root):
    state = object_file(directory/'autotune.json', root)
    last_modified = (directory/'autotune.json').stat().st_mtime
    for key in ('selection', 'historical_output_references', 'qualification'):
        if key in state and not isinstance(state[key], dict): raise ValueError('Invalid '+key+' record.')
    stages = state.get('stages', [])
    if not isinstance(stages, list) or len(stages) > 32:
        raise ValueError('Invalid recorded stages.')
    names = [s.get('name') for s in stages if isinstance(s, dict)]
    active = state.get('active_stage') if isinstance(state.get('active_stage'), str) else None
    if active and active not in names: names.append(active)
    completed, planned, active_arm, rows, stage_rows, issues = 0, 0, None, [], [], []
    binary_hashes = set()
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r'\d{2}-[a-z0-9-]+', name):
            issues.append('A stage name is invalid.'); continue
        folder = root/(directory.name+'--'+name)
        try:
            data = object_file(folder/'campaign.json', root)
            plan = object_file(folder/'plan.json', root)
            runtime = object_file(folder/'runtime.json', root)
        except (OSError, ValueError) as exc:
            issues.append(f'{name}: {exc}'); continue
        binary_hashes.add(runtime.get('engine_sha256'))
        last_modified = max(last_modified, (folder/'campaign.json').stat().st_mtime)
        arms = data.get('arms', [])
        if not isinstance(arms, list) or len(arms) > 128:
            issues.append(name+': invalid arms.'); continue
        completed += len(arms)
        planned += len(plan.get('arms', []))
        stage_rows.append({'name': name, 'status': data.get('status'), 'completed': len(arms), 'planned': len(plan.get('arms', []))})
        if name == active:
            active_arm = data.get('active_arm')
            if isinstance(active_arm, str) and re.fullmatch(r'[A-Za-z0-9_-]+', active_arm):
                for suffix in ('.csv', '.chunks', '.err'):
                    source = folder/(active_arm+suffix)
                    if source.resolve().is_relative_to(root) and source.is_file():
                        last_modified = max(last_modified, source.stat().st_mtime)
        if name == '06-qualification':
            rows = [recorded_arm(arm) for arm in arms]
            expected = [a.get('tag') for a in plan.get('arms', [])]
            if [a['tag'] for a in rows] != expected[:len(rows)]: issues.append('Qualification arm order differs from its saved plan.')
            refs = state.get('historical_output_references', {})
            for row in rows:
                reference = refs.get(str(row['generated_tokens']), {})
                if row['output_sha256'] != reference.get('sha256'):
                    row['checks_passed'] = False
    candidate = state.get('selection', {}).get('layout')
    groups = comparisons(rows, candidate) if isinstance(candidate, str) else []
    identities_ok = len(binary_hashes) == 1 and bool(re.fullmatch(r'[a-f0-9]{64}', next(iter(binary_hashes), '') or ''))
    qualified = (state.get('status') == 'complete' and state.get('qualification', {}).get('measurement_checks_passed') is True
                 and len(groups) == 2 and all(g['three_pairs_complete'] for g in groups) and identities_ok and not issues)
    env = state.get('candidate_environment', {})
    if not isinstance(env, dict): env = {}
    # Only the settings data is exported. A campaign cannot smuggle a command
    # into an executable action through its JSON content.
    settings = {k: v for k, v in env.items() if isinstance(k, str) and re.fullmatch(r'DS4_[A-Z0-9_]+', k) and isinstance(v, str)}
    tuning = []
    for decision in state.get('tuning', [])[:32]:
        if not isinstance(decision, dict): continue
        gain = decision.get('median_gain_pct')
        if not isinstance(gain, (int, float)) or isinstance(gain, bool) or not math.isfinite(gain): gain = None
        tuning.append({'name': decision.get('experiment'), 'gain_pct': gain, 'retained': decision.get('adopt_for_next_test') is True,
                       'reason': decision.get('reason') or decision.get('rule')})
    return {'id': directory.name, 'status': state.get('status'), 'started_at': state.get('started_at'),
            'finished_at': state.get('finished_at'), 'deadline': state.get('deadline'), 'active_stage': active,
            'activity_age_s': max(0,time.time()-last_modified),
            'active_arm': active_arm, 'completed_arms': completed, 'recorded_plan_arms': planned, 'stages': stage_rows,
            'candidate': candidate, 'tuning': tuning, 'groups': groups, 'recorded_qualification_passed': qualified,
            'target_5_met': qualified and all(g['candidate']['median'] >= 5 for g in groups),
            'target_6_met': qualified and all(g['candidate']['median'] >= 6 for g in groups),
            'engine_sha256': sorted(x for x in binary_hashes if isinstance(x, str)),
            'settings': {'environment': settings, 'cache_budget': state.get('candidate_cache_budget', 'auto')},
            'error': state.get('error'), 'issues': issues,
            'publication_ready': False, 'source_note': 'Saved runner records. Raw evidence and model quality require separate review.'}


class CampaignMonitor:
    def __init__(self):
        self.cached_root = None
        self.cached_at = 0
        self.cached = None

    def snapshot(self, root):
        root = Path(root).resolve()
        now = time.monotonic()
        if self.cached is not None and root == self.cached_root and now-self.cached_at < 5:
            return self.cached
        paths, errors, campaigns = [], [], []
        try:
            for index, directory in enumerate(root.iterdir()):
                if index >= 4096:
                    errors.append('Folder scan limited to 4096 entries.'); break
                if len(paths) >= 100: break
                if (directory.name.startswith(('argodrive-autotune-', 'argodrive-refine-')) and '--' not in directory.name
                        and not directory.is_symlink() and (directory/'autotune.json').is_file()):
                    paths.append(directory)
            paths.sort(key=lambda p: (p/'autotune.json').stat().st_mtime, reverse=True)
            for directory in paths[:10]:
                try: campaigns.append(read_campaign(directory, root))
                except (OSError, ValueError, TypeError, KeyError) as exc: errors.append(directory.name+': '+str(exc))
        except OSError as exc:
            errors.append(str(exc))
        self.cached_root, self.cached_at = root, now
        self.cached = {'campaigns': campaigns, 'errors': errors, 'mode': 'saved-campaigns',
                       'source': str(root), 'collected_at': time.time(), 'starts_inference': False}
        return self.cached
