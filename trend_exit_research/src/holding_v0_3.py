"""Causal observation intervals and exit associations; no invented entry trades."""
from __future__ import annotations

import pandas as pd

from .indicators import config_hash
from .pilot import execution, future_metrics


def associate_holdings(daily, frame, case, config, *, lane):
    source = frame.to_dict("records")
    positions = {row["trade_date"]: i for i, row in enumerate(source)}
    rows, diagnostics, actions = [], [], []
    pending = []
    holding = None
    reduced = exit_required = invalidated = False
    state = "NO_QUALIFIED_HOLDING"
    peak = reference = None
    for row in daily.to_dict("records"):
        day, i = row["trade_date"], positions[row["trade_date"]]
        in_study = case["study_start"] <= day <= case["study_end"]
        # Future execution annotations affect state only at their observed next open.
        for action in pending:
            if (action.get("order_model_at") or "")[:10] != day:
                continue
            if action["action"] == "MA55_CLEAR_INTENT":
                if pd.notna(action["execution_price"]):
                    state = "EXITED_OBSERVATION_MODEL_ONLY"
                    holding["observation_exit_date"] = day
                    holding["observation_exit_price"] = action["execution_price"]
                else:
                    state = "EXIT_REQUIRED_EXECUTION_UNRESOLVED"
            else:
                state = "DEFENSIVE_OBSERVATION_REDUCTION_SIZE_UNKNOWN"
        data_bad = row["trend_qualification"] == "TREND_DATA_UNQUALIFIED" and row["indicator_status"] != "SUSPENDED"
        if holding and data_bad and not exit_required:
            invalidated = True
            state = "HOLDING_DATA_INVALIDATED_EXPOSURE_UNKNOWN"
            holding["data_invalidation_date"] = holding["data_invalidation_date"] or day
        if holding is None and in_study and row["trend_qualification"] == "TREND_ELIGIBLE":
            identity = f'{config["lifecycle"]["version"]}/{case["case_id"]}/C{row["confirmation_days"]}/{lane}/{day}'
            reference = peak = row["raw_close"]
            state = "QUALIFIED_OBSERVATION_NO_ENTRY_EVIDENCE"
            holding = dict(case_id=case["case_id"], security_id=row["security_id"], stock_name=case["stock_name"],
                holding_id=identity, confirmation_days=row["confirmation_days"], execution_lane=lane,
                association_start=day, observation_reference_close=reference, entry_evidence="ABSENT_NO_ENTRY_FILL",
                actual_entry_price=None, position_quantity=None, actual_max_floating_profit=None,
                actual_profit_giveback=None, actual_exit_price=None, ma28_intent_date=None, ma55_intent_date=None,
                observation_exit_date=None, observation_exit_price=None, first_reentry_candidate=None,
                reentry_delay_from_clear_intent=None, reentry_delay_from_observation_exit=None,
                data_invalidation_date=None, qualification_version=row["trend_qualification_version"],
                trend_config_sha256=row["trend_config_sha256"], lifecycle_version=config["lifecycle"]["version"],
                research_config_sha256=config_hash(config), source_snapshot_id=row["source_snapshot_id"],
                snapshot_manifest_sha256=row["snapshot_manifest_sha256"], initial_trend_status="TREND_ELIGIBLE")
        # Start-day high precedes the close reference: never claim that gain was held.
        if holding and day > holding["association_start"] and not exit_required and not invalidated and pd.notna(row["indicator_close"]):
            peak = max(peak, row["raw_high"])
        if holding and exit_required and row["trend_qualification"] == "TREND_ELIGIBLE" and not holding["first_reentry_candidate"]:
            holding["first_reentry_candidate"] = day
            holding["reentry_delay_from_clear_intent"] = i - positions[holding["ma55_intent_date"]]
            if holding["observation_exit_date"]:
                holding["reentry_delay_from_observation_exit"] = i - positions[holding["observation_exit_date"]]
        selected_action = None
        if in_study and holding and not invalidated and not exit_required:
            if row["clear_required"]:
                selected_action = "MA55_CLEAR_INTENT"
            elif row["reduce_required"] and not reduced:
                selected_action = "MA28_REDUCE_CAP_INTENT"
        for period, flag in ((28, "reduce_required"), (55, "clear_required")):
            if not in_study or not row[flag]:
                continue
            applicable = selected_action == ("MA28_REDUCE_CAP_INTENT" if period == 28 else "MA55_CLEAR_INTENT")
            reason = ("LINKED_OBSERVATION_INTENT_ONLY" if applicable else "NO_QUALIFIED_HOLDING" if not holding else
                "HOLDING_DATA_UNQUALIFIED" if invalidated else "AFTER_EXIT_INTENT_NO_REENTRY" if exit_required else
                "MA55_OVERRIDES_MA28" if period == 28 and row["clear_required"] else "MA28_INTENT_ALREADY_ISSUED")
            diagnostics.append(dict(case_id=case["case_id"], security_id=row["security_id"], trade_date=day,
                confirmation_days=row["confirmation_days"], execution_lane=lane, ma_period=period,
                raw_event=f"MA{period}_BREAK_CONFIRMED", holding_id=holding["holding_id"] if holding else None,
                holding_association_applicable=applicable, reason=reason, actual_sell_generated=False,
                trend_status_at_event=row["trend_qualification"], risk_rule_version=row["risk_rule_version"],
                source_snapshot_id=row["source_snapshot_id"], snapshot_manifest_sha256=row["snapshot_manifest_sha256"],
                research_config_sha256=config_hash(config)))
        if selected_action:
            clear = selected_action == "MA55_CLEAR_INTENT"
            holding["ma55_intent_date" if clear else "ma28_intent_date"] = day
            if clear:
                exit_required = True
                state = "EXIT_REQUIRED_INTENT_ONLY"
            else:
                reduced = True
                state = "DEFENSIVE_INTENT_SIZE_UNSPECIFIED"
            action = dict(case_id=case["case_id"], holding_id=holding["holding_id"], security_id=row["security_id"],
                stock_name=case["stock_name"], confirmation_days=row["confirmation_days"], execution_lane=lane,
                trade_date=day, decision_at=row["decision_at"], action=selected_action, target_position=0 if clear else None,
                ma28_position_cap=config["lifecycle"]["ma28_position_cap"], modelled_position_trade=False,
                actual_sell_generated=False, actual_realized_profit=None, entry_evidence="ABSENT_NO_ENTRY_FILL",
                peak_known_at_signal=peak, observation_reference_close=reference, price_metrics_only=True,
                lifecycle_version=config["lifecycle"]["version"], research_config_sha256=config_hash(config),
                source_snapshot_id=row["source_snapshot_id"], snapshot_manifest_sha256=row["snapshot_manifest_sha256"],
                risk_rule_version=row["risk_rule_version"], risk_config_sha256=row["risk_config_sha256"],
                **execution(source, i, row, lane))
            price = action["execution_price"]
            action["price_giveback_from_known_peak"] = price / peak - 1 if price is not None else None
            action["marked_gain_giveback_points"] = (peak-price) / reference if price is not None else None
            if price is not None:
                j = positions[action["execution_date"]]
                action.update(future_metrics(source, j, price, config["horizons"]))
            else:
                action.update({f"post_{h}_status": "NO_EXECUTION_ANCHOR" for h in config["horizons"]})
                action.update({"unexecuted_" + k: v for k, v in future_metrics(source, i, row["raw_close"], config["horizons"]).items()})
            actions.append(action)
            pending.append(action)
        output = {key: row[key] for key in ("security_id", "trade_date", "decision_at", "source_snapshot_id",
            "snapshot_manifest_sha256", "risk_rule_version", "risk_config_sha256", "confirmation_days",
            "trend_qualification", "trend_qualification_reason", "trend_qualification_version", "trend_config_sha256")}
        output.update(case_id=case["case_id"], execution_lane=lane, row_scope="STUDY" if in_study else "EVALUATION_ONLY",
            lifecycle_version=config["lifecycle"]["version"], research_config_sha256=config_hash(config),
            raw_risk_state=row["POSITION_RISK_STATE"], holding_id=holding["holding_id"] if holding else None,
            holding_state=state, action_intent=selected_action or "NONE", actual_sell_generated=False,
            ma28_position_cap=config["lifecycle"]["ma28_position_cap"], target_position=0 if exit_required else None,
            actual_position_quantity=None, actual_floating_profit=None, actual_profit_giveback=None,
            known_observation_peak=peak, marked_max_price_gain=peak/reference-1 if holding and not invalidated else None,
            exposure_status="UNKNOWN_NO_ENTRY_OR_SIZE_EVIDENCE", observation_path_status="INVALIDATED" if invalidated else "AVAILABLE",
            reentry_candidate=bool(holding and exit_required and row["trend_qualification"] == "TREND_ELIGIBLE"))
        rows.append(output)
    if holding:
        holding.update(terminal_state=state, observed_peak_price=peak, marked_max_price_gain=peak/reference-1 if not invalidated else None,
            actual_risk_exposure="UNKNOWN_NO_ENTRY_EVIDENCE", ma28_cap_status="UNSPECIFIED_NOT_FULL_POSITION")
    numeric = ("ma28_position_cap", "target_position", "actual_position_quantity", "actual_floating_profit",
               "actual_profit_giveback", "known_observation_peak", "marked_max_price_gain")
    result = pd.DataFrame(rows)
    if not result.empty:
        result[list(numeric)] = result[list(numeric)].astype(float)
        result["holding_id"] = result.holding_id.astype("string")
    return (result, pd.DataFrame(diagnostics), pd.DataFrame(actions), pd.DataFrame([holding] if holding else []))
