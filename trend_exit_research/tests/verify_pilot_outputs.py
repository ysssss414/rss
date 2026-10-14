"""Independent delivery check against the frozen source, never a signal input."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hashlib import sha256
import json
import math
import pandas as pd
from trend_exit_research.src.data import DEFAULT_SNAPSHOT, FrozenResearchSnapshot, stock_frame
from trend_exit_research.src.samples import read_samples

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description='Independently verify a saved pilot against its frozen source')
parser.add_argument('--out', type=Path, default=root / 'trend_exit_research/artifacts/pilot_v0_2')
out = parser.parse_args().out
receipt = json.loads((out/'run_receipt.json').read_text(encoding='utf-8'))
for name, expected in receipt['outputs'].items():
    assert sha256((out/name).read_bytes()).hexdigest() == expected['sha256'], name
    if 'rows' in expected:
        assert len(pd.read_csv(out/name)) == expected['rows'], name
for name, identity in receipt['code_sha256'].items():
    assert sha256((root/name).read_bytes()).hexdigest() == identity, name
d = pd.read_csv(out/'daily_signals.csv')
e = pd.read_csv(out/'execution_events.csv')
c = pd.read_csv(out/'cohort_daily.csv')
assert not any(x.startswith(('post_', 'forward_', 'unexecuted_')) for x in d.columns)
assert d.decision_at.eq(d.trade_date+'T15:30:00+08:00').all()
assert d.latest_input_date.eq(d.trade_date).all()
assert e.actual_execution_status.eq('NOT_OBSERVED_NO_ORDER_SENT').all()
assert e.loc[e.execution_lane.eq('QUALIFIED_ONLY'), 'execution_price'].isna().all()
count_checks = ma_checks = outcome_checks = 0
with FrozenResearchSnapshot(root/DEFAULT_SNAPSHOT) as snapshot:
    master = snapshot.load_universe()
    samples = read_samples(root/'trend_exit_research/inputs/manual_samples.csv', master)
    sources = {}
    for code, group in d.groupby('security_id'):
        sample = next(x for x in samples if x['resolved_code']==code)
        source = stock_frame(snapshot, sample, master).to_dict('records')
        sources[code] = source
        reference = group.loc[group.confirmation_days.eq(3)].set_index('trade_date').to_dict('index')
        history, previous = [], None
        for row in source:
            factor = row['factor']
            action = row['is_xr_sec'] is True or row['is_wd_sec'] is True or previous is not None and factor != previous
            if pd.notna(factor) and factor > 0:
                previous = factor
            if action or row['issue'] not in ('','SUSPENDED'):
                history = []
            elif row['issue']=='':
                history.append(row)
                history = history[-60:]
            if row['trade_date'] not in reference:
                continue
            actual = reference[row['trade_date']]
            for n in (28,55):
                expected = sum(x['raw_close'] for x in history[-n:])/n if not action and row['issue']=='' and len(history)>=n else None
                assert pd.isna(actual[f'ma{n}']) if expected is None else math.isclose(actual[f'ma{n}'],expected,rel_tol=1e-10,abs_tol=1e-9)
                ma_checks += 1
        for days, variant in group.groupby('confirmation_days'):
            counts={28:0,55:0}; starts={28:None,55:None}
            for row in variant.to_dict('records'):
                for n,flag in ((28,'reduce_required'),(55,'clear_required')):
                    if row['ma%d_input_status'%n]=='VALID':
                        if row['indicator_close'] < row['ma%d'%n]:
                            counts[n]+=1
                            if counts[n]==1:
                                starts[n]=row['trade_date']
                        else:
                            counts[n]=0; starts[n]=None
                    elif row['ma%d_input_status'%n]!='SUSPENDED_PAUSED':
                        counts[n]=0; starts[n]=None
                    assert row[f'ma{n}_break_count']==counts[n]
                    assert bool(row[flag])==(counts[n]==days and row['ma%d_input_status'%n]=='VALID')
                    actual_start=row[f'ma{n}_episode_start']
                    assert pd.isna(actual_start) if starts[n] is None else actual_start==starts[n]
                    count_checks += 1
        for event in e.loc[e.security_id.eq(code)].to_dict('records'):
            if pd.isna(event['execution_price']):
                assert pd.isna(event['execution_date'])
                continue
            i=next(i for i,x in enumerate(source) if x['trade_date']==event['confirmation_date'])
            j=i+1
            while source[j]['trading_status']=='SUSPENDED':
                j+=1
            assert event['execution_date']==source[j]['trade_date']>event['confirmation_date']
            assert event['execution_price']==source[j]['raw_open']
            for h in (5,10,20,40):
                if event[f'post_{h}_status']!='AVAILABLE':
                    assert pd.isna(event[f'post_{h}_return'])
                    continue
                path=source[j+1:j+h+1]
                assert len(path)==h and all(x['factor']==source[j]['factor'] and x['issue']=='' for x in path)
                price=event['execution_price']
                assert math.isclose(event[f'post_{h}_return'],path[-1]['raw_close']/price-1,rel_tol=1e-9,abs_tol=1e-10)
                assert math.isclose(event[f'post_{h}_mae'],min(0,min(x['raw_low']/price-1 for x in path)),rel_tol=1e-9,abs_tol=1e-10)
                outcome_checks+=1
strict=c.loc[c.execution_lane.eq('QUALIFIED_ONLY')]
assert strict.remaining_shares.eq(1).all() and strict.cash.eq(0).all()
for _, path in c.groupby(['security_id','confirmation_days','cohort_date','execution_lane','scheme','scenario_cap']):
    assert path.remaining_shares.diff().dropna().le(1e-10).all()
result=dict(status='PASS',snapshot_manifest_sha256=receipt['snapshot_validation']['manifest_sha256'],
    output_hashes_verified=len(receipt['outputs']), independent_ma_checks=ma_checks,
    independent_confirmation_checks=count_checks, independent_execution_outcome_checks=outcome_checks,
    daily_rows=len(d),event_rows=len(e),cohort_path_rows=len(c),strict_fills=0,
    no_future_outcomes_in_signal_table=True,no_signal_close_fills=True,no_automatic_reentry=True)
(out/'real_consistency.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
