from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StrategyConfig:
    rsi_period: int = 14
    candidate_rsi_threshold: float = 70.0
    md2_rsi_floor: float = 60.0
    md2_max_below70_days: int = 9
    candidate_max_observation_days: int = 20
    warmup_trading_days: int = 150
    price_adjustment: str = "qfq"

    def __post_init__(self) -> None:
        if self.rsi_period < 1:
            raise ValueError("rsi_period must be at least 1")
        if self.md2_rsi_floor >= self.candidate_rsi_threshold:
            raise ValueError("md2_rsi_floor must be below candidate_rsi_threshold")
        if self.md2_max_below70_days < 1:
            raise ValueError("md2_max_below70_days must be at least 1")
        if self.candidate_max_observation_days < 1:
            raise ValueError("candidate_max_observation_days must be at least 1")
        if self.warmup_trading_days < 1:
            raise ValueError("warmup_trading_days must be at least 1")
        if self.price_adjustment not in {"qfq", "none"}:
            raise ValueError("price_adjustment must be 'qfq' or 'none'")

    @classmethod
    def from_file(cls, path: Path | str | None) -> "StrategyConfig":
        if path is None:
            return cls()
        config_path = Path(path)
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Config file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Config file is not valid JSON: {config_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Config root must be a JSON object")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown config fields: {', '.join(unknown)}")
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
