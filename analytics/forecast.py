"""Short-horizon trend forecast: will this unit cross its safe limit soon?

Linear regression over the most recent samples. The proposal cites ARIMA (Box & Jenkins)
for this task; this linear trend is the deliberately simple baseline and the fallback
used whenever ARIMA cannot be fitted (see analytics/arima.py).

Horizons are counted in samples, not minutes: the sensors report every STEP_MINUTES
minutes, so a 30-minute horizon is six steps ahead, and the result converts back to
minutes for display.
"""

import numpy as np
import pandas as pd

from analytics.config import FORECAST_HORIZON_STEPS, FORECAST_LOOKBACK_STEPS, STEP_MINUTES

MIN_POINTS = 8


def forecast_trend(
    series: pd.Series,
    max_safe_c: float,
    lookback_steps: int = FORECAST_LOOKBACK_STEPS,
    horizon_steps: int = FORECAST_HORIZON_STEPS,
    min_safe_c: float | None = None,
) -> dict:
    """series: cold_temp on the sensor's sampling grid, most recent last.

    Watches both ends of the safe band. "direction" says which limit is threatened, so the
    dashboard can say "heading towards freezing" rather than reusing warm-side wording."""
    recent = series.dropna().iloc[-lookback_steps:]
    horizon_min = horizon_steps * STEP_MINUTES
    if len(recent) < MIN_POINTS:
        return {"status": "insufficient_data", "horizon_min": horizon_min}

    x = np.arange(len(recent), dtype=float)
    slope, intercept = np.polyfit(x, recent.values, 1)
    fitted = slope * x + intercept
    ss_res = float(((recent.values - fitted) ** 2).sum())
    ss_tot = float(((recent.values - recent.values.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Whether the unit is outside its band *now* is a question about the latest reading, not
    # about where the fitted line ends. Using the line's end value once reported "already
    # above the safe limit" for a shelf reading 4.2 C inside a 0-5 C band, because the line
    # was still pulled up by the warmer readings before a gap.
    current = float(recent.iloc[-1])
    predicted = float(current + slope * horizon_steps)

    direction = None
    if current > max_safe_c:
        minutes_to_limit, direction = 0.0, "warm"
    elif min_safe_c is not None and current < min_safe_c:
        minutes_to_limit, direction = 0.0, "cold"
    elif slope > 0:
        minutes_to_limit, direction = float((max_safe_c - current) / slope) * STEP_MINUTES, "warm"
    elif slope < 0 and min_safe_c is not None:
        minutes_to_limit, direction = float((current - min_safe_c) / -slope) * STEP_MINUTES, "cold"
    else:
        minutes_to_limit = None

    at_risk = minutes_to_limit is not None and minutes_to_limit <= horizon_min
    return {
        "status": "at_risk" if at_risk else "stable",
        "direction": direction if at_risk else None,
        "slope_c_per_min": round(float(slope) / STEP_MINUTES, 4),
        "current_c": round(current, 2),
        "predicted_c": round(predicted, 2),
        "horizon_min": horizon_min,
        "minutes_to_limit": None if minutes_to_limit is None else round(minutes_to_limit, 1),
        "fit_r2": round(r2, 3),
    }
