"""Short-horizon trend forecast: will this unit cross its safe limit soon?

Linear regression over the most recent minutes. The proposal cites ARIMA (Box & Jenkins)
for this task; a linear trend is the deliberately simple first version for this phase,
with ARIMA the planned upgrade for the analytics milestone.
"""

import numpy as np
import pandas as pd


def forecast_trend(series: pd.Series, max_safe_c: float, lookback_min: int = 30, horizon_min: int = 30) -> dict:
    """series: minute-level cold_temp indexed by time, most recent last."""
    recent = series.dropna().iloc[-lookback_min:]
    if len(recent) < 8:
        return {"status": "insufficient_data"}

    x = np.arange(len(recent), dtype=float)
    slope, intercept = np.polyfit(x, recent.values, 1)
    fitted = slope * x + intercept
    ss_res = float(((recent.values - fitted) ** 2).sum())
    ss_tot = float(((recent.values - recent.values.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    current = float(fitted[-1])
    predicted = float(slope * (len(recent) - 1 + horizon_min) + intercept)

    if current > max_safe_c:
        minutes_to_limit = 0.0
    elif slope > 0:
        minutes_to_limit = float((max_safe_c - current) / slope)
    else:
        minutes_to_limit = None

    at_risk = minutes_to_limit is not None and minutes_to_limit <= horizon_min
    return {
        "status": "at_risk" if at_risk else "stable",
        "slope_c_per_min": round(float(slope), 4),
        "current_c": round(current, 2),
        "predicted_c": round(predicted, 2),
        "horizon_min": horizon_min,
        "minutes_to_limit": None if minutes_to_limit is None else round(minutes_to_limit, 1),
        "fit_r2": round(r2, 3),
    }
