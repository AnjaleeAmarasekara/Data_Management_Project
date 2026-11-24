"""Event detection on quality-filtered minute-level data.

Two complementary detectors:
- Setpoint excursions: rule-based, time spent outside the unit's safe band. Both
  directions matter: too warm spoils stock, too cold freezes and ruins chilled goods.
  These are the events that actually put stock at risk.
- Isolation Forest (Liu et al., 2008 -- proposal ref [5]): unsupervised, multivariate,
  catches unusual behaviour (odd temp/humidity combinations, sudden swings) that never
  crosses the safe limit -- an early-warning signal rather than a confirmed excursion.
"""

from dataclasses import dataclass

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from analytics.config import STEP_MINUTES

MERGE_GAP_STEPS = 2  # flagged samples this close together belong to one event
MERGE_GAP_MIN = MERGE_GAP_STEPS * STEP_MINUTES
VOLATILITY_STEPS = 6  # rolling spread over half an hour
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
                minutes=len(group) * STEP_MINUTES,
                peak_temp_c=round(peak, 2),
                severity="critical" if peak >= max_safe_c + 3 else "warning",
                avg_trust=round(float(group["trust"].mean()), 2),
            )
        )
    return events


def detect_cold_excursions(df: pd.DataFrame, min_safe_c: float | None) -> list[Event]:
    """Stretches where a chilled unit ran *below* its lower bound.

    Freezing is a real cold-chain failure, not a harmless over-performance: dairy
    separates, produce suffers chilling injury, eggs crack. Watching only the upper limit
    reports a unit sitting at -2 C with dairy in it as "Normal", which is wrong in a way
    that is visible on the dashboard.
    """
    if min_safe_c is None:
        return []

    events = []
    for group in _merge_minutes(df, df["cold_temp"] < min_safe_c):
        trough = float(group["cold_temp"].min())
        events.append(
            Event(
                kind="cold_excursion",
                start_ts=group["ts"].iloc[0],
                end_ts=group["ts"].iloc[-1],
                minutes=len(group) * STEP_MINUTES,
                peak_temp_c=round(trough, 2),  # the extreme reached, here the coldest
                severity="critical" if trough <= min_safe_c - 3 else "warning",
                avg_trust=round(float(group["trust"].mean()), 2),
            )
        )
    return events


def detect_silences(raw: pd.DataFrame, observed_until: pd.Timestamp, threshold_min: float) -> list[Event]:
    """Stretches where the sensor sent nothing for longer than threshold_min.

    raw must be the *unfiltered* readings: readings the quality layer excluded were still
    received, so dropping them first would invent gaps that never happened. observed_until
    is the newest reading from the same premises, so a sensor that died before its siblings
    stopped shows a silence running to the end rather than simply a shorter record.
    The temperature reported is the last one received before the silence - the most recent
    thing known about the stock.
    """
    if raw.empty:
        return []
    ts = raw["ts"].reset_index(drop=True)
    temps = raw["temp"].reset_index(drop=True)
    starts = list(ts)
    ends = list(ts.iloc[1:]) + [observed_until]
    events = []
    for i, (a, b) in enumerate(zip(starts, ends)):
        gap_min = (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 60
        if gap_min > threshold_min:
            events.append(
                Event(
                    kind="sensor_silent",
                    start_ts=pd.Timestamp(a),
                    end_ts=pd.Timestamp(b),
                    minutes=int(round(gap_min)),
                    peak_temp_c=round(float(temps.iloc[i]), 2),
                    severity="critical",
                    avg_trust=0.0,
                )
            )
    return events


def isolation_forest_flags(df: pd.DataFrame, contamination: float = IF_CONTAMINATION) -> pd.Series:
    """The raw per-minute anomaly flags, before merging into events or excluding
    confirmed excursions. Factored out so analytics/evaluation/evaluate_anomaly.py can
    run the exact same production detection code against data with known synthetic
    anomalies, rather than a re-implementation that could silently drift from reality."""
    features = pd.DataFrame(
        {
            "cold_temp": df["cold_temp"],
            "humidity": df["humidity"].fillna(df["humidity"].median()),
            "temp_change": df["cold_temp"].diff().fillna(0.0),
            "temp_std_30m": df["cold_temp"].rolling(VOLATILITY_STEPS, min_periods=1).std().fillna(0.0),
        }
    )
    scaled = StandardScaler().fit_transform(features)
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=42)
    return pd.Series(model.fit_predict(scaled) == -1, index=df.index)


def detect_isolation_forest(df: pd.DataFrame, max_safe_c: float, min_safe_c: float | None = None) -> list[Event]:
    is_anomaly = isolation_forest_flags(df)

    events = []
    for group in _merge_minutes(df, is_anomaly):
        peak = float(group["cold_temp"].max())
        trough = float(group["cold_temp"].min())
        # Already reported as a confirmed excursion in one direction or the other; a second
        # "early warning" for the same minutes would double-count it on the alerts page.
        if peak > max_safe_c or (min_safe_c is not None and trough < min_safe_c):
            continue
        events.append(
            Event(
                kind="isolation_forest",
                start_ts=group["ts"].iloc[0],
                end_ts=group["ts"].iloc[-1],
                minutes=len(group) * STEP_MINUTES,
                peak_temp_c=round(peak, 2),
                severity="watch",
                avg_trust=round(float(group["trust"].mean()), 2),
            )
        )
    return events
