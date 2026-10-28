"""Shared result type for all data quality checks."""

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    flag: bool
    penalty: float  # 0-1, how much this check should reduce the trust score
    evidence: dict
    flagged_timestamps: list = field(default_factory=list)
