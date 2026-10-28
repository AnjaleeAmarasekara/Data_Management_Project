"""Event detection on quality-filtered minute-level data.

Two complementary detectors:
- Setpoint excursions: rule-based, minutes above the unit's max safe temperature. These
  are the events that actually put stock at risk.
- Isolation Forest (Liu et al., 2008 -- proposal ref [5]): unsupervised, multivariate,
  catches unusual behaviour (odd temp/humidity combinations, sudden swings) that never
  crosses the safe limit -- an early-warning signal rather than a confirmed excursion.
"""

from dataclasses import dataclass

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

MERGE_GAP_MIN = 2  # flagged minutes closer together than this belong to one event
IF_CONTAMINATION = 0.005


@dataclass
class Event:
    kind: str  # 'excursion' | 'isolation_forest'
    start_ts: pd.Timestamp
    end_ts: pd.Timestamp
    minutes: int
    peak_temp_c: float
    severity: str
    avg_trust: float


def _merge_minutes(df: pd.DataFrame, mask: pd.Series):
    """Group flagged minutes into events, merging flagged minutes at most MERGE_GAP_MIN apart."""
    flagged = df[mask]
    if flagged.empty:
        return
    gap = flagged["ts"].diff() > pd.Timedelta(minutes=MERGE_GAP_MIN)
    for _, group in flagged.groupby(gap.cumsum()):
        yield group


def detect_excursions(df: pd.DataFrame, max_safe_c: float) -> list[Event]:
    events = []
    for group in _merge_minutes(df, df["cold_temp"] > max_safe_c):
        peak = float(group["cold_temp"].max())
        events.append(
            Event(
                kind="excursion",
                start_ts=group["ts"].iloc[0],
                end_ts=group["ts"].iloc[-1],
                minutes=len(group),
                peak_temp_c=round(peak, 2),
                severity="critical" if peak >= max_safe_c + 3 else "warning",
                avg_trust=round(float(group["trust"].mean()), 2),
            )
        )
    return events


def detect_isolation_forest(df: pd.DataFrame, max_safe_c: float) -> list[Event]:
    features = pd.DataFrame(
        {
            "cold_temp": df["cold_temp"],
            "humidity": df["humidity"].fillna(df["humidity"].median()),
            "temp_change": df["cold_temp"].diff().fillna(0.0),
            "temp_std_5m": df["cold_temp"].rolling(5, min_periods=1).std().fillna(0.0),
        }
    )
    scaled = StandardScaler().fit_transform(features)
    model = IsolationForest(n_estimators=200, contamination=IF_CONTAMINATION, random_state=42)
    is_anomaly = pd.Series(model.fit_predict(scaled) == -1, index=df.index)

    events = []
    for group in _merge_minutes(df, is_anomaly):
        peak = float(group["cold_temp"].max())
        if peak > max_safe_c:
            continue  # already reported as a confirmed excursion
        events.append(
            Event(
                kind="isolation_forest",
                start_ts=group["ts"].iloc[0],
                end_ts=group["ts"].iloc[-1],
                minutes=len(group),
                peak_temp_c=round(peak, 2),
                severity="watch",
                avg_trust=round(float(group["trust"].mean()), 2),
            )
        )
    return events
