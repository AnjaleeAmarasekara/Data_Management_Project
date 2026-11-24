"""Business impact estimation: revenue at risk from cold-chain excursions.

  degree_minutes        = sum over minutes of  max(0, temp - max_safe)      (too warm)
                                              + max(0, min_safe - temp)      (too cold)
  spoilage_probability  = 1 - exp(-degree_minutes / SCALE)
  revenue_at_risk       = sales_exposed * spoilage_probability

Both directions count. Freezing ruins chilled stock as surely as warming does, and a model
that only counted warm exposure reported the demo top shelf - 38 cold excursions with dairy
on it - as carrying almost no risk. The same scale is applied to both sides; that is a
simplification, stated here rather than hidden, since freezing damage is in reality more
abrupt than gradual warm spoilage.

sales_exposed is the day's sales of the categories stored in that unit. This is a
documented heuristic -- it demonstrates the enterprise-data-integration idea (sensor
conditions x retail exposure), it is not a calibrated spoilage model. Each estimate also
carries the average data trust for that day, so a large number resting on low-trust data
is visibly less reliable than the same number on clean data.
"""

import numpy as np
import pandas as pd

from analytics.config import MIN_DAY_COVERAGE_STEPS, SPOILAGE_DEGREE_MINUTES_SCALE, STEP_MINUTES, UnitConfig


def estimate_impact(
    df: pd.DataFrame, cfg: UnitConfig, sales_by_day: dict, product_df: pd.DataFrame | None = None
) -> list[dict]:
    """df: clean data on the sensor's sampling grid, with cold_temp/trust/relative_day.
    sales_by_day: {relative_day: {category: total_sales}}
    product_df: optional probe series measuring the goods themselves.

    Spoilage is driven by the temperature of the food, not of the air around it, and the
    two disagree often enough to matter: across the 123 refrigerators, 10 would raise a
    breach on air temperature that the product never experienced, and 4 would miss a real
    product breach that the air hid. Where a probe exists its readings replace the air
    temperature, and the basis is recorded on every row so no figure silently implies a
    precision it does not have.
    """
    basis = "air"
    if product_df is not None and not product_df.empty:
        probe = product_df[["ts", "cold_temp"]].rename(columns={"cold_temp": "product_temp"})
        df = df.merge(probe, on="ts", how="inner")
        if not df.empty:
            df = df.drop(columns=["cold_temp"]).rename(columns={"product_temp": "cold_temp"})
            basis = "product"

    rows = []
    for day, day_df in df.groupby("relative_day"):
        if len(day_df) < MIN_DAY_COVERAGE_STEPS:
            continue
        over = (day_df["cold_temp"] - cfg.max_safe_c).clip(lower=0.0)
        under = (
            (cfg.min_safe_c - day_df["cold_temp"]).clip(lower=0.0)
            if cfg.min_safe_c is not None
            else pd.Series(0.0, index=day_df.index)
        )
        # each sample stands for STEP_MINUTES minutes of exposure, so degree-minutes is
        # the excess temperature integrated over real time, not summed per row
        warm_degree_minutes = float(over.sum()) * STEP_MINUTES
        cold_degree_minutes = float(under.sum()) * STEP_MINUTES
        degree_minutes = warm_degree_minutes + cold_degree_minutes
        probability = float(1.0 - np.exp(-degree_minutes / SPOILAGE_DEGREE_MINUTES_SCALE))
        exposed = sum(sales_by_day.get(int(day), {}).get(cat, 0.0) for cat in cfg.categories)
        rows.append(
            {
                "relative_day": int(day),
                "categories": ", ".join(cfg.categories),
                "sales_exposed": round(exposed, 2),
                "excursion_minutes": int((over > 0).sum()) * STEP_MINUTES,
                "cold_minutes": int((under > 0).sum()) * STEP_MINUTES,
                "degree_minutes": round(degree_minutes, 2),
                "cold_degree_minutes": round(cold_degree_minutes, 2),
                "peak_temp_c": round(float(day_df["cold_temp"].max()), 2),
                "low_temp_c": round(float(day_df["cold_temp"].min()), 2),
                "spoilage_probability": round(probability, 4),
                "revenue_at_risk": round(exposed * probability, 2),
                "avg_trust": round(float(day_df["trust"].mean()), 2),
                "temp_basis": basis,
            }
        )
    return rows
