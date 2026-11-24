"""Precision/recall evaluation for the Isolation Forest anomaly detector.

There is no real incident log for this dataset -- no ground truth exists for "this
reading really was an anomaly" the way a labeled dataset would provide. This follows the
standard practice for evaluating an unsupervised detector without labels: inject known
synthetic anomalies into real, quality-filtered data, run the actual production
detection code (analytics.anomaly.isolation_forest_flags, not a re-implementation), and
measure how many of the known injections were caught (recall) against how many flagged
minutes were false alarms on otherwise-clean data (precision). Run many random trials,
since a single run could get lucky or unlucky with where the injections happen to land.
"""

import json
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingestion"))

from analytics.anomaly import IF_CONTAMINATION, isolation_forest_flags  # noqa: E402
from analytics.config import UNITS  # noqa: E402
from analytics.evaluation.inject import inject_humidity_anomaly, inject_temp_bump, random_injection_plan  # noqa: E402
from analytics.prepare import load_clean_minutely, sensor_day_zero  # noqa: E402
from db import DATABASE_URL  # noqa: E402

N_TRIALS = 30
INJECTIONS_PER_TRIAL = 6
MIN_ROWS_REQUIRED = 500


def stable_seed(*parts) -> int:
    """Python's hash() of a str is randomized per process, which would make every run
    different. crc32 is stable, so the reported numbers are reproducible."""
    return zlib.crc32("|".join(map(str, parts)).encode())


def run_trial(
    clean_df: pd.DataFrame, max_safe_c: float, rng: np.random.Generator, kind: str, contamination: float | None = None
) -> dict:
    df = clean_df.reset_index(drop=True)
    plan = random_injection_plan(rng, len(df), INJECTIONS_PER_TRIAL)

    true_positions: set[int] = set()
    for start, duration in plan:
        magnitude = float(rng.uniform(1.5, 3.0))
        if kind == "temp_bump":
            df, positions = inject_temp_bump(df, start, duration, magnitude, max_safe_c)
        else:
            df, positions = inject_humidity_anomaly(df, start, duration, magnitude * 8)
        true_positions |= positions

    flags = isolation_forest_flags(df) if contamination is None else isolation_forest_flags(df, contamination)
    predicted = set(np.where(flags.values)[0].tolist())

    tp, fp, fn = len(predicted & true_positions), len(predicted - true_positions), len(true_positions - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    # clusters of flagged minutes ~ the number of "watch" alerts this setting would raise
    flagged_sorted = sorted(predicted)
    clusters = sum(1 for i, m in enumerate(flagged_sorted) if i == 0 or m - flagged_sorted[i - 1] > 2)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1,
            "flagged_minutes": len(predicted), "alert_clusters": clusters}


def aggregate(trials: list[dict]) -> dict:
    return {
        "n_trials": len(trials),
        "mean_precision": round(float(np.mean([t["precision"] for t in trials])), 3),
        "mean_recall": round(float(np.mean([t["recall"] for t in trials])), 3),
        "mean_f1": round(float(np.mean([t["f1"] for t in trials])), 3),
        "std_precision": round(float(np.std([t["precision"] for t in trials])), 3),
        "std_recall": round(float(np.std([t["recall"] for t in trials])), 3),
        "total_tp": sum(t["tp"] for t in trials),
        "total_fp": sum(t["fp"] for t in trials),
        "total_fn": sum(t["fn"] for t in trials),
        "mean_flagged_minutes": round(float(np.mean([t["flagged_minutes"] for t in trials])), 1),
        "mean_alert_clusters": round(float(np.mean([t["alert_clusters"] for t in trials])), 1),
    }


def injected_fraction(n_rows: int) -> float:
    """Roughly what share of a series an injection plan corrupts, used to set the
    budget-matched contamination below."""
    mean_duration = (3 + 11) / 2
    return INJECTIONS_PER_TRIAL * mean_duration / n_rows


def main() -> None:
    results: dict = {}
    with psycopg.connect(DATABASE_URL) as conn:
        day_zero = sensor_day_zero(conn)
        for device_id, cfg in UNITS.items():
            clean_df = load_clean_minutely(conn, device_id, cfg, day_zero)
            if len(clean_df) < MIN_ROWS_REQUIRED:
                print(f"{cfg.name}: skipped, not enough clean data ({len(clean_df)} rows)")
                continue

            # The production contamination is an *alert budget*: at 0.5% of a 5-day,
            # 5-minute series the detector may flag only ~7 samples, while a trial injects
            # ~42. Recall is therefore capped by the budget before the model's ability to
            # discriminate even comes into it. Reporting only that number would read as
            # "the model cannot detect anomalies" when the real finding is "the budget is
            # smaller than the thing being looked for". So each case is scored twice: once
            # as deployed, and once with the budget matched to the injected rate, which is
            # what actually measures discrimination.
            budget_matched = round(injected_fraction(len(clean_df)), 4)

            unit_results = {}
            for kind in ("temp_bump", "humidity_anomaly"):
                for label, contamination in (("production", None), ("budget_matched", budget_matched)):
                    rng = np.random.default_rng(seed=stable_seed(device_id, kind, "report", label))
                    trials = [run_trial(clean_df, cfg.max_safe_c, rng, kind, contamination) for _ in range(N_TRIALS)]
                    agg = aggregate(trials)
                    agg["contamination"] = contamination if contamination is not None else IF_CONTAMINATION
                    unit_results[f"{kind}__{label}"] = agg
                    print(
                        f"{cfg.name} / {kind} [{label} c={agg['contamination']}]: "
                        f"precision={agg['mean_precision']:.3f} recall={agg['mean_recall']:.3f} "
                        f"f1={agg['mean_f1']:.3f} (over {agg['n_trials']} trials)"
                    )
            results[cfg.name] = unit_results

    out_path = ROOT / "docs" / "ml_evaluation_isolation_forest.json"
    out_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "method": (
                    "Synthetic anomaly injection: known temp/humidity anomalies injected into real "
                    "quality-filtered data, detected with the production isolation_forest_flags() code, "
                    f"averaged over {N_TRIALS} random trials per unit per anomaly type. Each case is "
                    "scored twice: 'production' uses the deployed contamination (an alert budget far "
                    "smaller than the injected anomaly count, which caps recall by construction), and "
                    "'budget_matched' sets contamination to the injected rate, which is what measures "
                    "the detector's ability to discriminate."
                ),
                "results": results,
            },
            indent=2,
        )
    )
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
