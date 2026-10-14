"""Independent saved-run checks against the frozen source; no SDK calls."""
from pathlib import Path
import argparse
from hashlib import sha256
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pandas as pd
from trend_exit_research.src.data import DEFAULT_SNAPSHOT, FrozenResearchSnapshot, stock_frame
from trend_exit_research.src.samples import read_samples
from trend_exit_research.src.cases_v0_3 import read_cases

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, default=ROOT / "trend_exit_research/artifacts/v0_3")
out = parser.parse_args().out
receipt = json.loads((out / "run_receipt.json").read_text(encoding="utf-8"))
for name, expected in receipt["outputs"].items():
    assert sha256((out/name).read_bytes()).hexdigest() == expected["sha256"], name
    if "rows" in expected:
        assert len(pd.read_csv(out/name)) == expected["rows"], name
for name, identity in receipt["code_sha256"].items():
    assert sha256((ROOT/name).read_bytes()).hexdigest() == identity, name
audit = pd.read_csv(out / "case_coverage.csv")
risk = pd.read_csv(out / "daily_trend_risk.csv")
daily = pd.read_csv(out / "daily_holding_associations.csv")
links = pd.read_csv(out / "event_holding_links.csv")
actions = pd.read_csv(out / "holding_action_outcomes.csv")
holdings = pd.read_csv(out / "holding_intervals.csv")
assert len(audit) == 17 and audit.stock_name.nunique() == 16
assert not audit.loc[audit.target_status.eq("TARGET_CASE_WINDOW_UNCOVERED"), "computed"].any()
assert not any(k.startswith(("post_", "unexecuted_", "forward_")) for k in daily.columns)
assert not daily.actual_sell_generated.any() and not actions.actual_sell_generated.any()
assert not actions.modelled_position_trade.any()
assert daily.actual_position_quantity.isna().all() and daily.ma28_position_cap.isna().all()
assert actions.actual_realized_profit.isna().all() and holdings.actual_max_floating_profit.isna().all()
assert actions.loc[actions.execution_lane.eq("QUALIFIED_ONLY"), "execution_price"].isna().all()
assert not actions.duplicated(["holding_id", "action"]).any()
assert not holdings.duplicated(["case_id", "confirmation_days", "execution_lane"]).any()
assert links.actual_sell_generated.eq(False).all()
qualification_checks = count_checks = outcome_checks = 0
with FrozenResearchSnapshot(ROOT / DEFAULT_SNAPSHOT) as snapshot:
    master = snapshot.load_universe()
    registry = read_samples(ROOT / "trend_exit_research/inputs/manual_samples.csv", master)
    cases = read_cases(ROOT / "trend_exit_research/inputs/target_cases_v0_3.csv", registry)
    q = receipt["config"]["qualification"]
    for case in cases:
        values = risk.loc[risk.case_id.eq(case["case_id"]) & risk.confirmation_days.eq(3)]
        if values.empty:
            continue
        source = stock_frame(snapshot, case, master)
        source = source.loc[source.trade_date.between(case["warmup_start"], case["evaluation_end"])].reset_index(drop=True).to_dict("records")
        known = values.set_index("trade_date").to_dict("index")
        history, previous = [], None
        for row in source:
            factor = row["factor"]
            event = row["is_xr_sec"] is True or row["is_wd_sec"] is True or previous is not None and factor != previous
            if pd.notna(factor) and factor > 0:
                previous = factor
            if event or row["issue"] not in ("", "SUSPENDED"):
                history = []
            elif row["issue"] == "":
                history.append(row)
                history = history[-60:]
            if row["trade_date"] not in known:
                continue
            actual = known[row["trade_date"]]
            ready = row["issue"] == "" and not event and len(history) == 60
            expected = "TREND_DATA_UNQUALIFIED"
            if ready:
                closes = [x["raw_close"] for x in history]
                ma28 = sum(closes[-28:])/28
                ma55 = sum(closes[-55:])/55
                slope28 = ma28/(sum(closes[-33:-5])/28)-1
                slope55 = ma55/(sum(closes[-60:-5])/55)-1
                runup = closes[-1]/closes[-q["runup_window"]-1]-1
                conditions = [slope28 > q["min_ma28_slope"], slope55 > q["min_ma55_slope"],
                    ma28/ma55-1 > q["min_ma28_over_ma55"], runup >= q["min_runup"], closes[-1] >= ma28,
                    closes[-1]/ma55-1 <= q["max_close_over_ma55"],
                    closes[-1]/max(x["raw_high"] for x in history)-1 >= -q["max_distance_below_peak"]]
                expected = "TREND_ELIGIBLE" if all(conditions) else "TREND_NOT_QUALIFIED"
                assert math.isclose(actual["recent_runup"], runup, rel_tol=1e-9, abs_tol=1e-10)
            assert actual["trend_qualification"] == expected, (case["case_id"], row["trade_date"])
            qualification_checks += 1
        for days, group in risk.loc[risk.case_id.eq(case["case_id"])].groupby("confirmation_days"):
            counts = {28:0,55:0}
            for row in group.to_dict("records"):
                for n, flag in ((28,"reduce_required"),(55,"clear_required")):
                    if row[f"ma{n}_input_status"] == "VALID":
                        counts[n] = counts[n]+1 if row["indicator_close"] < row[f"ma{n}"] else 0
                    elif row[f"ma{n}_input_status"] != "SUSPENDED_PAUSED":
                        counts[n] = 0
                    assert row[f"ma{n}_break_count"] == counts[n]
                    assert bool(row[flag]) == (counts[n] == days and row[f"ma{n}_input_status"] == "VALID")
                    count_checks += 1
        positions = {x["trade_date"]:i for i,x in enumerate(source)}
        for action in actions.loc[actions.case_id.eq(case["case_id"])].to_dict("records"):
            if pd.isna(action["execution_price"]):
                continue
            i, j = positions[action["trade_date"]], positions[action["execution_date"]]
            assert j > i and action["execution_price"] == source[j]["raw_open"]
            assert source[j]["trading_status"] == "TRADING"
            holding = holdings.loc[holdings.holding_id.eq(action["holding_id"])].iloc[0]
            start = positions[holding.association_start]
            peak = max([source[start]["raw_close"]] + [x["raw_high"] for x in source[start+1:i+1]])
            assert math.isclose(action["peak_known_at_signal"], peak, rel_tol=1e-9)
            for h in (5,10,20,40):
                if action[f"post_{h}_status"] != "AVAILABLE":
                    continue
                path = source[j+1:j+h+1]
                assert len(path) == h and all(x["factor"] == source[j]["factor"] and x["issue"] == "" for x in path)
                assert math.isclose(action[f"post_{h}_return"], path[-1]["raw_close"]/action["execution_price"]-1, rel_tol=1e-9,abs_tol=1e-10)
                outcome_checks += 1
# Industrial's frozen three-day confirmations must match the old pilot exactly.
old = pd.read_csv(ROOT / "trend_exit_research/reports/pilot_event_timeline.csv")
old = old.loc[old.security_id.eq("601138.SH") & old.execution_lane.eq("QUALIFIED_ONLY")]
current = links.loc[links.case_id.eq("FOXCONN_2025") & links.confirmation_days.eq(3) & links.execution_lane.eq("QUALIFIED_ONLY")]
assert set(zip(old.confirmation_date,old.ma_period)) == set(zip(current.trade_date,current.ma_period))
result = dict(status="PASS",qualification_checks=qualification_checks,baseline_confirmation_checks=count_checks,
    execution_price_path_checks=outcome_checks,industrial_v02_baseline_events_identical=True,
    no_duplicate_holding_actions=True,no_actual_profit_invented=True,raw_diagnostic_rows=len(links),
    holding_intent_rows=len(actions),actual_sell_count=0,strict_model_price_count=0,
    snapshot_manifest_sha256=receipt["snapshot_validation"]["manifest_sha256"])
(out / "consistency_receipt.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")
print(json.dumps(result))
