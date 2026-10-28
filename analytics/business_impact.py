"""Business impact estimation: revenue at risk from cold-chain excursions.

  degree_minutes        = sum over minutes of max(0, cold_temp - max_safe)
  spoilage_probability  = 1 - exp(-degree_minutes / SCALE)
  revenue_at_risk       = sales_exposed * spoilage_probability

sales_exposed is the day's sales of the categories stored in that unit. This is a
documented heuristic -- it demonstrates the enterprise-data-integration idea (sensor
conditions x retail exposure), it is not a calibrated spoilage model. Each estimate also
carries the average data trust for that day, so a large number resting on low-trust data
is visibly less reliable than the same number on clean data.
"""

import numpy as np
import pandas as pd

from analytics.config import MIN_DAY_COVERAGE_MIN, SPOILAGE_DEGREE_MINUTES_SCALE, UnitConfig


def estimate_impact(df: pd.DataFrame, cfg: UnitConfig, sales_by_day: dict) -> list[dict]:
    """df: clean minute-level data with cold_temp/trust/relative_day.
    sales_by_day: {relative_day: {category: total_sales}}"""
    rows = []
    for day, day_df in df.groupby("relative_day"):
        if len(day_df) < MIN_DAY_COVERAGE_MIN:
            continue
        over = (day_df["cold_temp"] - cfg.max_safe_c).clip(lower=0.0)
        degree_minutes = float(over.sum())
        probability = float(1.0 - np.exp(-degree_minutes / SPOILAGE_DEGREE_MINUTES_SCALE))
        exposed = sum(sales_by_day.get(int(day), {}).get(cat, 0.0) for cat in cfg.categories)
        rows.append(
            {
                "relative_day": int(day),
                "categories": ", ".join(cfg.categories),
                "sales_exposed": round(exposed, 2),
                "excursion_minutes": int((over > 0).sum()),
                "degree_minutes": round(degree_minutes, 2),
                "peak_temp_c": round(float(day_df["cold_temp"].max()), 2),
                "spoilage_probability": round(probability, 4),
                "revenue_at_risk": round(exposed * probability, 2),
                "avg_trust": round(float(day_df["trust"].mean()), 2),
            }
        )
    return rows
