"""Second bounded GLM campaign: balance the hub, then try existing compute knobs.

This controller requires the first campaign to have finished and uses its exact
engine, harness and output references. It does not edit a running controller.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

import glm_campaign as campaign

WEIGHTS = {'C5': [10, 5, 5, 2, 2], 'D5': [9, 6, 6, 3, 3], 'E5': [9, 7, 7, 2, 2]}
EXPERIMENTS = [('balance-D5', 'D5', {}, None, 60),
               ('balance-E5', 'E5', {}, None, 60),
               ('cache-70GB', None, {}, '70GB', 512),
               ('q8-nsg-2', None, {'DS4_METAL_Q8_MV_NSG': '2'}, None, 128),
               ('q8-nsg-8', None, {'DS4_METAL_Q8_MV_NSG': '8'}, None, 128)]


def replace_layout(arm, layout, sources):
    result = copy.deepcopy(arm)
    weights = WEIGHTS[layout]
    result.update(layout=layout, weights=list(weights))
    result['ds4_environment'].update(DS4_MODEL_PRIMARY_WEIGHT=str(weights[0]),
        DS4_MODEL_REPLICAS=','.join(f'{path}*{w}' for path, w in zip(sources[1:], weights[1:])),
        DS4_MODEL_REPLICA_PIECES_FD='2,1,1,1,1')
    geometry = campaign.split_pieces(27 * campaign.BLOCK, weights, [2, 1, 1, 1, 1])
    result.update(actual_blocks=[sum(n for _, n in row)//campaign.BLOCK for row in geometry],
                  piece_bytes=[[n for _, n in row] for row in geometry])
    return result


def paired_plan(base, current, experiment):
    name, layout, overrides, cache, tokens = experiment
    candidate = replace_layout(current, layout, base['model_sources']) if layout else copy.deepcopy(current)
    candidate['ds4_environment'].update(overrides)
    if cache: candidate['cache_budget'] = cache
    plan = copy.deepcopy(base)
    plan.update(phase='refine', experiment=name, candidate=candidate['layout'], arms=[])
    for i, (side, pair) in enumerate((('control', 1), ('candidate', 1), ('candidate', 2), ('control', 2)), 1):
        arm = copy.deepcopy(current if side == 'control' else candidate)
        arm.update(tokens=tokens, pair=pair, setting_role=side, tag=f'refine-{name}-{i:02}-{side}-{tokens}')
        plan['arms'].append(arm)
    return plan, candidate


def decide(plan, result):
    arms = result.get('arms', [])
    if (result.get('status') != 'complete' or len(arms) != 4 or
            [a.get('tag') for a in arms] != [a['tag'] for a in plan['arms']]):
        return {'adopt': False, 'reason': 'Incomplete comparison.', 'paired_gains_pct': []}
    if any(not a.get('passed') or a.get('actual_generated_tokens') != p['tokens'] or
           not isinstance(a.get('engine_decode_seconds'), (int, float)) or isinstance(a['engine_decode_seconds'], bool)
           or not math.isfinite(a['engine_decode_seconds']) or a['engine_decode_seconds'] <= 0
           for a, p in zip(arms, plan['arms'])):
        return {'adopt': False, 'reason': 'Failed measurement checks.', 'paired_gains_pct': []}
    if len({a.get('output_sha256') for a in arms}) != 1 or not arms[0].get('output_sha256'):
        return {'adopt': False, 'reason': 'Outputs differ.', 'paired_gains_pct': []}
    rates = [a['actual_generated_tokens']/a['engine_decode_seconds'] for a in arms]
    gains = [(rates[1]/rates[0]-1)*100, (rates[2]/rates[3]-1)*100]
    median = (statistics.median([rates[1], rates[2]])/statistics.median([rates[0], rates[3]])-1)*100
    return {'adopt': median >= 2 and min(gains) > 0, 'median_gain_pct': median, 'paired_gains_pct': gains,
            'reason': 'Require at least 2% median gain and both matched pairs positive; final qualification remains required.'}


def qualification_plan(base, current):
    result = copy.deepcopy(base)
    original = next(a for a in base['arms'] if a['layout'] == 'A4')
    result.update(phase='qualify', candidate=current['layout'], arms=[])
    for tokens in (128, 512):
        for pair, order in enumerate((('control', 'candidate'), ('candidate', 'control'), ('control', 'candidate')), 1):
            for side in order:
                arm = copy.deepcopy(original if side == 'control' else current)
                arm.update(tokens=tokens, pair=pair, tag=f'qualify-{len(result["arms"])+1:02}-{arm["layout"]}-{tokens}')
                result['arms'].append(arm)
    return result


def prepare(previous):
    previous = Path(previous).resolve()
    state = json.loads((previous/'autotune.json').read_text())
    if state.get('status') != 'complete' or not state.get('qualification', {}).get('measurement_checks_passed'):
        raise ValueError('The first campaign must finish and pass its measurement checks before refinement.')
    plan = json.loads((previous.parent/(previous.name+'--01-layout-screen')/'plan.json').read_text())
    # The first run froze one engine and all source helpers. Refuse silent drift.
    for path, expected in state['input_hashes'].items():
        if campaign.sha256(path) != expected:
            raise ValueError('A first-campaign input changed: ' + path)
    current = copy.deepcopy(next(a for a in plan['arms'] if a['layout'] == state['selection']['layout']))
    if current['layout'] not in WEIGHTS:
        raise ValueError('The first campaign selected a different layout; review refinement before running it.')
    current['ds4_environment'] = dict(state['candidate_environment'])
    current['weights'] = list(WEIGHTS[current['layout']])
    current['cache_budget'] = 'auto'
    return state, plan, current


def run(previous, out, deadline, allow_existing_swap=False):
    state0, base, current = prepare(previous)
    campaign.process_guard()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    state = {'status': 'preparing', 'started_at': datetime.now(timezone.utc).isoformat(),
             'deadline': deadline.isoformat(), 'previous_campaign': str(Path(previous).resolve()),
             'input_hashes': state0['input_hashes'], 'historical_output_references': state0['historical_output_references'],
             'candidate_environment': current['ds4_environment'], 'candidate_cache_budget': 'auto',
             'selection': {'layout': current['layout']}, 'stages': [], 'tuning': [],
             'publication_ready': False, 'target_5_met': False, 'target_6_met': False}
    state['controller_hashes'] = {str(path): campaign.sha256(path) for path in
        (Path(__file__).resolve(), Path(campaign.__file__).resolve(), Path(campaign.__file__).resolve().with_name('optimizer_storage.py'))}
    references = {n: (Path(previous)/f'reference-{n}.txt').read_bytes() for n in (128, 512)}
    for n, data in references.items():
        expected = state0['historical_output_references'].get(str(n), {}).get('sha256')
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Historical output reference changed at length '+str(n))
    # Also bind new 60-token screens to the successful first campaign's text.
    first_stage = Path(previous).parent/(Path(previous).name+'--01-layout-screen')
    references[60] = (first_stage/'screen-01-A4-60.txt').read_bytes()
    for n, data in references.items():
        if not data: raise ValueError('Empty output reference.')
        (out/f'reference-{n}.txt').write_bytes(data)
    def save(): campaign.atomic_json(out/'autotune.json', state)
    def stage(name, plan, minimum_seconds):
        if (deadline-datetime.now(timezone.utc)).total_seconds() < minimum_seconds:
            raise RuntimeError('Insufficient time for ' + name + ' before the deadline.')
        for path, expected in state['input_hashes'].items():
            if campaign.sha256(path) != expected: raise RuntimeError('Input changed: '+path)
        for path, expected in state['controller_hashes'].items():
            if campaign.sha256(path) != expected: raise RuntimeError('Controller source changed: '+path)
        state.update(status='running', active_stage=name); save()
        directory = out.parent/(out.name+'--'+name)
        try:
            campaign.execute(plan, directory, 100, 600, allow_existing_swap, deadline, references)
        except RuntimeError:
            # A successful inference with a different text rejects this knob.
            # Any other failure stops the campaign, retaining the evidence.
            result = json.loads((directory/'campaign.json').read_text()) if (directory/'campaign.json').is_file() else {}
            failed = [a for a in result.get('arms', []) if not a.get('passed')]
            if (not failed or any(a.get('failures') != ['Generated text differs from the matched control.'] for a in failed)):
                raise
        result = json.loads((directory/'campaign.json').read_text())
        state['stages'].append({'name': name, 'path': str(directory), 'status': result['status']}); save()
        return result
    save()
    try:
        for i, experiment in enumerate(EXPERIMENTS, 1):
            planned, candidate = paired_plan(base, current, experiment)
            result = stage(f'{i:02}-{experiment[0].lower()}', planned, 900 if experiment[-1] == 512 else 360)
            decision = decide(planned, result)
            state['tuning'].append({'experiment': experiment[0], 'adopt_for_next_test': decision['adopt'], **decision})
            if decision['adopt']: current = candidate
            state.update(candidate_environment=current['ds4_environment'], candidate_cache_budget=current.get('cache_budget', 'auto'),
                         selection={'layout': current['layout']}); save()
        planned = qualification_plan(base, current)
        result = stage('06-qualification', planned, 1800)
        q = campaign.qualification(planned, result['arms'])
        state['qualification'] = q
        for target in (5, 6):
            state[f'target_{target}_met'] = q['measurement_checks_passed'] and len(q['groups']) == 2 and all(g[f'target_{target}_met'] for g in q['groups'])
        state.update(status='complete', active_stage=None)
        campaign.atomic_json(out/'candidate-settings.json', {'arm': current, 'qualification': q, 'publication_ready': False})
    except BaseException as exc:
        state.update(status='stopped', active_stage=None, error=str(exc) or type(exc).__name__)
        raise
    finally:
        state['finished_at'] = datetime.now(timezone.utc).isoformat(); save()
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'run'))
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--deadline', required=True)
    parser.add_argument('--allow-existing-swap', action='store_true')
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.deadline.replace('Z', '+00:00'))
    if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc): raise ValueError('A future deadline with timezone is required.')
    if args.action == 'plan':
        state, base, current = prepare(args.previous)
        print(json.dumps({'execution': 'not started', 'maximum_arms': 32, 'deadline': deadline.isoformat(),
                          'starting_layout': current['layout'], 'starting_cache': current['cache_budget'],
                          'experiments': [paired_plan(base, current, x)[0] for x in EXPERIMENTS],
                          'qualification': 'Three matched pairs at 128 and 512; four-drive control unchanged.',
                          'publication_ready': False}, indent=2))
    else:
        run(args.previous, args.out, deadline, args.allow_existing_swap)


if __name__ == '__main__': main()
