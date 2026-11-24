"""ARIMA short-horizon forecast -- the analytics-milestone upgrade over the plain linear
trend used earlier. Falls back to that trend model when there isn't enough clean history
or no candidate order converges, so the forecast never just goes blank.
"""

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA

from analytics.config import FORECAST_HORIZON_STEPS, FORECAST_LOOKBACK_STEPS, STEP_MINUTES
from analytics.forecast import forecast_trend

ORDER_CANDIDATES = [(1, 1, 1), (2, 1, 0)]
MIN_POINTS = 20


def _best_arima(values: np.ndarray):
    best = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for order in ORDER_CANDIDATES:
            try:
                fit = ARIMA(values, order=order).fit()
            except Exception:
                continue
            if best is None or fit.aic < best.aic:
                best = fit
    return best


def forecast_arima(
    series: pd.Series,
    max_safe_c: float,
    lookback_steps: int = FORECAST_LOOKBACK_STEPS,
    horizon_steps: int = FORECAST_HORIZON_STEPS,
    min_safe_c: float | None = None,
) -> dict:
    """series: cold_temp on the sensor's sampling grid. Horizons are counted in samples,
    so a six-step forecast on five-minute data looks thirty minutes ahead. Both ends of the
    safe band are watched; "direction" says which one is threatened."""
    recent = series.dropna().iloc[-lookback_steps:]
    horizon_min = horizon_steps * STEP_MINUTES
    if len(recent) < MIN_POINTS:
        fallback = forecast_trend(
            series, max_safe_c, lookback_steps=max(MIN_POINTS, len(series)), horizon_steps=horizon_steps,
            min_safe_c=min_safe_c,
        )
        return {**fallback, "method": "trend_fallback"}

    fit = _best_arima(recent.values)
    if fit is None:
        return {
            **forecast_trend(series, max_safe_c, horizon_steps=horizon_steps, min_safe_c=min_safe_c),
            "method": "trend_fallback",
        }

    forecast = np.asarray(fit.forecast(steps=horizon_steps))
    current = float(recent.iloc[-1])
    predicted = float(forecast[-1])

    minutes_to_limit, direction = None, None
    over = np.where(forecast > max_safe_c)[0]
    under = np.where(forecast < min_safe_c)[0] if min_safe_c is not None else np.array([], dtype=int)
    if current > max_safe_c:
        minutes_to_limit, direction = 0.0, "warm"
    elif min_safe_c is not None and current < min_safe_c:
        minutes_to_limit, direction = 0.0, "cold"
    elif len(over) or len(under):
        # whichever limit the forecast reaches first
        first_over = over[0] if len(over) else np.inf
        first_under = under[0] if len(under) else np.inf
        step, direction = (first_over, "warm") if first_over <= first_under else (first_under, "cold")
        minutes_to_limit = float(step + 1) * STEP_MINUTES

    at_risk = minutes_to_limit is not None and minutes_to_limit <= horizon_min
    return {
        "status": "at_risk" if at_risk else "stable",
        "direction": direction if at_risk else None,
        "method": "arima",
        "order": list(fit.model.order),
        "aic": round(float(fit.aic), 1),
        "current_c": round(current, 2),
        "predicted_c": round(predicted, 2),
        "horizon_min": horizon_min,
        "minutes_to_limit": minutes_to_limit,
    }
