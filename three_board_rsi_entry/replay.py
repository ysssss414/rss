from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any, Iterable

import pandas as pd

from .config import StrategyConfig
from .models import (
    CANDIDATE_COLUMNS,
    CURRENT_STAGES,
    SIGNAL_COLUMNS,
    CycleState,
    ReplayResult,
)


def _number(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _make_bar_lookup(bars: pd.DataFrame) -> dict[tuple[date, str], dict[str, Any]]:
    required = {
        "trade_date",
        "ts_code",
        "open",
        "high",
        "low",
        "close",
        "amount",
        "suspended",
        "rsi14",
        "ma5",
    }
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"Indicator bars missing fields: {', '.join(missing)}")
    data = bars.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="raise").dt.date
    duplicate = data.duplicated(["trade_date", "ts_code"], keep=False)
    if duplicate.any():
        raise ValueError("Indicator bars contain duplicate trade_date/ts_code rows")
    return {
        (row.trade_date, row.ts_code): row._asdict()
        for row in data.itertuples(index=False)
    }


def _update_latest(cycle: CycleState, day: date, bar: dict[str, Any] | None) -> None:
    if not bar or bool(bar.get("suspended", False)):
        return
    close = _number(bar.get("close"))
    if close is None:
        return
    cycle.latest_trade_date = day
    cycle.latest_close = close
    cycle.latest_ma5 = _number(bar.get("ma5"))
    cycle.latest_rsi14 = _number(bar.get("rsi14"))


def _append_signal(
    signals: list[dict[str, Any]],
    *,
    cycle: CycleState,
    signal_type: str,
    day: date,
    observation_day: int,
    bar: dict[str, Any],
    previous_rsi: float | None,
    config: StrategyConfig,
    run_as_of: date,
) -> None:
    signals.append(
        {
            "signal_date": day,
            "candidate_cycle_id": cycle.candidate_cycle_id,
            "ts_code": cycle.ts_code,
            "stock_name": cycle.stock_name,
            "signal_type": signal_type,
            "qualified_date": cycle.qualified_date,
            "qualified_board_count": cycle.qualified_board_count,
            "last_limit_up_date": cycle.last_limit_up_date,
            "max_board_count": cycle.max_board_count,
            "observation_day": observation_day,
            "rsi14": _number(bar.get("rsi14")),
            "previous_rsi14": previous_rsi,
            "close": _number(bar.get("close")),
            "high": _number(bar.get("high")),
            "low": _number(bar.get("low")),
            "ma5": _number(bar.get("ma5")),
            "below70_trading_days": cycle.below70_trading_days,
            "below70_min_rsi": cycle.below70_min_rsi,
            "price_adjustment": config.price_adjustment,
            "run_as_of": run_as_of,
        }
    )


def _process_md1(
    cycle: CycleState,
    *,
    day: date,
    observation_day: int,
    bar: dict[str, Any],
    previous_rsi: float | None,
    config: StrategyConfig,
    run_as_of: date,
    signals: list[dict[str, Any]],
) -> None:
    rsi = _number(bar.get("rsi14"))
    low = _number(bar.get("low"))
    close = _number(bar.get("close"))
    ma5 = _number(bar.get("ma5"))

    if (
        cycle.first_ma5_touch_date is None
        and low is not None
        and ma5 is not None
        and low <= ma5
    ):
        cycle.first_ma5_touch_date = day
        recovered = close is not None and close >= ma5
        cycle.first_ma5_touch_close_recovered = recovered
        if cycle.md1_result == "NOT_REACHED":
            if rsi is None or rsi < config.candidate_rsi_threshold:
                cycle.md1_result = "LOST_RSI70_BEFORE_TOUCH"
            elif not recovered:
                cycle.md1_result = "FAILED_FIRST_TOUCH"
            else:
                cycle.md1_date = day
                cycle.md1_result = "TRIGGERED"
                _append_signal(
                    signals,
                    cycle=cycle,
                    signal_type="MD_1",
                    day=day,
                    observation_day=observation_day,
                    bar=bar,
                    previous_rsi=previous_rsi,
                    config=config,
                    run_as_of=run_as_of,
                )


def _process_md2(
    cycle: CycleState,
    *,
    day: date,
    observation_day: int,
    bar: dict[str, Any],
    previous_rsi: float | None,
    config: StrategyConfig,
    run_as_of: date,
    signals: list[dict[str, Any]],
) -> None:
    if cycle.md2_result != "NO_REBREAK":
        return
    rsi = _number(bar.get("rsi14"))
    if rsi is None or previous_rsi is None:
        return

    threshold = config.candidate_rsi_threshold
    if rsi < threshold:
        if not cycle.below70_active and previous_rsi >= threshold:
            cycle.below70_active = True
            cycle.below70_start_date = day
            cycle.below70_trading_days = 1
            cycle.below70_min_rsi = rsi
        elif cycle.below70_active:
            cycle.below70_trading_days += 1
            if cycle.below70_min_rsi is None:
                cycle.below70_min_rsi = rsi
            else:
                cycle.below70_min_rsi = min(cycle.below70_min_rsi, rsi)

        if cycle.below70_active:
            if rsi < config.md2_rsi_floor:
                cycle.md2_result = "RSI_BELOW_60"
                cycle.below70_active = False
            elif cycle.below70_trading_days > config.md2_max_below70_days:
                cycle.md2_result = "BELOW70_TIMEOUT"
                cycle.below70_active = False
        return

    if cycle.below70_active and previous_rsi < threshold:
        close = _number(bar.get("close"))
        ma5 = _number(bar.get("ma5"))
        duration_valid = (
            1
            <= cycle.below70_trading_days
            <= config.md2_max_below70_days
        )
        floor_valid = (
            cycle.below70_min_rsi is not None
            and cycle.below70_min_rsi >= config.md2_rsi_floor
        )
        if (
            duration_valid
            and floor_valid
            and close is not None
            and ma5 is not None
            and close >= ma5
        ):
            cycle.md2_date = day
            cycle.md2_result = "TRIGGERED"
            _append_signal(
                signals,
                cycle=cycle,
                signal_type="MD_2",
                day=day,
                observation_day=observation_day,
                bar=bar,
                previous_rsi=previous_rsi,
                config=config,
                run_as_of=run_as_of,
            )
        # A rebreak below MA5 is not a signal. A later, distinct continuous
        # below-70 adjustment may still qualify unless the cycle has hit a
        # permanent floor or timeout failure.
        cycle.below70_active = False


def _set_stage(
    cycle: CycleState, *, as_of_date: date, config: StrategyConfig
) -> None:
    if cycle.invalid_reason:
        cycle.current_stage = "INVALID"
    elif not cycle.qualified:
        cycle.current_stage = "NOT_QUALIFIED"
    elif as_of_date == cycle.last_limit_up_date:
        cycle.current_stage = "LIMIT_UP_ACTIVE"
    elif cycle.observation_days > config.candidate_max_observation_days:
        cycle.current_stage = "EXPIRED"
    else:
        md1_pending = cycle.md1_result == "NOT_REACHED"
        md2_pending = cycle.md2_result == "NO_REBREAK"
        if md1_pending and md2_pending:
            cycle.current_stage = "WAITING_BOTH"
        elif md1_pending:
            cycle.current_stage = "WAITING_MD1"
        elif md2_pending:
            cycle.current_stage = "WAITING_MD2"
        elif cycle.md1_date is not None or cycle.md2_date is not None:
            cycle.current_stage = "SIGNAL_TRIGGERED"
        else:
            cycle.current_stage = "EXPIRED"
    if cycle.current_stage not in CURRENT_STAGES:
        raise AssertionError(f"Unexpected current_stage: {cycle.current_stage}")


def run_replay(
    *,
    boards: pd.DataFrame,
    indicator_bars: pd.DataFrame,
    trading_days: Iterable[date],
    start_date: date,
    as_of_date: date,
    config: StrategyConfig,
    data_complete: bool = True,
) -> ReplayResult:
    """Recompute every cycle and signal by advancing one trading day at a time."""

    calendar = sorted(
        {
            value
            for value in trading_days
            if start_date <= value <= as_of_date
        }
    )
    calendar_position = {value: index for index, value in enumerate(calendar)}
    if not calendar:
        return ReplayResult(
            candidate_cycles=pd.DataFrame(columns=CANDIDATE_COLUMNS),
            signals=pd.DataFrame(columns=SIGNAL_COLUMNS),
        )

    board_data = boards.copy()
    if not board_data.empty:
        board_data["trade_date"] = pd.to_datetime(
            board_data["trade_date"], errors="raise"
        ).dt.date
        board_data = board_data[
            (board_data["trade_date"] >= start_date)
            & (board_data["trade_date"] <= as_of_date)
        ].sort_values(["trade_date", "ts_code"], kind="stable")
    boards_by_day = {
        day: frame for day, frame in board_data.groupby("trade_date", sort=False)
    }
    bar_lookup = _make_bar_lookup(indicator_bars)
    cycles: list[CycleState] = []
    last_cycle_by_code: dict[str, CycleState] = {}
    signals: list[dict[str, Any]] = []
    last_valid_rsi: dict[str, float] = {}

    for day in calendar:
        day_records = boards_by_day.get(day, pd.DataFrame())
        input_codes = set(day_records["ts_code"]) if not day_records.empty else set()

        # Input is applied first. Thus a four-board record on this date moves
        # last_limit_up_date before signal evaluation and suppresses a false
        # post-limit-up signal on the same date.
        for row in day_records.itertuples(index=False):
            prior = last_cycle_by_code.get(row.ts_code)
            day_index = calendar_position[day]
            prior_trading_day = calendar[day_index - 1] if day_index > 0 else None
            if prior is not None and prior.last_limit_up_date == prior_trading_day:
                expected = prior.max_board_count + 1
                if int(row.board_count) != expected:
                    raise ValueError(
                        "Adjacent trading-day board_count must increase by 1: "
                        f"{row.ts_code} {day} expected {expected}, "
                        f"got {row.board_count}"
                    )
                cycle = prior
                cycle.last_limit_up_date = day
                cycle.max_board_count = int(row.board_count)
                cycle.observation_days = 0
                cycle.cycle_expiry_date = None
                if getattr(row, "stock_name", ""):
                    cycle.stock_name = str(row.stock_name)
            else:
                cycle = CycleState(
                    candidate_cycle_id=f"{row.ts_code}_{day.strftime('%Y%m%d')}",
                    ts_code=row.ts_code,
                    stock_name=str(getattr(row, "stock_name", "") or ""),
                    sequence_start_date=day,
                    initial_board_count=int(row.board_count),
                    last_limit_up_date=day,
                    max_board_count=int(row.board_count),
                    data_complete=data_complete,
                )
                cycles.append(cycle)
                last_cycle_by_code[row.ts_code] = cycle

            bar = bar_lookup.get((day, row.ts_code))
            _update_latest(cycle, day, bar)
            rsi = _number(bar.get("rsi14")) if bar else None
            if (
                cycle.qualified_date is None
                and rsi is not None
                and rsi > config.candidate_rsi_threshold
            ):
                cycle.qualified_date = day
                cycle.qualified_board_count = int(row.board_count)

        for cycle in cycles:
            if cycle.sequence_start_date > day:
                continue
            bar = bar_lookup.get((day, cycle.ts_code))
            _update_latest(cycle, day, bar)
            rsi = _number(bar.get("rsi14")) if bar else None
            previous_rsi = last_valid_rsi.get(cycle.ts_code)

            # MD_1's high-RSI invariant begins at qualification and includes
            # later limit-up days, even though those days cannot signal.
            if (
                cycle.qualified
                and day >= cycle.qualified_date
                and cycle.md1_result == "NOT_REACHED"
                and rsi is not None
                and rsi < config.candidate_rsi_threshold
            ):
                cycle.md1_result = "LOST_RSI70_BEFORE_TOUCH"

            if (
                not cycle.qualified
                or day <= cycle.last_limit_up_date
                or cycle.ts_code in input_codes
            ):
                continue
            observation_day = (
                calendar_position[day] - calendar_position[cycle.last_limit_up_date]
            )
            cycle.observation_days = observation_day
            if observation_day == config.candidate_max_observation_days:
                cycle.cycle_expiry_date = day
            if observation_day > config.candidate_max_observation_days:
                if cycle.md1_result == "NOT_REACHED":
                    cycle.md1_result = "EXPIRED"
                if cycle.md2_result == "NO_REBREAK":
                    cycle.md2_result = "EXPIRED"
                continue
            if not bar or bool(bar.get("suspended", False)):
                continue

            _process_md1(
                cycle,
                day=day,
                observation_day=observation_day,
                bar=bar,
                previous_rsi=previous_rsi,
                config=config,
                run_as_of=as_of_date,
                signals=signals,
            )
            _process_md2(
                cycle,
                day=day,
                observation_day=observation_day,
                bar=bar,
                previous_rsi=previous_rsi,
                config=config,
                run_as_of=as_of_date,
                signals=signals,
            )

        # Previous RSI means the previous valid stock trading observation.
        for code in {cycle.ts_code for cycle in cycles}:
            bar = bar_lookup.get((day, code))
            if bar and not bool(bar.get("suspended", False)):
                rsi = _number(bar.get("rsi14"))
                if rsi is not None:
                    last_valid_rsi[code] = rsi

    for cycle in cycles:
        _set_stage(cycle, as_of_date=as_of_date, config=config)

    candidate_frame = pd.DataFrame(
        [cycle.as_row() for cycle in cycles], columns=CANDIDATE_COLUMNS
    )
    if not candidate_frame.empty:
        candidate_frame = candidate_frame.sort_values(
            ["sequence_start_date", "ts_code", "candidate_cycle_id"], kind="stable"
        ).reset_index(drop=True)
    signal_frame = pd.DataFrame(signals, columns=SIGNAL_COLUMNS)
    if not signal_frame.empty:
        signal_frame = signal_frame.sort_values(
            ["signal_date", "candidate_cycle_id", "signal_type"], kind="stable"
        ).reset_index(drop=True)
    return ReplayResult(candidate_cycles=candidate_frame, signals=signal_frame)
