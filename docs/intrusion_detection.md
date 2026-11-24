# Intrusion Detection: What's Built, What Was Tried, What Didn't Work

Enhancing data reliability turns out to overlap directly with a well-studied security
topic: **False Data Injection Attacks (FDIA)** against IoT/industrial sensor networks,
where an adversary feeds a sensor fabricated readings to hide a real problem or trigger
false alarms. The core finding in that literature is that a slow, gradual injection is
statistically indistinguishable from natural sensor drift, and a single injected reading
looks like a natural spike -- the same statistical territory anomaly-based intrusion
detection already occupies. Two things follow from that, and both are built here.

## 1. Access-layer intrusion detection (who's trying to get in)

`backend/app/routers/ingest.py` logs every failed device-key check and every push from
an unregistered `device_id` to a new `security_event` table -- a literal unauthorized-
access log, distinct from judging whether *accepted* data is trustworthy. Surfaced via
`GET /api/security/events` (Quality Supervisor and above) and a panel on the Data Quality
page.

## 2. Data-layer tampering detection (is accepted data trustworthy)

`dq_engine/checks/integrity.py` adds two checks framed around tampering signatures
rather than ordinary faults:

- **`check_impossible_jump`** -- a single-step change bigger than a cold-storage unit's
  thermal mass could produce (>15C in one sample). This is an absolute physical bound,
  not a statistical one, so it's immune to the drift/noise confounds the other checks
  have to work around. **Shipped**: wired into `scoring.py` as the `integrity` flag,
  weighted at 0.18, and hard-capped at trust 20 when it fires -- the same severity
  treatment as the implausible-outlier and drift caps.
- **`check_replay`** -- flags long runs of exact-repeat consecutive values, the classic
  signature of a frozen/replayed signal. **Investigated, rejected, then re-validated on a
  better dataset and shipped for most zones** (below).

## Replay detection: rejected once, then earned

This check has been through two rounds of validation, and the history is worth keeping
because it shows what changed and why.

### Round 1 — rejected on the earlier dataset

The original development data was room-level environmental telemetry. Validating the check
against it falsified the design:

| Device | Median exact-repeat ratio per window | Longest natural same-value run |
|---|---|---|
| `b8:27:eb:bf:9d:51` | 83.6% | 353 of ~350 readings (nearly the whole window) |
| `00:0f:00:70:91:0a` | 85.1% | 207 |
| `1c:bf:ce:15:ec:4d` | 84.2% | 232 |

That sensor cached a reading between its own internal sampling cycles, so most consecutive
polls returned the same cached value rather than a fresh measurement. No threshold could
separate natural from injected, so the check was excluded from scoring and the dashboard
reported "Not detected" with that reasoning.

### Round 2 — re-validated on the refrigeration dataset

The dataset was later replaced with real refrigeration telemetry (TagPlus-TH loggers,
5-minute interval, 0.1 °C resolution; see `data_linkage.md`). The same measurement on all
123 refrigerators gives a completely different picture:

| Zone | Exact-repeat ratio | Longest natural run | Clean windows flagged at min_run 8 / min_ratio 0.3 |
|---|---|---|---|
| Freezer compartment | 7.6% | 34 | **1.6%** |
| Top shelf chiller | 11.4% | 52 | **2.0%** |
| Middle shelf chiller | 23.8% | 44 | **4.5%** |
| Door shelf | 27.1% | 51 | **7.1%** |
| Vegetable drawer | 48.6% | 86 | **23.7%** |

The confound was the old hardware, not the idea. These loggers report a fresh measurement
each cycle, so a frozen run is a real signal in any zone that actually moves.

**Decision**: `check_replay` is now wired into `scoring.py`, **except for vegetable
drawers**. A drawer is thermally damped by the produce in it and genuinely sits still, so
nearly a quarter of its clean windows would be flagged — a check that cries wolf that often
is worse than no check. The drawer is a declared blind spot rather than a false-alarm source.

Replay and `check_impossible_jump` share the `integrity` weight (0.18), because they answer
the same question — was this fabricated rather than measured? They differ in how conclusive
they are, so they cap trust differently: an impossible jump is proof the reading did not come
from the real world and caps trust at 20; a frozen run is strong suspicion and caps it at 50.

Tested by `TestScoringIntegrity::test_replay_is_scored_where_the_zone_supports_it` and
`::test_replay_is_disabled_for_the_vegetable_drawer`.

## Still not detectable

Two blind spots remain, both named rather than hidden:

- **A slow, gradual injection** is indistinguishable from natural drift using single-sensor
  statistics — the same limitation the FDIA literature describes.
- **A subtle calibration offset** (for example +6 °C, still a physically plausible fridge
  temperature) shifts every reading uniformly, so no window-local statistic can separate it
  from a genuinely warmer unit. Catching it needs a second reference sensor or periodic
  recalibration against a known thermometer.

Both are demonstrated live by the dashboard's fault-injection panel, which reports them as
not detected with this reasoning.
