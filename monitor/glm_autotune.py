"""Bounded, evidence-driven layout and scheduler tests for the local GLM rig.

One launch, one inference arm at a time. Never edits engine settings or weights.
Screening selects experiments; only the final long runs can qualify a gain.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics

import glm_campaign as campaign

KNOBS = [('workers-36', {'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS': '36'}),
         ('workers-64', {'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS': '64'}),
         ('hub-cap-4', {'DS4_MODEL_REPLICA_INFLIGHT': '0,0,0,4,4'}),
         ('hub-cap-8', {'DS4_MODEL_REPLICA_INFLIGHT': '0,0,0,8,8'})]


def checked_results(plan, state):
    results = state.get('arms', [])
    if (state.get('status') != 'complete' or len(results) != len(plan['arms'])
            or [r.get('tag') for r in results] != [a['tag'] for a in plan['arms']]):
        raise ValueError('Incomplete or reordered campaign cannot select settings.')
    for arm, result in zip(plan['arms'], results):
        rate = result.get('engine_decode_tok_s')
        if (not result.get('passed') or result.get('actual_generated_tokens') != arm['tokens']
                or not isinstance(rate, (int, float)) or isinstance(rate, bool) or not math.isfinite(rate) or rate <= 0):
            raise ValueError('Every screening arm needs successful evidence, engine counts and a finite rate.')
    if len({r.get('output_sha256') for r in results}) != 1 or not results[0].get('output_sha256'):
        raise ValueError('Screening outputs must be identical.')
    return results


def choose_layout(plan, state):
    results = checked_results(plan, state)
    medians = {layout: statistics.median(r['engine_decode_tok_s'] for r in results if r['layout'] == layout)
               for layout in ('A4', 'B5', 'C5')}
    # Preserve the old hub share when the two five-drive layouts tie within 2%.
    chosen = 'B5' if medians['B5'] > medians['C5'] * 1.02 else 'C5'
    return {'layout': chosen, 'medians': medians, 'speedup_vs_A4_pct': (medians[chosen]/medians['A4']-1)*100,
            'promoted': False, 'note': 'Selected for scheduler experiments; final qualification still required.'}


def tuning_plan(base, layout, current, name, overrides):
    result = copy.deepcopy(base)
    original = next(a for a in base['arms'] if a['layout'] == layout)
    result.update(phase='tune', experiment=name, candidate=layout, arms=[])
    for i, (side, pair) in enumerate((('control', 1), ('candidate', 1), ('candidate', 2), ('control', 2)), 1):
        arm = copy.deepcopy(original)
        arm.update(tag=f'tune-{name}-{i:02}-{side}-60', pair=pair, setting_role=side,
                   ds4_environment={**current, **(overrides if side == 'candidate' else {})})
        result['arms'].append(arm)
    return result


def tuning_decision(plan, state):
    results = checked_results(plan, state)
    controls, candidates = {}, {}
    for arm, result in zip(plan['arms'], results):
        (controls if arm['setting_role'] == 'control' else candidates)[arm['pair']] = result['engine_decode_tok_s']
    if set(controls) != {1,2} or set(candidates) != {1,2}:
        raise ValueError('Tuning requires two reversed-order matched pairs.')
    gains = [(candidates[i]/controls[i]-1)*100 for i in (1,2)]
    gain = (statistics.median(candidates.values())/statistics.median(controls.values())-1)*100
    return {'experiment': plan['experiment'], 'paired_gains_pct': gains, 'median_gain_pct': gain,
            'adopt_for_next_test': gain >= 2.0 and min(gains) > 0,
            'promoted': False, 'rule': 'At least 2% median gain and neither matched pair regresses; qualification still required.'}


def final_plan(base, layout, settings):
    result = copy.deepcopy(base)
    control = next(a for a in base['arms'] if a['layout'] == 'A4')
    candidate = next(a for a in base['arms'] if a['layout'] == layout)
    result.update(phase='qualify', candidate=layout, arms=[])
    for tokens in (128,512):
        for pair, order in enumerate((('control','candidate'),('candidate','control'),('control','candidate')),1):
            for side in order:
                arm = copy.deepcopy(control if side == 'control' else candidate)
                arm.update(tokens=tokens, pair=pair, tag=f'qualify-{len(result["arms"])+1:02}-{arm["layout"]}-{tokens}')
                if side == 'candidate': arm['ds4_environment'] = dict(settings)
                result['arms'].append(arm)
    return result


def report_text(state):
    lines = ['# Argodrive GLM optimization campaign', '', f'Status: {state["status"]}.',
             'Target: 5–6 engine decode tokens/s. No public release or champion file is changed.', '']
    if state.get('error'): lines.extend(['Stopped: '+state['error'], ''])
    if state.get('selection'):
        lines += ['## Layout screen', '', '| Layout | Median engine tok/s |', '|---|---:|']
        lines += [f'| {name} | {value:.4f} |' for name,value in state['selection']['medians'].items()]
        lines += ['', 'These 60-token screens select further experiments; they are not a public speed claim.', '']
    if state.get('tuning'):
        lines += ['## Scheduler screens', '', '| Change | Median gain | Pair gains | Retained for qualification |', '|---|---:|---|---|']
        for item in state['tuning']:
            gains=', '.join(f'{x:+.2f}%' for x in item['paired_gains_pct'])
            lines += [f'| {item["experiment"]} | {item["median_gain_pct"]:+.2f}% | {gains} | {item["adopt_for_next_test"]} |']
        lines += ['']
    if state.get('qualification'):
        q=state['qualification']
        lines += ['## Final measurement', '', '| Generated tokens | A4 median | Candidate median | Candidate range | Worst pair gain |', '|---|---:|---:|---|---:|']
        for g in q['groups']:
            lines += [f'| {g["tokens"]} | {g["control_median"]:.4f} | {g["candidate_median"]:.4f} | {g["candidate_range"]} | {g["worst_pair_gain_pct"]:+.2f}% |']
        lines += ['', f'Measurement checks passed: {q["measurement_checks_passed"]}.',
                  'Target reached at both lengths: '+str(state.get('target_5_met',False))+'.', '']
        lines += ['- '+reason for reason in q['reasons']]
    lines += ['', '## Evidence limits', '',
              '- This is the fixed short continuation prompt, not a chat or 512-token prefill benchmark; the engine records the input count.',
              '- Actual engine counts, exact output hashes, per-arm environments and raw samplers accompany the results.',
              '- File stats and matched output do not establish full replica content identity.',
              '- Routed-expert requantization must be disclosed; output equality is not a quantization quality evaluation.',
              '- Realistic held-out prompts, model checksum evidence and public artifact review remain required.',
              '- Target checks require valid final measurements at both generation lengths. No rounding up to 5.', '']
    return '\n'.join(lines)


def run(base, output, deadline, allow_existing_swap=False, pressure_gb=100, arm_timeout=600, reference_paths=None):
    # A local Terminal must be able to inspect processes before any output or load.
    campaign.process_guard()
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
    state={'status':'preparing','started_at':datetime.now(timezone.utc).isoformat(),
           'deadline':deadline.isoformat(),'target_5_met':False,'target_6_met':False,'publication_ready':False,
           'stages':[],'tuning':[],'existing_swap_allowed':allow_existing_swap}
    original_inputs=[Path(base['engine']),Path(base['project'])/'tools/harness/champion.env']
    original_inputs += [Path(base['project'])/p for p in campaign.HARNESS_FILES]
    identities={str(p):campaign.sha256(p) for p in original_inputs}
    state['input_hashes']=identities
    references={}
    state['historical_output_references']={}
    for tokens,path in (reference_paths or {}).items():
        data=Path(path).read_bytes()
        if not data:raise ValueError('Historical output reference is empty.')
        references[tokens]=data
        reference_copy=out/f'reference-{tokens}.txt';reference_copy.write_bytes(data)
        state['historical_output_references'][tokens]={'source':str(Path(path).resolve()),'sha256':campaign.sha256(reference_copy)}
    def save():
        campaign.atomic_json(out/'autotune.json',state)
        (out/'REPORT.md').write_text(report_text(state))
    def stage(name, planned, minimum_seconds):
        if (deadline-datetime.now(timezone.utc)).total_seconds()<minimum_seconds:
            raise RuntimeError('Insufficient time before the deadline for '+name)
        if any(not Path(p).is_file() or campaign.sha256(p)!=sha for p,sha in identities.items()):
            raise RuntimeError('Engine, champion or harness source changed between stages.')
        state.update(status='running',active_stage=name);save()
        # Existing Argodrive collectors discover one folder below the arms root.
        # Sibling stage folders retain that compatibility without another sampler.
        directory=out.parent/(out.name+'--'+name)
        campaign.execute(planned,directory,pressure_gb,arm_timeout,allow_existing_swap,deadline,references)
        result=json.loads((directory/'campaign.json').read_text())
        state['stages'].append({'name':name,'path':str(directory),'status':result['status']});save()
        return result
    save()
    try:
        screen=stage('01-layout-screen',base,300)
        selection=choose_layout(base,screen);state['selection']=selection;save()
        settings=dict(next(a for a in base['arms'] if a['layout']==selection['layout'])['ds4_environment'])
        for i,(name,overrides) in enumerate(KNOBS,2):
            planned=tuning_plan(base,selection['layout'],settings,name,overrides)
            result=stage(f'{i:02}-{name}',planned,240)
            decision=tuning_decision(planned,result);state['tuning'].append(decision)
            if decision['adopt_for_next_test']:settings.update(overrides)
            state['candidate_environment']=settings;save()
        planned=final_plan(base,selection['layout'],settings)
        result=stage('06-qualification',planned,1800)
        q=campaign.qualification(planned,result['arms']);state['qualification']=q
        for target in (5,6):
            state[f'target_{target}_met']=(q['measurement_checks_passed'] and len(q['groups'])==2
                and all(g[f'target_{target}_met'] for g in q['groups']))
        state.update(status='complete',active_stage=None)
        campaign.atomic_json(out/'candidate-settings.json',{'environment':settings,'qualification':q,'publication_ready':False})
    except BaseException as exc:
        state.update(status='stopped',active_stage=None,error=str(exc) or type(exc).__name__)
        raise
    finally:
        state['finished_at']=datetime.now(timezone.utc).isoformat();save()
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','run'))
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--engine',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--deadline',required=True,help='ISO UTC timestamp; required for bounded unattended work')
    parser.add_argument('--allow-existing-swap',action='store_true')
    parser.add_argument('--pressure-gb',type=int,default=100)
    parser.add_argument('--arm-timeout',type=int,default=600)
    parser.add_argument('--reference-128',type=Path,help='Previously qualified output for the exact same prompt/model; final arms must match it')
    parser.add_argument('--reference-512',type=Path,help='Previously qualified output for the exact same prompt/model; final arms must match it')
    args=parser.parse_args()
    deadline=datetime.fromisoformat(args.deadline.replace('Z','+00:00'))
    if deadline.tzinfo is None or deadline<=datetime.now(timezone.utc):raise ValueError('Deadline must be a future timestamp with timezone.')
    base=campaign.plan(args.project,args.engine)
    if args.action=='plan':
        print(json.dumps({'execution':'not started','deadline':deadline.isoformat(),'screen':base,
                         'tuning':[name for name,_ in KNOBS],'qualification':'Three reversed-order pairs at each of 128 and 512',
                         'maximum_arms':34,'champion_changed':False,'publication_ready':False},indent=2))
    else:
        refs={n:p for n,p in ((128,args.reference_128),(512,args.reference_512)) if p}
        run(base,args.out,deadline,args.allow_existing_swap,args.pressure_gb,args.arm_timeout,refs)


if __name__=='__main__':main()
