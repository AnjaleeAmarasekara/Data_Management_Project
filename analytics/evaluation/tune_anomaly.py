"""Is the Isolation Forest's weak evaluation result a tuning problem?

The detector's one real free parameter is `contamination`, the fraction of minutes it is
allowed to flag. At the production value (0.5%) the flag budget is smaller than the
injected anomalies plus the data's own natural outliers, which caps recall by construction.

To avoid fooling ourselves, tuning and reporting use DIFFERENT random trials: contamination
is chosen on the "tune" seeds, then the chosen value and the production baseline are both
scored on untouched "held-out" seeds. Reporting a number tuned and scored on the same
trials would just measure how well the setting fits those particular injections.

Raising contamination is not free -- it raises the number of 'watch' alerts a human has to
read -- so alert volume is reported next to F1 and the tradeoff is left visible.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingestion"))

from analytics.anomaly import IF_CONTAMINATION  # noqa: E402
from analytics.config import UNITS  # noqa: E402
from analytics.evaluation.evaluate_anomaly import aggregate, run_trial, stable_seed  # noqa: E402
from analytics.prepare import load_clean_minutely, sensor_day_zero  # noqa: E402
from db import DATABASE_URL  # noqa: E402

CANDIDATES = [0.005, 0.01, 0.02, 0.04]
TUNE_TRIALS = 8
HELDOUT_TRIALS = 20
KINDS = ("temp_bump", "humidity_anomaly")


def score(clean_df, max_safe_c, device_id, kind, contamination, n_trials, phase):
    rng = np.random.default_rng(seed=stable_seed(device_id, kind, phase))
    return aggregate([run_trial(clean_df, max_safe_c, rng, kind, contamination) for _ in range(n_trials)])


def main() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        day_zero = sensor_day_zero(conn)
        data = {did: (cfg, load_clean_minutely(conn, did, cfg, day_zero)) for did, cfg in UNITS.items()}

    # ---- phase 1: choose contamination on the tuning seeds (mean F1 across all unit x kind cases)
    sweep = {}
    for c in CANDIDATES:
        f1s = []
        for did, (cfg, df) in data.items():
            for kind in KINDS:
                f1s.append(score(df, cfg.max_safe_c, did, kind, c, TUNE_TRIALS, "tune")["mean_f1"])
        sweep[c] = round(float(np.mean(f1s)), 3)
        print(f"tune: contamination={c}: mean F1 across units/kinds = {sweep[c]}", flush=True)
    best = max(sweep, key=sweep.get)
    print(f"-> chosen on tuning seeds: {best}", flush=True)

    # ---- phase 2: score production baseline vs chosen value on held-out seeds
    heldout = {}
    for label, c in (("production", IF_CONTAMINATION), ("tuned", best)):
        heldout[label] = {"contamination": c, "units": {}}
        for did, (cfg, df) in data.items():
            for kind in KINDS:
                agg = score(df, cfg.max_safe_c, did, kind, c, HELDOUT_TRIALS, "heldout")
                heldout[label]["units"].setdefault(cfg.name, {})[kind] = agg
                print(
                    f"heldout [{label} c={c}] {cfg.name}/{kind}: P={agg['mean_precision']:.3f} R={agg['mean_recall']:.3f} "
                    f"F1={agg['mean_f1']:.3f} alerts~{agg['mean_alert_clusters']}",
                    flush=True,
                )

    out = ROOT / "docs" / "ml_evaluation_isolation_forest_tuning.json"
    out.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "method": f"contamination chosen on {TUNE_TRIALS} 'tune' trials per case; production vs chosen then scored on "
                f"{HELDOUT_TRIALS} separate held-out trials per case.",
                "tuning_sweep_mean_f1": sweep,
                "chosen_contamination": best,
                "heldout": heldout,
            },
            indent=2,
        )
    )
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
