"""ARIMA short-horizon forecast -- the analytics-milestone upgrade over the plain linear
trend used earlier. Falls back to that trend model when there isn't enough clean history
or no candidate order converges, so the forecast never just goes blank.
"""

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA

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


def forecast_arima(series: pd.Series, max_safe_c: float, lookback_min: int = 45, horizon_min: int = 30) -> dict:
    recent = series.dropna().iloc[-lookback_min:]
    if len(recent) < MIN_POINTS:
        return {**forecast_trend(series, max_safe_c, lookback_min=min(30, len(series)), horizon_min=horizon_min), "method": "trend_fallback"}

    fit = _best_arima(recent.values)
    if fit is None:
        return {**forecast_trend(series, max_safe_c, horizon_min=horizon_min), "method": "trend_fallback"}

    forecast = np.asarray(fit.forecast(steps=horizon_min))
    current = float(recent.iloc[-1])
    predicted = float(forecast[-1])

    minutes_to_limit = None
    over = np.where(forecast > max_safe_c)[0]
    if current > max_safe_c:
        minutes_to_limit = 0.0
    elif len(over) > 0:
        minutes_to_limit = float(over[0] + 1)

    at_risk = minutes_to_limit is not None and minutes_to_limit <= horizon_min
    return {
        "status": "at_risk" if at_risk else "stable",
        "method": "arima",
        "order": list(fit.model.order),
        "aic": round(float(fit.aic), 1),
        "current_c": round(current, 2),
        "predicted_c": round(predicted, 2),
        "horizon_min": horizon_min,
        "minutes_to_limit": minutes_to_limit,
    }
