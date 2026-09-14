"""Read an Argodrive GLM campaign without launching an engine or sampler.

Rates are recalculated from final engine counts/timers and checked against raw
output and log hashes. Reports remain provisional while qualification is open.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

COUNT = re.compile(r'^ds4: generation counts: generated=(\d+) requested=(\d+) decode_seconds=([\d.eE+-]+) prompt_tokens=(\d+) prefill_seconds=([\d.eE+-]+)$', re.M)
FD = re.compile(r'fd(\d+)\[pieces=(\d+) read_ms=([\d.]+) wait_ms=([\d.]+) lands_last=(\d+)%\]')
ROLES = ('internal', 'Green', 'White', 'Yellow', 'Blue')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def arm_evidence(directory, result, first_token_from_prefill=False):
    tag = result['tag']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', tag):
        raise ValueError('Invalid arm tag')
    base = Path(directory) / tag
    err = base.with_suffix('.err').read_text()
    output = base.with_suffix('.txt').read_bytes()
    log = base.with_suffix('.log').read_bytes()
    capture = json.loads(base.with_suffix('.capture.json').read_text())
    failures = list(result.get('failures', []))
    if not result.get('passed'):
        failures.append('Campaign verdict did not pass.')
    if not output or digest(output) != result.get('output_sha256'):
        failures.append('Raw output hash differs from the recorded verdict.')
    if digest(log) != result.get('log_sha256'):
        failures.append('Raw log hash differs from the recorded verdict.')
    records = COUNT.findall(err)
    n = requested = prompt_tokens = None
    seconds = prefill = rate = steady = None
    if len(records) != 1:
        failures.append('Expected one final engine count/timer record.')
    else:
        n, requested, seconds, prompt_tokens, prefill = records[0]
        n, requested, prompt_tokens = int(n), int(requested), int(prompt_tokens)
        seconds, prefill = float(seconds), float(prefill)
        if n != result.get('tokens') or requested != n or n != result.get('actual_generated_tokens'):
            failures.append('Actual/requested/planned token counts disagree.')
        if not positive(seconds) or not positive(prefill) or prompt_tokens <= 0:
            failures.append('Invalid final engine timer or prompt count.')
        else:
            rate = n / seconds
            stored = result.get('engine_decode_tok_s')
            if not positive(stored) or not math.isclose(rate, stored, rel_tol=1e-9):
                failures.append('Raw count/timer differs from the stored rate.')
            # This convention is opt-in after source review, not inferred from
            # stdout chunks. Valid only for a complete greedy GLM generation.
            if first_token_from_prefill and n == requested == result.get('tokens') and n > 1:
                steady = (n - 1) / seconds
    lines = [line for line in err.splitlines() if 'replica telemetry:' in line]
    telemetry = []
    if lines:
        for fd, pieces, read, wait, last in FD.findall(lines[-1]):
            fd = int(fd)
            telemetry.append({'fd': fd, 'role': ROLES[fd] if fd < len(ROLES) else f'fd{fd}',
                              'pieces': int(pieces), 'gross_read_ms_per_piece': float(read),
                              'gross_wait_ms_per_piece': float(wait), 'last_demand_batch_pct': int(last)})
    summary = result.get('summary', {})
    window = capture.get('gen_window_s')
    byte_gb = summary.get('gen_window_gb_by_drive', {})
    draw = {role: value / window for role, value in byte_gb.items()
            if positive(window) and isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0}
    return {'stage': Path(directory).name, 'tag': tag, 'layout': result.get('layout'), 'pair': result.get('pair'),
            'generated_tokens': n, 'prompt_tokens': prompt_tokens, 'engine_generation_seconds': seconds,
            'engine_generation_tok_s': rate, 'derived_decode_evals_per_s': steady,
            'engine_prefill_seconds': prefill, 'first_response_s': capture.get('first_byte_s'),
            'output_sha256': digest(output), 'checks_passed': not failures, 'failures': failures,
            'decode_sampler_window_s': window, 'decode_window_gb_s_by_drive': draw,
            'decode_window_gb_per_requested_token': summary.get('gen_window_gb_per_tok'),
            'swap_growth_mb': summary.get('swap_growth_mb'), 'whole_arm_replica_telemetry': telemetry}


def comparisons(rows, candidate):
    groups = []
    for n in (128, 512):
        subset = [r for r in rows if r['generated_tokens'] == n]
        control = [r for r in subset if r['layout'] == 'A4']
        alternate = [r for r in subset if r['layout'] == candidate]
        if not control and not alternate:
            continue
        pairs = []
        for pair in (1, 2, 3):
            a = [r for r in control if r['pair'] == pair]
            b = [r for r in alternate if r['pair'] == pair]
            if len(a) == len(b) == 1 and a[0]['checks_passed'] and b[0]['checks_passed']:
                pairs.append((b[0]['engine_generation_tok_s'] / a[0]['engine_generation_tok_s'] - 1) * 100)
        valid = (len(control) == len(alternate) == len(pairs) == 3
                 and all(r['checks_passed'] for r in subset)
                 and len({r['output_sha256'] for r in subset}) == 1)
        def stats(arms):
            values = [a['engine_generation_tok_s'] for a in arms if a['checks_passed']]
            steady = [a['derived_decode_evals_per_s'] for a in arms if a['checks_passed'] and positive(a['derived_decode_evals_per_s'])]
            return {'count': len(values), 'median': statistics.median(values) if values else None,
                    'range': [min(values), max(values)] if values else None,
                    'derived_decode_evals_median': statistics.median(steady) if len(steady) == len(values) and steady else None}
        a, b = stats(control), stats(alternate)
        groups.append({'tokens': n, 'three_pairs_complete': valid, 'control': a, 'candidate': b,
                       'median_gain_pct': (b['median']/a['median']-1)*100 if positive(a['median']) and positive(b['median']) else None,
                       'paired_gains_pct': pairs, 'worst_pair_gain_pct': min(pairs) if pairs else None})
    return groups


def collect(root, first_token_from_prefill=False):
    root = Path(root).resolve()
    state = json.loads((root / 'autotune.json').read_text())
    names = [stage['name'] for stage in state.get('stages', [])]
    if state.get('active_stage') and state['active_stage'] not in names:
        names.append(state['active_stage'])
    rows, engine_hashes, final_rows, failures = [], set(), [], []
    references = state.get('historical_output_references', {})
    for name in names:
        if not re.fullmatch(r'\d{2}-[a-z0-9-]+', name):
            raise ValueError('Unexpected stage name')
        directory = root.parent / (root.name + '--' + name)
        runtime = json.loads((directory / 'runtime.json').read_text())
        stage = json.loads((directory / 'campaign.json').read_text())
        engine_hashes.add(runtime.get('engine_sha256'))
        stage_rows = [arm_evidence(directory, arm, first_token_from_prefill) for arm in stage.get('arms', [])]
        if name == '06-qualification':
            final_rows = stage_rows
            for row in final_rows:
                reference = references.get(str(row['generated_tokens']))
                if not reference or row['output_sha256'] != reference.get('sha256'):
                    row['checks_passed'] = False
                    row['failures'].append('Historical output identity is missing or differs.')
        rows.extend(stage_rows)
    if len(engine_hashes) != 1 or not re.fullmatch(r'[a-f0-9]{64}', next(iter(engine_hashes), '') or ''):
        failures.append('Stages do not establish one engine binary identity.')
    candidate = state.get('selection', {}).get('layout', 'C5')
    groups = comparisons(final_rows, candidate)
    complete = state.get('status') == 'complete' and len(groups) == 2 and all(g['three_pairs_complete'] for g in groups) and not failures
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'campaign_root': str(root),
            'campaign_status': state.get('status'), 'candidate': candidate, 'arms': rows, 'groups': groups,
            'engine_sha256': sorted(x for x in engine_hashes if isinstance(x, str)), 'failures': failures,
            'final_comparison_complete': complete, 'publication_ready': False,
            'first_token_from_prefill_source_convention': first_token_from_prefill,
            'target_5_engine_rate_met': complete and all(g['candidate']['median'] >= 5 for g in groups),
            'target_5_derived_decode_rate_met': complete and first_token_from_prefill and all(
                positive(g['candidate']['derived_decode_evals_median']) and g['candidate']['derived_decode_evals_median'] >= 5 for g in groups),
            'tuning': state.get('tuning', [])}


def markdown(report):
    lines = ['# GLM five-drive evidence', '', f'Campaign status: **{report["campaign_status"]}**. '
             f'Final comparison complete: **{report["final_comparison_complete"]}**.', '',
             'Engine-native generation rate is actual generated tokens divided by the final engine timer. '
             'These runs use a six-token continuation prompt and greedy generation; they are not pp512 or chat benchmarks.', '',
             '| Generated tokens | Four drives median (range) | Five drives median (range) | Median gain | Worst pair | Complete |',
             '|---|---:|---:|---:|---:|---|']
    def rate(item):
        return f'{item["median"]:.4f} ({item["range"][0]:.4f}–{item["range"][1]:.4f})' if item['range'] else 'pending'
    for group in report['groups']:
        gain = f'{group["median_gain_pct"]:+.2f}%' if group['median_gain_pct'] is not None else 'pending'
        worst = f'{group["worst_pair_gain_pct"]:+.2f}%' if group['worst_pair_gain_pct'] is not None else 'pending'
        lines.append(f'| {group["tokens"]} | {rate(group["control"])} | {rate(group["candidate"])} | {gain} | {worst} | {group["three_pairs_complete"]} |')
    if report['first_token_from_prefill_source_convention']:
        lines += ['', 'In this source-reviewed complete GLM loop, the first output comes from prefill logits. '
                  'N generated tokens require N−1 decode evaluations. The same timer also includes sampling and output. '
                  'The following derived rate uses (N−1)/time; it is not a separately timed kernel measurement.', '',
                  '| Generated tokens | Four-drive decode eval/s | Five-drive decode eval/s |', '|---|---:|---:|']
        for group in report['groups']:
            a, b = (group[k]['derived_decode_evals_median'] for k in ('control', 'candidate'))
            if positive(a) and positive(b): lines.append(f'| {group["tokens"]} | {a:.4f} | {b:.4f} |')
    lines += ['', '## Scheduler screens', '', '| Setting | Median change | Retained |', '|---|---:|---|']
    for item in report['tuning']:
        gain = f'{item["median_gain_pct"]:+.2f}%' if item.get('median_gain_pct') is not None else 'rejected / incomplete'
        lines.append(f'| {item["experiment"]} | {gain} | {item["adopt_for_next_test"]} |')
    lines += ['', '## Measurement limits', '',
              '- Each completed arm retains raw logs, actual counts, an output hash, environment, drive map and existing harness samplers.',
              '- Device GB/s is derived from the harness generation-window GB divided by that capture’s generation-window duration. It is not a peak, an application read rate, or an engine timer.',
              '- Replica read/wait means and last-arrival percentages are whole-arm counters. The engine’s “overlapped” counter counts waits below 0.05 ms; it does not measure GPU overlap.',
              '- Repeated output equality does not establish full-file replica identity or the quality of routed-expert requantization.',
              '- Full model checksum evidence, held-out prompts and public artifact review remain separate publication requirements.',
              '- The 60-token screen had an 11.4% change between its first and last four-drive control; its nominal layout gain is not qualified evidence.', '',
              f'5 tok/s target at both lengths, engine convention: **{report["target_5_engine_rate_met"]}**.',
              f'5 decode eval/s target at both lengths, derived convention: **{report["target_5_derived_decode_rate_met"]}**.', '',
              'Source campaign: `' + report['campaign_root'] + '`', '', 'Engine SHA-256: `' + ', '.join(report['engine_sha256']) + '`', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--first-token-from-prefill', action='store_true', help='Opt in only after reviewing this exact engine loop; derive (N-1)/time for completed greedy GLM runs')
    args = parser.parse_args()
    report = collect(args.campaign, args.first_token_from_prefill)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'evidence.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    (args.out / 'EVIDENCE.md').write_text(markdown(report))
    print(json.dumps({k: report[k] for k in ('campaign_status', 'final_comparison_complete', 'target_5_engine_rate_met', 'target_5_derived_decode_rate_met')}))


if __name__ == '__main__':
    main()
