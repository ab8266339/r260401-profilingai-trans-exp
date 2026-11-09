"""G13 price-only rerun. Execute only through the compute daemon."""
import argparse
import hashlib
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_SHA = '597c39a0b4ced225cc1db5b1f3eaa7bb7a933d067f20fab0e9fb2c5ad8d52a5b'
CAPS = [None, 64000, 32000, 16000, 8000, 4000, 2000, 1000, 500, 250]
PRICES = {'legacy': [0.1, 0.5, 1.0], 'measured': [0.147, 0.262, 0.328]}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def simulate(sim, family, n, seed, mode, cap, price, short=False):
    for tier, gamma in zip(sim.MODEL_TIERS, PRICES[price]):
        tier.gamma = gamma
    row = sim.run_simulation(n, seed, 3.0 if short else 60.0,
                             1.0 if short else 10.0, mode=mode,
                             state_store_mb=cap, crn_delta=True)
    row.update(sweep=family, state_store_mb=cap, price=price)
    return row


def check_row(row):
    required = ['cohort_generated', 'cohort_met', 'cohort_blocked',
                'cohort_completed', 'cohort_unresolved', 'pipeline_cycles',
                'pipeline_cached', 'pipeline_ul_bytes', 'sla_rate_cohort',
                'mean_total_ms', 'margin_log']
    if any(k not in row for k in required):
        raise RuntimeError('Incomplete simulation output')
    if row['cohort_generated'] != (row['cohort_blocked'] + row['cohort_completed'] + row['cohort_unresolved']):
        raise RuntimeError('Cohort accounting identity failed')
    if not 0 <= row['cohort_met'] <= row['cohort_completed'] <= row['cohort_generated']:
        raise RuntimeError('Cohort bounds failed')
    if not 0 <= row['pipeline_cached'] <= row['pipeline_cycles']:
        raise RuntimeError('Persistence bounds failed')
    if row['pipeline_cycles'] <= 0:
        raise RuntimeError('No completed pipelines; metric denominator invalid')
    json.dumps(row, allow_nan=False)


def metrics(row):
    return {'eligibility': row['pipeline_cached'] / row['pipeline_cycles'],
            'payload_kb': row['pipeline_ul_bytes'] / row['pipeline_cycles'] / 1000,
            'mean_total_ms': row['mean_total_ms'], 'p95_ms': row['p95_ms'],
            'attainment': row['sla_rate_cohort'],
            'blocking': row['cohort_blocked'] / row['cohort_generated'],
            'raises': row['margin_log']['raises'],
            'holds_state': row['margin_log']['holds_state']}


def summarize(rows):
    import numpy as np
    from scipy.stats import t

    def interval(values):
        a = np.asarray(values, dtype=float)
        mean = float(a.mean())
        half = float(t.ppf(.975, len(a)-1) * a.std(ddof=1) / np.sqrt(len(a)))
        return {'mean': mean, 'lo': mean-half, 'hi': mean+half, 'n': len(a)}

    ix = {(r['sweep'], r['N'], r['seed'], r['mode'], r['state_store_mb'], r['price']): metrics(r) for r in rows}
    out = {'inference': '30 paired seeds, unadjusted Student-t 95%; JOINT500 population means within seed',
           'STATE': {}, 'JOINT500': {}}
    for cap in CAPS:
        cells = {}
        for key in metrics(rows[0]):
            legacy = [ix['STATE', 140, s, 'full', cap, 'legacy'][key] for s in range(30)]
            measured = [ix['STATE', 140, s, 'full', cap, 'measured'][key] for s in range(30)]
            cells[key] = {'legacy': interval(legacy), 'measured': interval(measured),
                          'measured_minus_legacy': interval([b-a for a, b in zip(legacy, measured)])}
        out['STATE'][str(cap)] = cells
    for key in metrics(rows[0]):
        results = {}
        for price in PRICES:
            arm_values = {}
            for arm in ('full', 'joint_full'):
                values = [float(np.mean([ix['JOINT500', n, s, arm, 500, price][key] for n in range(70, 101)])) for s in range(30)]
                arm_values[arm] = values
                results[price + '|' + arm] = interval(values)
            results[price + '|joint_full-minus-full'] = interval([b-a for a, b in zip(arm_values['full'], arm_values['joint_full'])])
        for arm in ('full', 'joint_full'):
            diff = [float(np.mean([ix['JOINT500', n, s, arm, 500, 'measured'][key] - ix['JOINT500', n, s, arm, 500, 'legacy'][key] for n in range(70, 101)])) for s in range(30)]
            results[arm + '|measured-minus-legacy'] = interval(diff)
        out['JOINT500'][key] = results
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['smoke', 'formal'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    begin = time.time()
    try:
        actual_sha = hashlib.sha256((ROOT/'oran_mmas_sim.py').read_bytes()).hexdigest()
        if actual_sha != SOURCE_SHA:
            raise RuntimeError('Archived simulator bytes changed')
        import oran_mmas_sim as sim
        import numpy
        import scipy
        import simpy
        env = {'host': socket.gethostname(), 'machine': os.getenv('RCQ_MACHINE'),
               'job': os.getenv('RCQ_JOB'), 'python': sys.version,
               'platform': platform.platform(), 'numpy': numpy.__version__,
               'scipy': scipy.__version__, 'simpy': simpy.__version__,
               'source_sha256': actual_sha,
               'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'phase': args.phase, 'prices': PRICES, 'numbers_for_manuscript': args.phase == 'formal'}
        write(args.out/'environment.json', env)
        anchors = json.loads((ROOT/'legacy_anchors.json').read_text(encoding='utf-8'))
        checks = []
        for anchor in anchors:
            got = simulate(sim, anchor['sweep'], anchor['N'], anchor['seed'], anchor['mode'], anchor['state_store_mb'], 'legacy')
            check_row(got)
            differences = {k: {'expected': v, 'actual': got.get(k)} for k, v in anchor.items() if got.get(k) != v}
            checks.append({'sweep': anchor['sweep'], 'mode': anchor['mode'], 'differences': differences})
        write(args.out/'regression.json', {'exact_equal': all(not c['differences'] for c in checks), 'checks': checks})
        if any(c['differences'] for c in checks):
            raise RuntimeError('Cross-machine exact regression failed; no scientific rerun allowed')
        if args.phase == 'smoke':
            jobs = [('SMOKE', 140, 10001, 'full', cap) for cap in (None, 250)]
            jobs += [('SMOKE', 70, 10001, mode, 500) for mode in ('full', 'joint_full')]
        else:
            jobs = [('STATE', 140, seed, 'full', cap) for cap in CAPS for seed in range(30)]
            jobs += [('JOINT500', n, seed, mode, 500) for n in range(70, 101) for seed in range(30) for mode in ('full', 'joint_full')]
        rows = []
        with (args.out/'raw.jsonl').open('w', encoding='utf-8') as stream:
            for index, (family, n, seed, mode, cap) in enumerate(jobs):
                # Same process, adjacent pairs; no cross-worker or cross-protocol timing comparison.
                order = ('legacy', 'measured') if index % 2 == 0 else ('measured', 'legacy')
                for price in order:
                    row = simulate(sim, family, n, seed, mode, cap, price, args.phase == 'smoke')
                    check_row(row)
                    rows.append(row)
                    stream.write(json.dumps(row, allow_nan=False) + '\n')
                    stream.flush()
                if index % 10 == 0 or index + 1 == len(jobs):
                    print(f'{index+1}/{len(jobs)} pairs; elapsed {time.time()-begin:.1f}s', flush=True)
        if args.phase == 'formal':
            write(args.out/'summary.json', summarize(rows))
        write(args.out/'verification.json', {'status': 'technical_pass', 'phase': args.phase,
              'rows': len(rows), 'regression_exact': True, 'cohort_checks': True,
              'elapsed_s': time.time()-begin, 'scientific_pass': None,
              'interpretation': 'No direction-based pass threshold; report reversals and unresolved contrasts.'})
    except Exception as exc:
        write(args.out/'failure.json', {'error': str(exc), 'elapsed_s': time.time()-begin})
        raise


if __name__ == '__main__':
    main()
