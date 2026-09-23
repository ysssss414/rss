"""Gross forward price-path diagnostics; no exit rule or strategy PnL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from random import Random
from statistics import median
from typing import Mapping, Sequence

from .entry_signals import ENTRY_A, ENTRY_B, EntrySignal
from .foundation import ExecutionResult, TradingCalendar


HORIZONS = (1, 3, 5, 7, 10, 20)
SHANGHAI = timezone(timedelta(hours=8))
MFE_THRESHOLDS = (Decimal("0.03"), Decimal("0.05"), Decimal("0.10"))
MAE_THRESHOLDS = (Decimal("-0.03"), Decimal("-0.05"), Decimal("-0.10"))


def _price(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


@dataclass(frozen=True)
class PriceBar:
    trade_date: date
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    status: str = "TRADING"
    adjustment_factor: Decimal | None = None
    factor_available_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {"TRADING", "SUSPENDED", "INVALID"}:
            raise ValueError("Unknown bar status")
        if self.status == "TRADING" and (not all(_price(x) for x in (self.high, self.low, self.close))
                                         or not self.low <= self.close <= self.high):
            raise ValueError("Invalid raw high/low/close")
        if self.status != "TRADING" and any(x is not None for x in (self.high, self.low, self.close)):
            raise ValueError("Non-trading session cannot carry invented prices")


@dataclass(frozen=True)
class PricePath:
    security_id: str
    data_snapshot_id: str
    bars: tuple[PriceBar, ...]
    corporate_action_dates: tuple[date, ...] = ()
    lifecycle_termination_dates: tuple[date, ...] = ()
    factor_feed_qualified: bool = False
    factor_source_hash: str | None = None
    entry_adjustment_factor: Decimal | None = None
    entry_factor_available_at: datetime | None = None

    def __post_init__(self) -> None:
        dates = tuple(bar.trade_date for bar in self.bars)
        if not self.security_id or not self.data_snapshot_id or dates != tuple(sorted(set(dates))):
            raise ValueError("Path identity or bar order is invalid")
        for events in (self.corporate_action_dates, self.lifecycle_termination_dates):
            if events != tuple(sorted(set(events))):
                raise ValueError("Path event dates must be sorted and unique")


@dataclass(frozen=True)
class StudyEvent:
    event_id: str
    security_id: str
    signal_date: date
    entry_date: date | None
    entry_price: Decimal
    labels: tuple[str, ...]
    data_snapshot_id: str
    contract_versions: tuple[str, ...]
    observation_instance_id: str | None = None
    signal_id: str | None = None
    execution_result: str | None = None
    price_reference_date: date | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.event_id or not self.security_id or not self.data_snapshot_id
                or not self.labels or not self.contract_versions or not _price(self.entry_price)
                or self.price_reference_date is not None and self.entry_date is not None
                and self.price_reference_date > self.entry_date):
            raise ValueError("Invalid event identity or price")


@dataclass(frozen=True)
class HorizonRecord:
    horizon: int
    mfe: Decimal | None
    mae: Decimal | None
    ret: Decimal | None
    time_to_mfe: int | None
    observed_bar_count: int
    expected_session_count: int
    coverage_ratio: Decimal
    coverage_status: str
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class EventStudyRecord:
    event: StudyEvent
    horizons: tuple[HorizonRecord, ...]
    event_path_status: str
    exclusion_reason: str | None


class ForwardEventStudy:
    """Measure exchange-session windows from D0, including the entry session."""

    def __init__(self, calendar: TradingCalendar, horizons: tuple[int, ...] = HORIZONS):
        if (not horizons or tuple(sorted(set(horizons))) != horizons
                or any(type(h) is not int or h < 1 for h in horizons)):
            raise ValueError("Horizons must be positive, unique and sorted")
        self.calendar = calendar
        self.horizons = horizons

    def evaluate(self, event: StudyEvent, path: PricePath) -> EventStudyRecord:
        if event.security_id != path.security_id or event.data_snapshot_id != path.data_snapshot_id:
            raise ValueError("Event and path provenance disagree")
        if event.entry_date is not None and event.entry_date <= event.signal_date:
            # Signal diagnostic starts T+1; modeled fill is also T+1 or later.
            raise ValueError("Forward event clock cannot start before or on signal day")
        if event.entry_date is not None and event.entry_date not in self.calendar.sessions:
            raise ValueError("D0 is absent from the qualified exchange calendar")
        bar_by_date = {bar.trade_date: bar for bar in path.bars}
        start = self.calendar.sessions.index(event.entry_date) if event.entry_date else None
        rows = tuple(self._horizon(event, path, bar_by_date, start, h) for h in self.horizons)
        excluded_row = next((row for row in rows if row.exclusion_reason), None)
        excluded = excluded_row.exclusion_reason if excluded_row else None
        status = (excluded_row.coverage_status if excluded_row else
                  "PARTIAL_COVERAGE" if any(row.coverage_status != "COMPLETE" for row in rows)
                  else "COMPARABLE")
        return EventStudyRecord(event, rows, status, excluded)

    def _horizon(self, event: StudyEvent, path: PricePath, bars: Mapping[date, PriceBar],
                 start: int | None, h: int) -> HorizonRecord:
        if start is None or start + h > len(self.calendar.sessions):
            return HorizonRecord(h, None, None, None, None, 0, h, Decimal(0),
                                 "PATH_UNRESOLVED", "INSUFFICIENT_CALENDAR")
        dates = self.calendar.sessions[start:start + h]
        if any(dates[0] <= day <= dates[-1] for day in path.lifecycle_termination_dates):
            return HorizonRecord(h, None, None, None, None, 0, h, Decimal(0),
                                 "PATH_UNRESOLVED", "LIFECYCLE_TERMINATION")
        observed = [(i, bars[day]) for i, day in enumerate(dates, 1)
                    if day in bars and bars[day].status == "TRADING"]
        coverage = Decimal(len(observed)) / Decimal(h)
        reference_date = event.price_reference_date or event.entry_date
        action = any(reference_date < day <= dates[-1] for day in path.corporate_action_dates)
        if action and not self._factors_qualified(path, reference_date, observed):
            return HorizonRecord(h, None, None, None, None, len(observed), h, coverage,
                                 "CORPORATE_ACTION_UNRESOLVED", "UNQUALIFIED_COMPARABLE_PRICE")
        scale = path.entry_adjustment_factor if action else Decimal(1)
        highs = [(i, bar.high * (bar.adjustment_factor / scale if action else 1))
                 for i, bar in observed]
        lows = [bar.low * (bar.adjustment_factor / scale if action else 1)
                for _, bar in observed]
        if highs:
            top = max(value for _, value in highs)
            mfe = top / event.entry_price - 1
            mae = min(lows) / event.entry_price - 1
            first_top = next(i for i, value in highs if value == top)
        else:
            mfe = mae = first_top = None
        endpoint = bars.get(dates[-1])
        ret = None
        if endpoint is not None and endpoint.status == "TRADING":
            close = endpoint.close * (endpoint.adjustment_factor / scale if action else 1)
            ret = close / event.entry_price - 1
        status = "COMPLETE" if len(observed) == h else "PARTIAL" if observed else "NO_BARS"
        return HorizonRecord(h, mfe, mae, ret, first_top, len(observed), h, coverage, status)

    @staticmethod
    def _factors_qualified(path: PricePath, entry_date: date,
                           observed: Sequence[tuple[int, PriceBar]]) -> bool:
        if (not path.factor_feed_qualified or not path.factor_source_hash
                or len(path.factor_source_hash) != 64
                or any(char not in "0123456789abcdef" for char in path.factor_source_hash)
                or not _price(path.entry_adjustment_factor)
                or path.entry_factor_available_at is None
                or path.entry_factor_available_at.utcoffset() is None
                or path.entry_factor_available_at > datetime.combine(entry_date, time(23, 59, 59), SHANGHAI)):
            return False
        return all(_price(bar.adjustment_factor)
                   and bar.factor_available_at is not None
                   and bar.factor_available_at.utcoffset() is not None
                   and bar.factor_available_at <= datetime.combine(bar.trade_date, time(23, 59, 59), SHANGHAI)
                   for _, bar in observed)


@dataclass(frozen=True)
class SignalAttempt:
    signal_id: str
    security_id: str
    signal: EntrySignal
    execution: ExecutionResult
    signal_raw_close: Decimal
    limit_up_count_at_signal: int
    rsi_at_signal: Decimal
    pool_session_index: int

    def __post_init__(self) -> None:
        if (not self.signal_id or not self.security_id or not _price(self.signal_raw_close)
                or self.limit_up_count_at_signal not in {4, 5}
                or not isinstance(self.rsi_at_signal, Decimal)
                or not Decimal(70) < self.rsi_at_signal <= Decimal(100)
                or self.pool_session_index not in range(1, 8)
                or self.signal.execution_policy_id != "NEXT_SESSION_V1"
                or self.execution.policy_id != "NEXT_SESSION_V1"
                or self.execution.status not in {"MODELLED_FILL", "NO_FILL", "EXECUTION_UNRESOLVED"}):
            raise ValueError("Invalid frozen Entry attempt")
        if self.execution.status == "MODELLED_FILL" and (
                self.execution.execution_date is None or not _price(self.execution.execution_price)
                or self.execution.execution_date <= self.signal.signal_date):
            raise ValueError("A modeled fill requires a forward raw open")


@dataclass(frozen=True)
class Cohorts:
    attempts: tuple[SignalAttempt, ...]
    signal_events: tuple[StudyEvent, ...]
    primary_filled_events: tuple[StudyEvent, ...]
    signal_count: int
    fill_count: int
    no_fill_count: int
    unresolved_count: int
    fill_rate: Decimal | None


def build_cohorts(attempts: Sequence[SignalAttempt], calendar: TradingCalendar) -> Cohorts:
    """Keep every attempt; select only first modeled fill per pool for primary study."""
    ordered = tuple(sorted(attempts, key=lambda a: (a.signal.signal_date, a.signal_id)))
    if len({a.signal_id for a in ordered}) != len(ordered):
        raise ValueError("Duplicate signal id")
    signal_events = []
    primary = []
    seen_instances = set()
    for attempt in ordered:
        signal = attempt.signal
        labels = signal.signal_reasons
        if not labels or any(label not in {ENTRY_A, ENTRY_B} for label in labels):
            raise ValueError("Only frozen Entry A/B signal labels are admitted")
        metadata = {
            "execution_intent_type": signal.execution_intent_type,
            "execution_fidelity_gap": signal.execution_fidelity_gap,
            "limit_up_count_at_signal": attempt.limit_up_count_at_signal,
            "rsi_at_signal": attempt.rsi_at_signal,
            "pool_session_index": attempt.pool_session_index,
            "price_lineage": "RAW_TRADING_PRICE",
        }
        common = dict(security_id=attempt.security_id, signal_date=signal.signal_date,
                      labels=labels, data_snapshot_id=signal.data_snapshot_id,
                      contract_versions=signal.entry_contract_versions,
                      observation_instance_id=signal.observation_instance_id,
                      signal_id=attempt.signal_id, execution_result=attempt.execution.status,
                      metadata=metadata)
        signal_events.append(StudyEvent(
            event_id=f"SIGNAL:{attempt.signal_id}", entry_date=calendar.next_after(signal.signal_date),
            entry_price=attempt.signal_raw_close, price_reference_date=signal.signal_date, **common))
        if (attempt.execution.status == "MODELLED_FILL"
                and (attempt.security_id, signal.observation_instance_id) not in seen_instances):
            primary.append(StudyEvent(
                event_id=f"FILL:{attempt.signal_id}", entry_date=attempt.execution.execution_date,
                entry_price=attempt.execution.execution_price,
                price_reference_date=attempt.execution.execution_date, **common))
            seen_instances.add((attempt.security_id, signal.observation_instance_id))
    counts = {status: sum(a.execution.status == status for a in ordered)
              for status in ("MODELLED_FILL", "NO_FILL", "EXECUTION_UNRESOLVED")}
    executable = counts["MODELLED_FILL"] + counts["NO_FILL"]
    return Cohorts(ordered, tuple(signal_events), tuple(primary), len(ordered),
                   counts["MODELLED_FILL"], counts["NO_FILL"],
                   counts["EXECUTION_UNRESOLVED"],
                   Decimal(counts["MODELLED_FILL"]) / executable if executable else None)


def _quantile(values: Sequence[Decimal], probability: Decimal) -> Decimal:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    fraction = position - lower
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * fraction


def bootstrap_mean_ci(values: Sequence[Decimal], *, seed: int = 20260923,
                      draws: int = 1000) -> tuple[Decimal, Decimal] | None:
    if not values:
        return None
    if draws < 100:
        raise ValueError("Bootstrap needs at least 100 draws")
    rng = Random(seed)
    means = [sum((values[rng.randrange(len(values))] for _ in values), Decimal(0)) / len(values)
             for _ in range(draws)]
    return _quantile(means, Decimal("0.025")), _quantile(means, Decimal("0.975"))


def distribution(values: Sequence[Decimal]) -> dict[str, object]:
    if not values:
        return {"N": 0, "mean": None, "median": None, "P25": None, "P75": None}
    return {"N": len(values), "mean": sum(values, Decimal(0)) / len(values),
            "median": median(values), "P25": _quantile(values, Decimal("0.25")),
            "P75": _quantile(values, Decimal("0.75"))}


def summarize(records: Sequence[EventStudyRecord], horizons: tuple[int, ...] = HORIZONS) -> dict[str, object]:
    """Aggregate comparable metrics per horizon, retaining missing denominators."""
    securities = {record.event.security_id for record in records}
    per_security = [sum(record.event.security_id == security for record in records)
                    for security in sorted(securities)]
    output: dict[str, object] = {
        "event_count": len(records), "unique_security_count": len(securities),
        "events_per_security": distribution([Decimal(n) for n in per_security]),
        "path_status_counts": {status: sum(r.event_path_status == status for r in records)
                               for status in sorted({r.event_path_status for r in records})},
        "horizons": {},
    }
    for h in horizons:
        rows = [next(row for row in record.horizons if row.horizon == h) for record in records]
        mfe = [row.mfe for row in rows if row.mfe is not None]
        mae = [row.mae for row in rows if row.mae is not None]
        ret = [row.ret for row in rows if row.ret is not None]
        times = [Decimal(row.time_to_mfe) for row in rows if row.time_to_mfe is not None]
        output["horizons"][h] = {
            "event_N": len(rows), "mfe": distribution(mfe), "mae": distribution(mae),
            "ret": distribution(ret), "mean_mfe_ci95": bootstrap_mean_ci(mfe),
            "mean_ret_ci95": bootstrap_mean_ci(ret),
            "mfe_hit_rates": {str(t): Decimal(sum(x >= t for x in mfe)) / len(mfe) if mfe else None
                              for t in MFE_THRESHOLDS},
            "mae_hit_rates": {str(t): Decimal(sum(x <= t for x in mae)) / len(mae) if mae else None
                              for t in MAE_THRESHOLDS},
            "ret_hit_rates": {rule: Decimal(sum(test(x) for x in ret)) / len(ret) if ret else None
                              for rule, test in (("positive", lambda x: x > 0),
                                                 ("at_least_3pct", lambda x: x >= Decimal("0.03")),
                                                 ("at_most_minus_3pct", lambda x: x <= Decimal("-0.03")))},
            "median_time_to_mfe": median(times) if times else None,
            "coverage_status_counts": {status: sum(row.coverage_status == status for row in rows)
                                       for status in sorted({row.coverage_status for row in rows})},
            "endpoint_unavailable_N": sum(row.ret is None for row in rows),
            "excluded_N": sum(row.exclusion_reason is not None for row in rows),
        }
    return output


def entry_groups(records: Sequence[EventStudyRecord]) -> dict[str, tuple[EventStudyRecord, ...]]:
    """Entry-specific descriptive slices live above the generic metric engine."""
    groups: dict[str, list[EventStudyRecord]] = {key: [] for key in (
        "ALL", "ENTRY_A", "ENTRY_B", "A_ONLY", "B_ONLY", "A_AND_B",
        "LIMIT_UP_COUNT_4", "LIMIT_UP_COUNT_5", "RSI_70_75", "RSI_75_80", "RSI_GT_80",
        *(f"POOL_SESSION_{i}" for i in range(1, 8)),
        "NORMAL_CLOSE_INTENT", "LIMIT_UP_CLOSE_BOARD_INTENT")}
    for record in records:
        event = record.event
        meta = event.metadata
        labels = set(event.labels)
        keys = ["ALL"]
        if ENTRY_A in labels:
            keys.append("ENTRY_A")
        if ENTRY_B in labels:
            keys.append("ENTRY_B")
        keys.append("A_AND_B" if len(labels) == 2 else "A_ONLY" if ENTRY_A in labels else "B_ONLY")
        count = meta.get("limit_up_count_at_signal")
        if count in {4, 5}:
            keys.append(f"LIMIT_UP_COUNT_{count}")
        rsi = meta.get("rsi_at_signal")
        if isinstance(rsi, Decimal):
            keys.append("RSI_70_75" if rsi <= 75 else "RSI_75_80" if rsi <= 80 else "RSI_GT_80")
        day = meta.get("pool_session_index")
        if type(day) is int and day in range(1, 8):
            keys.append(f"POOL_SESSION_{day}")
        intent = meta.get("execution_intent_type")
        if intent in {"NORMAL_CLOSE_INTENT", "LIMIT_UP_CLOSE_BOARD_INTENT"}:
            keys.append(intent)
        for key in keys:
            groups[key].append(record)
    return {key: tuple(value) for key, value in groups.items()}


def fillability_by_intent(attempts: Sequence[SignalAttempt]) -> dict[str, dict[str, object]]:
    """Keep board-intent failures visible; unresolved is not an executable attempt."""
    output = {}
    for intent in ("NORMAL_CLOSE_INTENT", "LIMIT_UP_CLOSE_BOARD_INTENT"):
        selected = [a for a in attempts if a.signal.execution_intent_type == intent]
        fill = sum(a.execution.status == "MODELLED_FILL" for a in selected)
        no_fill = sum(a.execution.status == "NO_FILL" for a in selected)
        unresolved = sum(a.execution.status == "EXECUTION_UNRESOLVED" for a in selected)
        output[intent] = {
            "signal_N": len(selected), "fill_N": fill, "no_fill_N": no_fill,
            "unresolved_N": unresolved, "executable_attempt_N": fill + no_fill,
            "fill_rate": Decimal(fill) / (fill + no_fill) if fill + no_fill else None,
            "execution_fidelity_gap": intent == "LIMIT_UP_CLOSE_BOARD_INTENT",
        }
    return output


def selection_bias_diagnostic(signal_records: Sequence[EventStudyRecord],
                              horizons: tuple[int, ...] = HORIZONS) -> dict[int, dict[str, object]]:
    """Compare both attempt statuses at the same T-close signal reference basis."""
    output = {}
    for h in horizons:
        values = {}
        for status in ("MODELLED_FILL", "NO_FILL"):
            values[status] = [next(row.ret for row in record.horizons if row.horizon == h)
                              for record in signal_records if record.event.execution_result == status]
            values[status] = [value for value in values[status] if value is not None]
        fill, no_fill = values["MODELLED_FILL"], values["NO_FILL"]
        if fill and no_fill:
            rng = Random(20260923 + h)
            differences = [sum((no_fill[rng.randrange(len(no_fill))] for _ in no_fill), Decimal(0)) / len(no_fill)
                           - sum((fill[rng.randrange(len(fill))] for _ in fill), Decimal(0)) / len(fill)
                           for _ in range(1000)]
            ci = (_quantile(differences, Decimal("0.025")),
                  _quantile(differences, Decimal("0.975")))
            flag = "EXECUTION_SELECTION_BIAS" if ci[0] > 0 else "NOT_ESTABLISHED"
        else:
            ci, flag = None, "INSUFFICIENT_COMPARABLE_EVENTS"
        output[h] = {"fill_N": len(fill), "no_fill_N": len(no_fill),
                     "fill_mean_ret": distribution(fill)["mean"],
                     "no_fill_mean_ret": distribution(no_fill)["mean"],
                     "no_fill_minus_fill_ci95": ci, "status": flag,
                     "basis": "SIGNAL_T_RAW_CLOSE_TO_T_PLUS_1_D0"}
    return output
