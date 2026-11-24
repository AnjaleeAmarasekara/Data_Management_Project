"""Classification metrics for the data quality layer.

The question an evaluator asks is "how accurate is the quality layer?". Real sensor data has
no ground truth for "this window was faulty", so the faults are injected: a window known to be
clean is corrupted with a fault whose type and location are known, then scored by the
production code (dq_engine.scoring.score_window, the same fault functions as the dashboard's
fault-injection demo). Every outcome can then be put in a confusion matrix.

Design, so the numbers can be defended:

- Positives: clean real windows (no flag, trust High) from across the fleet, each corrupted
  with each of the ten faults. A positive is "detected" when the window raises the flag the
  fault is meant to raise. The subtle calibration offset has no expected flag, so it can only
  be a miss: it is included on purpose.
- Negatives: a random sample of real windows, NOT filtered for cleanliness. Any flag on one
  counts as a false alarm. Some of those windows may genuinely be faulty (a stuck sensor), so
  the false-alarm rate is an upper bound, not an optimistic one. Filtering the negatives with
  the same checks would make the false-alarm rate zero by construction.
- Each fault is scored against the same negatives, so every per-fault row is a one-vs-rest
  test and the macro average is comparable across faults.

Run: python -m dq_engine.evaluation.evaluate_quality
"""

import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.db import connect  # noqa: E402
from backend.app.routers.demo import FAULTS, FLAG_FOR_FAULT  # noqa: E402
from dq_engine.config import DEFAULT_INTERVAL_S, EXPECTED_INTERVAL_S  # noqa: E402
from dq_engine.scoring import score_window  # noqa: E402

ZONES = ["FC", "TS", "MS", "VD", "DS"]
FLAGS = ["missing_flag", "outlier_flag", "drift_flag", "noise_flag", "integrity_flag"]
N_POSITIVE_PER_ZONE = 60
N_NEGATIVE_PER_ZONE = 120
HIGH = 85.0  # a window is High at or above this trust
SEED = 20260211
OUT = ROOT / "docs" / "ml_evaluation_quality_layer.json"


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """95% interval for a proportion; honest about small samples."""
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def metrics(tp: int, fp: int, fn: int, tn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "accuracy": round((tp + tn) / (tp + fp + fn + tn), 3),
        "false_positive_rate": round(fp / (fp + tn), 3) if fp + tn else 0.0,
        "recall_ci95": wilson(tp, tp + fn),
        "false_positive_rate_ci95": wilson(fp, fp + tn),
    }


def window_readings(cur, device_id, start, end) -> pd.DataFrame:
    cur.execute(
        "SELECT ts, temp, humidity FROM sensor_reading WHERE device_id = %s AND ts >= %s AND ts < %s ORDER BY ts",
        (device_id, start, end),
    )
    return pd.DataFrame(cur.fetchall(), columns=["ts", "temp", "humidity"])


def sample_windows(cur, zone: str, where: str, n: int, rng: random.Random) -> list[tuple]:
    cur.execute(
        f"""SELECT device_id, window_start, window_end, missing_flag, outlier_flag, drift_flag, noise_flag, integrity_flag, trust_value
            FROM quality_score
            WHERE device_id LIKE %s AND {where}""",
        (f"BKK-%-{zone}",),
    )
    rows = cur.fetchall()
    rng.shuffle(rows)
    return rows[:n]


def main() -> None:
    rng = random.Random(SEED)
    negatives, positives = [], []  # each: the set of flags a window raised
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE missing_flag OR outlier_flag OR drift_flag OR noise_flag OR integrity_flag) "
            "FROM quality_score WHERE device_id NOT LIKE '%-PT' AND device_id NOT LIKE '%-AM'"
        )
        fleet_windows, fleet_flagged = cur.fetchone()

        for zone in ZONES:
            for r in sample_windows(cur, zone, "TRUE", N_NEGATIVE_PER_ZONE, rng):
                negatives.append({"zone": zone, "raised": {f for f, v in zip(FLAGS, r[3:8]) if v}, "trust": float(r[8])})

            bases = sample_windows(
                cur, zone,
                "NOT (missing_flag OR outlier_flag OR drift_flag OR noise_flag OR integrity_flag) AND trust_value >= 99",
                N_POSITIVE_PER_ZONE * 3, rng,
            )
            used = 0
            for device_id, ws, we, *_ in bases:
                clean = window_readings(cur, device_id, ws, we)
                if len(clean) < 20:
                    continue
                interval = EXPECTED_INTERVAL_S.get(device_id, DEFAULT_INTERVAL_S)
                for fault, make in FAULTS.items():
                    _, faulty = make(clean.copy(), device_id)
                    score = score_window(device_id, faulty, interval, window_start=pd.Timestamp(ws), window_end=pd.Timestamp(we))
                    positives.append({"fault": fault, "zone": zone, "raised": {f for f in FLAGS if getattr(score, f)}, "trust": float(score.trust_value)})
                used += 1
                if used == N_POSITIVE_PER_ZONE:
                    break

    n_neg = len(negatives)
    false_alarms = sum(1 for n in negatives if n["raised"])

    per_fault = {}
    for fault in FAULTS:
        rows = [p for p in positives if p["fault"] == fault]
        expected = FLAG_FOR_FAULT[fault]
        tp = 0 if expected is None else sum(1 for p in rows if expected in p["raised"])
        per_fault[fault] = {
            "expected_flag": expected, "windows": len(rows),
            **metrics(tp, false_alarms, len(rows) - tp, n_neg - false_alarms),
        }

    # Which flags each fault actually raised: cross-talk between checks.
    matrix = {}
    for fault in FAULTS:
        rows = [p for p in positives if p["fault"] == fault]
        matrix[fault] = {f: round(sum(1 for p in rows if f in p["raised"]) / len(rows), 3) for f in FLAGS}
        matrix[fault]["any_flag"] = round(sum(1 for p in rows if p["raised"]) / len(rows), 3)

    # Per check: positives are windows carrying a fault that check is meant to catch.
    per_check = {}
    for flag in FLAGS:
        faults = [f for f, e in FLAG_FOR_FAULT.items() if e == flag]
        rows = [p for p in positives if p["fault"] in faults]
        tp = sum(1 for p in rows if flag in p["raised"])
        fp = sum(1 for n in negatives if flag in n["raised"])
        per_check[flag] = {"catches": faults, "windows": len(rows), **metrics(tp, fp, len(rows) - tp, n_neg - fp)}

    # Window level: "is anything wrong with this window?", the question a user actually asks.
    # Every fault is a positive, including the subtle offset, so this is the honest overall figure.
    tp_any = sum(1 for p in positives if p["raised"])
    scale = len(positives) / n_neg  # re-weight negatives to a 50:50 mix so accuracy is not flattered
    overall = metrics(
        tp_any, round(false_alarms * scale), len(positives) - tp_any, round((n_neg - false_alarms) * scale)
    )
    overall["note"] = "negatives re-weighted to match the number of positives, so accuracy is on a 50:50 mix"

    # User-visible level: what the dashboard shows. A window is "alarmed" when its label is not High.
    # An isolated fault (one spike) is excluded at row level and leaves the window High by design,
    # so this level is deliberately stricter than the per-check one.
    neg_alarm = sum(1 for n in negatives if n["trust"] < HIGH)
    label_level = {}
    for fault in FAULTS:
        rows = [p for p in positives if p["fault"] == fault]
        tp = sum(1 for p in rows if p["trust"] < HIGH)
        label_level[fault] = metrics(tp, neg_alarm, len(rows) - tp, n_neg - neg_alarm)
    tp_l = sum(1 for p in positives if p["trust"] < HIGH)
    overall_label = metrics(tp_l, round(neg_alarm * scale), len(positives) - tp_l, round((n_neg - neg_alarm) * scale))
    overall_label["note"] = overall["note"]

    detectable = [f for f in FAULTS if FLAG_FOR_FAULT[f]]
    macro = {k: round(sum(per_fault[f][k] for f in detectable) / len(detectable), 3) for k in ("precision", "recall", "f1")}

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": __doc__.strip().split("\n\nRun:")[0],
        "sample": {
            "positive_windows_per_fault": len(positives) // len(FAULTS),
            "negative_windows": n_neg,
            "zones": ZONES,
            # Cold-zone windows only, matching what this evaluation samples: the product probe
            # and ambient channels are scored too, but they are not cold-storage units.
            "cold_unit_windows_scored": fleet_windows,
            "cold_unit_windows_with_any_flag": fleet_flagged,
            "cold_unit_flag_rate": round(fleet_flagged / fleet_windows, 4),
        },
        "false_alarm_rate_on_real_windows": {
            "flagged": false_alarms, "of": n_neg, "rate": round(false_alarms / n_neg, 3), "ci95": wilson(false_alarms, n_neg),
        },
        "false_alarm_rate_label_level": {"flagged": neg_alarm, "of": n_neg, "rate": round(neg_alarm / n_neg, 3), "ci95": wilson(neg_alarm, n_neg)},
        "per_fault": per_fault,
        "per_fault_label_level": label_level,
        "window_level_overall_label_level": overall_label,
        "per_check": per_check,
        "fault_vs_flag_rate": matrix,
        "window_level_overall": overall,
        "macro_average_detectable_faults": macro,
    }
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"negatives {n_neg}, flagged {false_alarms} ({false_alarms / n_neg:.1%}); fleet flag rate {fleet_flagged / fleet_windows:.2%}")
    print(f"{'fault':26} {'win':>4} {'prec':>6} {'rec':>6} {'f1':>6} {'acc':>6}")
    for f, m in per_fault.items():
        print(f"{f:26} {m['windows']:>4} {m['precision']:>6} {m['recall']:>6} {m['f1']:>6} {m['accuracy']:>6}")
    print("overall", {k: overall[k] for k in ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "accuracy")})
    print("macro", macro)
    print(f"label-level false alarms {neg_alarm}/{n_neg}", {f: label_level[f]['recall'] for f in FAULTS})
    print("overall (label level)", {k: overall_label[k] for k in ('tp', 'fp', 'fn', 'tn', 'precision', 'recall', 'f1', 'accuracy')})


if __name__ == "__main__":
    main()
