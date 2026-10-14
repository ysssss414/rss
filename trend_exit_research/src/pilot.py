"""Bounded offline pilot; future outcomes never enter the frozen signal engine."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess

import pandas as pd

from .cli import MODULE, ROOT
from .data import DEFAULT_SNAPSHOT, FrozenResearchSnapshot, coverage_row, stock_frame
from .evaluation import _execution, _path_status
from .indicators import calculate, config_hash, load_config
from .risk import apply_risk, load_risk_config
from .samples import read_samples


FILLED = "CONDITIONAL_MODELLED_FILL"
LANES = ("QUALIFIED_ONLY", "CONDITIONAL_CACHED_LIMITS")


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def segment_counts(frame):
    """Mirror the frozen event-free indicator warmup, without computing 16 stocks."""
    counts, count, previous = [], 0, None
    for row in frame.to_dict("records"):
        factor = row["factor"]
        event = (row.get("is_xr_sec") is True or row.get("is_wd_sec") is True
                 or previous is not None and pd.notna(factor) and factor != previous)
        if pd.notna(factor) and factor > 0:
            previous = factor
        if event or row["issue"] not in ("", "SUSPENDED"):
            count = 0
        elif row["issue"] == "":
            count += 1
        counts.append(count)
    return counts


def audit_sample(sample, frame, snapshot):
    record = coverage_row(sample, frame, snapshot)
    counts = segment_counts(frame)
    selected = frame.trade_date.between(record["effective_start"], record["effective_end"]) if not frame.empty else pd.Series(False, index=frame.index)
    indices = frame.index[selected].tolist()
    first = indices[0] if indices else None
    ready = [i for i in indices if counts[i] >= 55 and frame.iloc[i].issue == ""]
    ready_set, ready_run, three_possible = set(ready), 0, False
    for i in indices:
        if frame.iloc[i].issue == "SUSPENDED" and counts[i] > 0:
            continue
        ready_run = ready_run + 1 if i in ready_set else 0
        three_possible |= ready_run >= 3
    record.update(full_calendar_sessions=len(frame), full_missing_dates="|".join(
        frame.loc[~frame.issue.isin(["", "SUSPENDED"]), "trade_date"]) if not frame.empty else "",
        pre_start_event_free_observations=counts[first-1] if first and first > 0 else 0,
        ma55_ready_at_first_session=bool(first is not None and counts[first] >= 55),
        ma55_ready_sessions=len(ready), ma55_first_ready_date=frame.iloc[ready[0]].trade_date if ready else None,
        three_day_input_possible=three_possible,
        future40_input_possible=any(i + 40 < len(frame) and _path_status(
            frame.iloc[i+1:i+41].to_dict("records"), frame.iloc[i].to_dict()) == "AVAILABLE" for i in ready),
        historical_publication_time_qualified=False, morning_execution_constraints_qualified=False,
        factor_source_hash=snapshot.manifest["datasets"]["adjustment_factor"]["fingerprint"],
        bars_source_hash=snapshot.manifest["datasets"]["daily_bars"]["fingerprint"],
        status_source_hash=snapshot.manifest["datasets"]["daily_status"]["fingerprint"],
        price_basis="RAW_EXECUTION_PRICES;T_ANCHORED_EVENT_FREE_QFQ_INDICATORS",
        calendar_beyond_cutoff="UNKNOWN_NOT_COUNTED_AS_ZERO_MISSING",
        adequacy=sample["resolution_status"] if sample["resolution_status"] != "RESOLVED" else
            "PARTIAL_EXPLORATORY_PRICE_PATHS_ONLY" if ready else "NO_IN_INTERVAL_MA55_DATA")
    return record


def conditional_execution(source, position):
    """EOD vendor-limit scenario, explicitly below historical morning qualification.

    No authority or publication timestamp is invented. Only ordinary, event-free
    cached records with mutually consistent reference/rate/tick/limits are used.
    A sell at the lower limit is not assumed filled, even if it opens later.
    """
    result = dict(execution_status="EXECUTION_UNRESOLVED", execution_reason=None,
                  execution_date=None, execution_price=None, skipped_suspended_sessions=0,
                  order_status="RESEARCH_INTENT_ONLY_NO_ORDER_SENT", order_model_at=None,
                  actual_execution_status="NOT_OBSERVED_NO_ORDER_SENT")
    target = position + 1
    while target < len(source) and source[target]["trading_status"] == "SUSPENDED":
        result["skipped_suspended_sessions"] += 1
        target += 1
    if target >= len(source):
        result["execution_reason"] = "NO_FOLLOWING_TRADING_SESSION"
        return result
    current = source[target]
    result["order_model_at"] = current["trade_date"] + "T09:15:00+08:00"
    reason = None
    if current["trading_status"] != "TRADING" or current["issue"]:
        reason = "MISSING_OR_INVALID_NEXT_TRADING_DATA"
    elif any(x["factor"] != source[position]["factor"] or x.get("is_xr_sec") is True
             or x.get("is_wd_sec") is True for x in source[position+1:target+1]):
        reason = "UNQUALIFIED_ACTION_BEFORE_EXECUTION"
    elif current.get("is_st_sec") is not False:
        reason = "ST_OR_UNKNOWN_STATUS_NOT_SUPPORTED"
    else:
        try:
            opening, lower, upper, reference, rate, down_rate = [Decimal(str(current.get(key))) for key in
                ("raw_open", "low_limited", "high_limited", "preclose", "price_high_lmt_rate", "price_low_lmt_rate")]
            tick = Decimal("0.01")
            previous_close = Decimal(str(source[position]["raw_close"]))
            if not all(x.is_finite() and x > 0 for x in (opening, lower, upper, reference, rate, down_rate)):
                reason = "INVALID_CACHED_LIMITS"
            elif rate not in (Decimal("0.1"), Decimal("0.2")) or rate != down_rate:
                reason = "UNSUPPORTED_CACHED_RATE"
            elif reference != previous_close:
                reason = "REFERENCE_PRICE_DISAGREEMENT"
            elif (any(x != x.quantize(tick) for x in (opening, lower, upper))
                  or lower != (reference * (1-rate)).quantize(tick, rounding=ROUND_HALF_UP)
                  or upper != (reference * (1+rate)).quantize(tick, rounding=ROUND_HALF_UP)
                  or not lower <= opening <= upper):
                reason = "CACHED_LIMIT_OR_TICK_DISAGREEMENT"
            elif opening <= lower:
                result["execution_status"] = "NO_FILL"
                reason = "SELL_OPEN_AT_LOWER_LIMIT_NO_ASSUMED_LIQUIDITY"
            else:
                result.update(execution_status=FILLED, execution_date=current["trade_date"],
                              execution_price=float(opening))
                reason = "UNQUALIFIED_EOD_LIMIT_SCENARIO_NEXT_TRADABLE_OPEN"
        except (InvalidOperation, TypeError, ValueError):
            reason = "MISSING_OR_INVALID_CACHED_LIMITS"
    result["execution_reason"] = reason
    return result


def execution(source, position, signal, lane):
    if lane == "CONDITIONAL_CACHED_LIMITS":
        return conditional_execution(source, position)
    result = _execution(source, position, signal)
    return dict(execution_status=result["execution_status"], execution_reason=result["execution_reason"],
                execution_date=result["next_observed_trading_date"] if result["execution_status"] == "MODELLED_FILL" else None,
                execution_price=result["execution_model_price"], order_status=result["order_status"],
                order_model_at=result["order_model_at"], actual_execution_status=result["actual_execution_status"],
                skipped_suspended_sessions=result["skipped_suspended_sessions"])


def future_metrics(source, position, price, horizons):
    """D+1..D+H exchange sessions, excluding execution day; raw event-free paths."""
    result = {}
    for h in horizons:
        path = source[position+1:position+h+1]
        status = "RIGHT_CENSORED" if len(path) != h else _path_status(path, source[position])
        result[f"post_{h}_status"] = status
        result[f"post_{h}_date"] = path[-1]["trade_date"] if len(path) == h else None
        available = status == "AVAILABLE"
        result[f"post_{h}_return"] = path[-1]["raw_close"] / price - 1 if available else None
        result[f"post_{h}_mae"] = min(0., min(x["raw_low"] / price - 1 for x in path)) if available else None
        result[f"post_{h}_max_upside_proxy"] = max(0., max(x["raw_high"] / price - 1 for x in path)) if available else None
        result[f"post_{h}_terminal_opportunity_proxy"] = max(0., result[f"post_{h}_return"]) if available else None
    return result


def event_table(risk, frame, sample, pilot_config):
    source = frame.to_dict("records")
    positions = {x["trade_date"]: i for i, x in enumerate(source)}
    known_ma = {x["trade_date"]: x for x in risk.to_dict("records")}
    rows = []
    for signal in risk.to_dict("records"):
        for period, flag in ((28, "reduce_required"), (55, "clear_required")):
            if not signal[flag]:
                continue
            i = positions[signal["trade_date"]]
            for lane in LANES:
                row = {key: signal[key] for key in ("security_id", "trade_date", "decision_at", "source_snapshot_id",
                    "snapshot_manifest_sha256", "factor_source_hash", "risk_rule_version", "risk_config_sha256",
                    "confirmation_days", "risk_scenario", "rolling_high", "POSITION_RISK_STATE")}
                row.update(stock_name=sample["stock_name"], sample_id=sample["sample_id"], ma_period=period,
                    first_break_date=signal[f"ma{period}_episode_start"], confirmation_date=signal["trade_date"],
                    signal_close=signal["raw_close"], confirmation_drawdown=signal["drawdown"],
                    confirmation_delay_return=signal[f"ma{period}_change_since_first_break"], execution_lane=lane,
                    reason=f'{signal["confirmation_days"]} valid closes below each T MA{period}',
                    pilot_config_sha256=config_hash(pilot_config), outcome_only=True,
                    **execution(source, i, signal, lane))
                price = row["execution_price"]
                filled = price is not None
                row.update(execution_drawdown=price / signal["rolling_high"] - 1
                           if filled and pd.notna(signal["rolling_high"]) else None,
                    followup_status="MODELLED_EXIT_NO_REENTRY" if filled else "EXPOSURE_RETAINED_REQUIRES_REVIEW_NO_AUTO_RETRY")
                path = source[i+1:i+6]
                status = "RIGHT_CENSORED" if len(path) != 5 else _path_status(path, source[i])
                if status == "AVAILABLE" and any(x["trade_date"] not in known_ma
                        or pd.isna(known_ma[x["trade_date"]][f"ma{period}"]) for x in path):
                    status = "MA_EVALUATION_RANGE_CENSORED"
                row.update(recross5_status=status, recross_ma_within5=any(x["raw_close"] >=
                    known_ma[x["trade_date"]][f"ma{period}"] for x in path) if status == "AVAILABLE" else None)
                if filled:
                    row.update(future_metrics(source, positions[row["execution_date"]], price, pilot_config["horizons"]))
                else:
                    row.update({f"post_{h}_status": "NO_EXECUTION_ANCHOR" for h in pilot_config["horizons"]})
                    exposure = future_metrics(source, i, signal["raw_close"], pilot_config["horizons"])
                    row.update({"unexecuted_" + key: value for key, value in exposure.items()})
                rows.append(row)
    return pd.DataFrame(rows)


def cohort_comparison(risk, frame, events, pilot_config):
    """Paired event cohorts, not an entry/continuous/reentry strategy backtest.

    Each cohort assumes one preexisting share at MA28 confirmation close. That
    close marks initial NAV, never an execution. First later MA55 confirmation
    is processed causally. B fixes remaining shares to its NAV cap at that open;
    subsequent weight drift is reported, without inventing ongoing rebalances.
    """
    source = frame.to_dict("records")
    positions = {x["trade_date"]: i for i, x in enumerate(source)}
    signals = risk.to_dict("records")
    summaries, paths = [], []
    for signal in signals:
        if not signal["reduce_required"] or signal["row_scope"] != "STUDY":
            continue
        i = positions[signal["trade_date"]]
        later55 = next((x for x in signals if x["trade_date"] >= signal["trade_date"] and x["clear_required"]), None)
        prior55 = signal["ma55_break_count"] > signal["confirmation_days"]
        for lane in LANES:
            e28 = events.loc[events.confirmation_date.eq(signal["trade_date"]) & events.ma_period.eq(28)
                            & events.execution_lane.eq(lane)].iloc[0].to_dict()
            e55 = None
            if later55 is not None:
                match = events.loc[events.confirmation_date.eq(later55["trade_date"]) & events.ma_period.eq(55)
                                   & events.execution_lane.eq(lane)]
                e55 = match.iloc[0].to_dict()
            scenarios = [("A_MA28_FULL", 0.), *[("B_MA28_CAP_MA55_FULL", cap) for cap in
                pilot_config["remaining_position_scenarios"]], ("C_MA55_FULL", 1.), ("D_HOLD", 1.)]
            for scheme, cap in scenarios:
                base = {key: signal[key] for key in ("security_id", "source_snapshot_id", "snapshot_manifest_sha256",
                    "risk_rule_version", "risk_config_sha256", "confirmation_days", "stock_name", "sample_id")}
                base.update(cohort_date=signal["trade_date"], execution_lane=lane, scheme=scheme, scenario_cap=cap,
                    pilot_config_sha256=config_hash(pilot_config), prior_ma55_confirmed=prior55,
                    cohort_assumption="PREEXISTING_ONE_UNIT;OVERLAPPING_DIAGNOSTIC_COHORTS;NO_AUTOMATIC_REENTRY")
                q, cash, peak, worst, max_weight, fill_count, unresolved = 1., 0., 1., 0., 0., 0, 0
                valid = not prior55
                navs, dds, weights_at_horizon = {}, {}, {}
                for j in range(i+1, min(i+41, len(source))):
                    current = source[j]
                    valid = valid and _path_status([current], source[i]) == "AVAILABLE"
                    intents = []
                    simultaneous_clear = scheme.startswith("B_") and e55 is not None and e55["confirmation_date"] == e28["confirmation_date"]
                    if scheme.startswith(("A_", "B_")) and not simultaneous_clear:
                        intents.append((e28, cap))
                    if scheme.startswith(("B_", "C_")) and e55:
                        intents.append((e55, 0.))
                    # A simultaneous MA55 confirmation replaces the weaker B reduction.
                    for event, remaining in intents:
                        if event["execution_date"] == current["trade_date"] and q > 0:
                            price = event["execution_price"]
                            if pd.notna(price):
                                nav_open = cash + q * price
                                new_q = min(q, remaining * nav_open / price)
                                cash += (q-new_q) * price
                                q = new_q
                                fill_count += 1
                        attempt_day = (event.get("order_model_at") or "")[:10]
                        if attempt_day == current["trade_date"] and pd.isna(event["execution_price"]):
                            unresolved += 1
                    nav = (cash + q * current["raw_close"]) / signal["raw_close"] if valid else None
                    if nav is not None:
                        peak = max(peak, nav)
                        worst = min(worst, nav / peak - 1)
                        weight = q * current["raw_close"] / (cash + q * current["raw_close"])
                        # Only assess B cap drift after the first conditional reduction.
                        if scheme.startswith("B_") and fill_count:
                            max_weight = max(max_weight, weight)
                    else:
                        weight = None
                    h = j-i
                    navs[h], dds[h] = nav, worst if valid else None
                    weights_at_horizon[h] = max_weight if scheme.startswith("B_") and fill_count else None
                    paths.append(base | dict(trade_date=current["trade_date"], elapsed_sessions=h,
                        remaining_shares=q, cash=cash, normalized_nav=nav, weight=weight,
                        path_status="AVAILABLE" if valid else "PRIOR_EXIT_OR_UNQUALIFIED_PATH", modelled_sell_count=fill_count,
                        followup_status="MODELLED_EXIT_NO_REENTRY" if q == 0 else
                            "EXPOSURE_RETAINED_REQUIRES_REVIEW" if unresolved else "NO_REENTRY_MODELLED"))
                for h in pilot_config["horizons"]:
                    summary = base | dict(horizon=h, evaluation_status="AVAILABLE" if navs.get(h) is not None else
                        "PRIOR_MA55_ALREADY_CONFIRMED" if prior55 else "CENSORED_OR_UNQUALIFIED_PATH",
                        return_value=navs[h]-1 if navs.get(h) is not None else None, max_drawdown=dds.get(h),
                        max_b_weight_after_reduction=weights_at_horizon.get(h),
                        cap_maintenance="NOT_IMPLEMENTED_ONE_REBALANCE_THEN_WEIGHT_DRIFT", fees_and_slippage="NOT_MODELLED")
                    summaries.append(summary)
    summary = pd.DataFrame(summaries)
    if not summary.empty:
        keys = ["security_id", "confirmation_days", "cohort_date", "execution_lane", "horizon"]
        hold = summary.loc[summary.scheme.eq("D_HOLD"), keys + ["return_value", "max_drawdown"]].rename(
            columns={"return_value": "hold_return", "max_drawdown": "hold_max_drawdown"})
        summary = summary.merge(hold, on=keys, validate="many_to_one")
        summary["return_gap_vs_hold"] = summary.return_value - summary.hold_return
        summary["drawdown_improvement_vs_hold"] = summary.max_drawdown - summary.hold_max_drawdown
        summary["terminal_opportunity_cost_vs_hold"] = (summary.hold_return-summary.return_value).clip(lower=0)
    return summary, pd.DataFrame(paths)


def summary_tables(events, cohorts):
    event_rows, scheme_rows = [], []
    for keys, group in events.groupby(["stock_name", "security_id", "confirmation_days", "ma_period", "execution_lane"], sort=True):
        event_rows.append(dict(zip(("stock_name", "security_id", "confirmation_days", "ma_period", "execution_lane"), keys)) |
            dict(confirmations=len(group), fills=int(group.execution_price.notna().sum()),
                no_fill=int(group.execution_status.eq("NO_FILL").sum()), unresolved=int(group.execution_status.eq("EXECUTION_UNRESOLVED").sum()),
                recross5_evaluable=int(group.recross5_status.eq("AVAILABLE").sum()),
                recross5_count=int(group.recross_ma_within5.eq(True).sum()),
                mean_confirmation_delay_return=group.confirmation_delay_return.mean(),
                mean_peak_drawdown_at_confirmation=group.confirmation_drawdown.mean(),
                mean_peak_drawdown_at_execution=group.execution_drawdown.mean(),
                post40_evaluable=int(group.post_40_status.eq("AVAILABLE").sum()),
                mean_post40_return=group.post_40_return.mean() if "post_40_return" in group else None,
                mean_post40_mae=group.post_40_mae.mean() if "post_40_mae" in group else None,
                mean_post40_max_upside_proxy=group.post_40_max_upside_proxy.mean() if "post_40_max_upside_proxy" in group else None))
    available = cohorts.loc[cohorts.evaluation_status.eq("AVAILABLE")]
    for keys, group in available.groupby(["stock_name", "security_id", "confirmation_days", "execution_lane", "scheme", "scenario_cap", "horizon"], sort=True):
        scheme_rows.append(dict(zip(("stock_name", "security_id", "confirmation_days", "execution_lane", "scheme", "scenario_cap", "horizon"), keys)) |
            dict(evaluable_cohorts=len(group), mean_return=group.return_value.mean(), mean_max_drawdown=group.max_drawdown.mean(),
                mean_drawdown_improvement=group.drawdown_improvement_vs_hold.mean(),
                mean_return_gap_vs_hold=group.return_gap_vs_hold.mean(),
                mean_terminal_opportunity_cost=group.terminal_opportunity_cost_vs_hold.mean(),
                worse_than_hold_count=int(group.return_gap_vs_hold.lt(0).sum()),
                better_than_hold_count=int(group.return_gap_vs_hold.gt(0).sum())))
    return pd.DataFrame(event_rows), pd.DataFrame(scheme_rows)


def write_tables(out, tables, provenance):
    out.mkdir(parents=True, exist_ok=True)
    inventory = {}
    for name, table in tables.items():
        table.to_csv(out / name, index=False, encoding="utf-8-sig", float_format="%.12g", lineterminator="\n")
        inventory[name] = dict(rows=len(table), sha256=digest(out / name))
    receipt = provenance | dict(outputs=inventory, no_download=True, no_real_orders=True,
        execution_grades_separate=True, strategy_effectiveness="STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED")
    (out / "run_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline fixed-four-case exit pilot; no optimization")
    parser.add_argument("--snapshot", type=Path, default=ROOT / DEFAULT_SNAPSHOT)
    parser.add_argument("--out", type=Path, default=MODULE / "artifacts/pilot_v0_2")
    args = parser.parse_args(argv)
    if not args.out.resolve().is_relative_to((MODULE / "artifacts").resolve()):
        parser.error("Outputs must remain inside module artifacts")
    pilot = json.loads((MODULE / "config/pilot_v0_2.json").read_text(encoding="utf-8"))
    candidate = load_config(MODULE / "config/default.json")
    baseline = load_risk_config(MODULE / "config/risk_baseline.json")
    audits, daily_tables, signal_tables, event_tables, comparisons, cohort_paths = [], [], [], [], [], []
    with FrozenResearchSnapshot(args.snapshot) as snapshot:
        master = snapshot.load_universe()
        samples = read_samples(MODULE / "inputs/manual_samples.csv", master)
        for sample in samples:
            if sample["resolution_status"] != "RESOLVED":
                audits.append(audit_sample(sample, pd.DataFrame(), snapshot))
                continue
            frame = stock_frame(snapshot, sample, master)
            record = audit_sample(sample, frame, snapshot)
            audits.append(record)
            if sample["stock_name"] not in pilot["cases"]:
                continue
            start, end = record["effective_start"], record["effective_end"]
            # MA prehistory is allowed; break counts/risk state start at the study boundary.
            end_indices = frame.index[frame.trade_date.le(end)].tolist()
            stop = min(len(frame), end_indices[-1]+41) if end_indices else 0
            features, _ = calculate(frame.iloc[:stop], candidate)
            study = features.loc[features.trade_date.ge(start)].copy().reset_index(drop=True)
            for days in pilot["confirmation_days"]:
                risk, signals = apply_risk(study, baseline, confirmation_days=days)
                for table in (risk, signals):
                    table["stock_name"], table["sample_id"] = sample["stock_name"], sample["sample_id"]
                    table["row_scope"] = table.trade_date.map(lambda day: "STUDY" if day <= end else "OUTCOME_EXTENSION")
                    table["pilot_config_sha256"] = config_hash(pilot)
                events = event_table(risk, frame, sample, pilot)
                comparison, path = cohort_comparison(risk, frame, events, pilot)
                daily_tables.append(risk)
                signal_tables.append(signals)
                # Extension signals are retained separately in the signal log for cohort decisions.
                event_tables.append(events.loc[events.confirmation_date.le(end)])
                comparisons.append(comparison)
                cohort_paths.append(path)
            print(json.dumps(dict(computed=sample["stock_name"], start=start, end=end), ensure_ascii=False), flush=True)
        tables = {"coverage_audit.csv": pd.DataFrame(audits), "daily_signals.csv": pd.concat(daily_tables, ignore_index=True),
            "signal_log.csv": pd.concat(signal_tables, ignore_index=True), "execution_events.csv": pd.concat(event_tables, ignore_index=True),
            "cohort_comparison.csv": pd.concat(comparisons, ignore_index=True), "cohort_daily.csv": pd.concat(cohort_paths, ignore_index=True)}
        tables["confirmation_summary.csv"], tables["scheme_summary.csv"] = summary_tables(
            tables["execution_events.csv"], tables["cohort_comparison.csv"])
        provenance = dict(schema="trend-exit-pilot/0.2", python=platform.python_version(), pandas=pd.__version__,
            git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            v01_release_commit="10e720cb4367e00fae643493116572f4cbcd17c5", config=pilot, pilot_config_sha256=config_hash(pilot),
            candidate_config_sha256=config_hash(candidate), risk_config_sha256=config_hash(baseline),
            source_snapshot_id=snapshot.snapshot_id, snapshot_validation=snapshot.validation,
            code_sha256={p.relative_to(ROOT).as_posix(): digest(p) for p in sorted((MODULE / "src").glob("*.py"))},
            reused_code_sha256={p: digest(ROOT / p) for p in ("research/snapshot.py", "research/data/adjustment.py",
                "research/factor_pit.py", "research/foundation.py")},
            samples_sha256=digest(MODULE / "inputs/manual_samples.csv"),
            interpretation="MANUAL_CASES_OVERLAPPING_COHORTS_NO_UNBIASED_SUCCESS_RATE",
            no_parameter_search=True, no_automatic_reentry=True,
            strict_execution="HISTORICAL_MORNING_CONSTRAINT_REQUIRED",
            conditional_execution="EOD_VENDOR_LIMITS_NOT_HISTORICALLY_QUALIFIED;NO_LIQUIDITY_OR_SLIPPAGE_PROOF",
            horizon_definition="D+1..D+H_EXCHANGE_SESSIONS;EXECUTION_DAY_EXCLUDED;EVENT_FREE_RAW_PRICE_PATH",
            risk_boundary="COUNTS_RESET_AT_STUDY_START;PREHISTORY_FOR_INDICATORS_ONLY")
        receipt = write_tables(args.out, tables, provenance)
        from .pilot_report import render_report
        report = args.out / "trend_exit_v0_2_pilot_report.md"
        report.write_text(render_report(tables, receipt), encoding="utf-8")
        receipt["outputs"][report.name] = dict(sha256=digest(report))
        (args.out / "run_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(dict(status="PILOT_CALCULATION_COMPLETE", output=str(args.out.resolve()), outputs=receipt["outputs"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
