"""Walk-forward backtest for the ARIMA forecast.

Unlike anomaly detection, this needs no synthetic ground truth: it's real historical
data, so the "actual" values the forecast is checked against are genuinely real. At each
origin point, forecast forward using only data up to that point (never looking ahead),
then compare the forecast to what the real series actually did next. Reports both raw
point-forecast error (MAE/RMSE) and the business-relevant classification the dashboard
actually surfaces: did the forecast correctly call "at_risk" vs "stable" against whether
the unit really crossed its safe limit in that window.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingestion"))

from analytics.arima import forecast_arima  # noqa: E402
from analytics.config import FORECAST_HORIZON_STEPS, FORECAST_LOOKBACK_STEPS, STEP_MINUTES, UNITS  # noqa: E402
from analytics.prepare import load_clean_minutely, sensor_day_zero  # noqa: E402
from db import DATABASE_URL  # noqa: E402

LOOKBACK_STEPS = FORECAST_LOOKBACK_STEPS
HORIZON_STEPS = FORECAST_HORIZON_STEPS
HORIZON_MIN = HORIZON_STEPS * STEP_MINUTES
ORIGIN_SPACING = 12  # spacing between backtest origins -- every hour across the full history
MIN_ROWS_REQUIRED = LOOKBACK_STEPS + HORIZON_STEPS + 50


def backtest_unit(cold_temp: pd.Series, max_safe_c: float, min_safe_c: float | None = None) -> dict:
    """The forecast warns about both ends of the safe band, so a real breach is judged the
    same way - leaving the actuals warm-only would score every correct freeze warning as a
    false alarm."""
    n = len(cold_temp)
    origins = range(LOOKBACK_STEPS, n - HORIZON_STEPS, ORIGIN_SPACING)

    abs_errors, sq_errors = [], []
    base_abs, base_sq = [], []  # persistence baseline: "the last observed value simply persists"
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}  # "at_risk" as the positive class
    # Early warning = the claim the dashboard actually makes ("will exceed the limit in N min").
    # Restricted to origins where the unit is CURRENTLY within its limit: while already above it,
    # flagging "at risk" is trivial and says nothing about forecasting skill.
    early = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    used = 0

    for origin in origins:
        history = cold_temp.iloc[: origin + 1]  # everything up to and including "now" -- no lookahead
        future_actual = cold_temp.iloc[origin + 1 : origin + 1 + HORIZON_STEPS]
        if len(future_actual) < HORIZON_STEPS:
            continue

        result = forecast_arima(
            history, max_safe_c, lookback_steps=LOOKBACK_STEPS, horizon_steps=HORIZON_STEPS, min_safe_c=min_safe_c
        )
        if result.get("status") not in ("at_risk", "stable"):
            continue
        used += 1

        # point-forecast error: compare the single predicted_c (end-of-horizon value) to
        # what the series actually was at that point
        err = result["predicted_c"] - float(future_actual.iloc[-1])
        abs_errors.append(abs(err))
        sq_errors.append(err**2)

        base_err = float(history.iloc[-1]) - float(future_actual.iloc[-1])
        base_abs.append(abs(base_err))
        base_sq.append(base_err**2)

        predicted_at_risk = result["status"] == "at_risk"
        actual_at_risk = bool((future_actual > max_safe_c).any()) or (
            min_safe_c is not None and bool((future_actual < min_safe_c).any())
        )
        if predicted_at_risk and actual_at_risk:
            confusion["tp"] += 1
        elif predicted_at_risk and not actual_at_risk:
            confusion["fp"] += 1
        elif not predicted_at_risk and actual_at_risk:
            confusion["fn"] += 1
        else:
            confusion["tn"] += 1

        now = float(history.iloc[-1])
        if now <= max_safe_c and (min_safe_c is None or now >= min_safe_c):  # currently inside the band
            key = ("tp" if actual_at_risk else "fp") if predicted_at_risk else ("fn" if actual_at_risk else "tn")
            early[key] += 1

    if not abs_errors:
        return {"n_origins_used": 0}

    tp, fp, fn, tn = confusion["tp"], confusion["fp"], confusion["fn"], confusion["tn"]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    etp, efp, efn, etn = early["tp"], early["fp"], early["fn"], early["tn"]
    return {
        "n_origins_used": used,
        "early_warning": {
            **early,
            "positives": etp + efn,
            "precision": round(etp / (etp + efp), 3) if (etp + efp) else None,
            "recall": round(etp / (etp + efn), 3) if (etp + efn) else None,
            "note": "persistence cannot early-warn by construction: from a within-limit state it always predicts no crossing, so its recall here is 0",
        },
        "mae_c": round(float(np.mean(abs_errors)), 3),
        "rmse_c": round(float(np.sqrt(np.mean(sq_errors))), 3),
        "persistence_baseline": {
            "mae_c": round(float(np.mean(base_abs)), 3),
            "rmse_c": round(float(np.sqrt(np.mean(base_sq))), 3),
            # >0 means ARIMA beats persistence; <=0 means it adds nothing over "assume no change"
            "mae_skill": round(1 - float(np.mean(abs_errors)) / float(np.mean(base_abs)), 3) if np.mean(base_abs) else None,
        },
        "at_risk_classification": {
            **confusion,
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "accuracy": round((tp + tn) / used, 3),
        },
    }


def main() -> None:
    results: dict = {}
    with psycopg.connect(DATABASE_URL) as conn:
        day_zero = sensor_day_zero(conn)
        for device_id, cfg in UNITS.items():
            df = load_clean_minutely(conn, device_id, cfg, day_zero)
            if len(df) < MIN_ROWS_REQUIRED:
                print(f"{cfg.name}: skipped, not enough clean data ({len(df)} rows)")
                continue

            agg = backtest_unit(df.set_index("ts")["cold_temp"], cfg.max_safe_c, cfg.min_safe_c)
            results[cfg.name] = agg
            if agg["n_origins_used"]:
                c = agg["at_risk_classification"]
                print(
                    f"{cfg.name}: {agg['n_origins_used']} origins | MAE={agg['mae_c']}C RMSE={agg['rmse_c']}C | "
                    f"persistence MAE={agg['persistence_baseline']['mae_c']}C skill={agg['persistence_baseline']['mae_skill']} | "
                    f"at-risk precision={c['precision']} recall={c['recall']} accuracy={c['accuracy']} "
                    f"(tp={c['tp']} fp={c['fp']} fn={c['fn']} tn={c['tn']})"
                )
                e = agg["early_warning"]
                print(f"    early-warning only (currently within limit): positives={e['positives']} tp={e['tp']} fp={e['fp']} fn={e['fn']} precision={e['precision']} recall={e['recall']}")
            else:
                print(f"{cfg.name}: no usable backtest origins")

    out_path = ROOT / "docs" / "ml_evaluation_arima.json"
    out_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "method": (
                    f"Walk-forward backtest: at every origin (every {ORIGIN_SPACING * STEP_MINUTES} min across real history), "
                    f"forecast {HORIZON_MIN} min ahead using only the preceding {LOOKBACK_STEPS * STEP_MINUTES} min, then "
                    "compare to the real values that actually followed. No synthetic data -- genuinely "
                    "real held-out actuals."
                ),
                "results": results,
            },
            indent=2,
        )
    )
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
