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
  signature of a frozen/replayed signal. **Investigated, found non-viable, not shipped**
  (below).

## Why replay detection doesn't work on this dataset -- an honest negative result

The obvious design was: a real sensor has thermal and electrical noise, so exact repeats
across many consecutive readings should be rare, and a run far longer than anything
natural is a replay signature. Validating that against the real 405K-row dataset (the
same discipline that caught the earlier outlier-check false-positive issue) falsified it:

| Device | Median exact-repeat ratio per window | Longest natural same-value run |
|---|---|---|
| `b8:27:eb:bf:9d:51` | 83.6% | 353 of ~350 readings (i.e. nearly the whole window) |
| `00:0f:00:70:91:0a` | 85.1% | 207 |
| `1c:bf:ce:15:ec:4d` | 84.2% | 232 |

This specific sensor hardware caches a reading between its own internal sampling cycles
-- most consecutive polls return the same cached value, not a fresh measurement. Checking
whether temp *and* humidity freeze jointly doesn't help either (still 47-61% of readings
on average). There is no threshold in `[1, 360]` that cleanly separates "natural" from
"injected" for this hardware: the natural upper tail reaches the full window length.

**Decision**: `check_replay` stays in the codebase, correct and unit-tested against a
synthetic clean baseline (where this confound doesn't exist -- see
`dq_engine/tests/test_checks.py::TestIntegrity`), but is **not** called from
`score_window`. A replay-only window scores identically to an untouched one
(`TestScoringIntegrity::test_replay_is_not_part_of_composite_score`). The dashboard's
fault-injection demo includes "Replay attack" specifically to show this limitation live
-- it reports "Not detected" with the reasoning above, the same honesty pattern already
used for the subtle-calibration-bias blind spot.

**What would fix it**: cross-sensor comparison (does a second sensor in the same unit
show the same freeze?) or a reference/ground-truth channel -- the same fix the
calibration-bias blind spot needs, which strengthens the case that both gaps share one
root cause: single-sensor statistics have a hard ceiling on what they can prove.

## Demo

`backend/app/routers/demo.py` adds two fault types reusing the existing fault-injection
mechanism:

- **Data injection attack** -- trust collapses 100 -> 20, `integrity` flag fires,
  detected.
- **Replay attack** -- trust stays 100 -> 100, not detected, with the limitation
  explained inline.

## Known limitations beyond the replay gap

- One shared `DEVICE_API_KEY` for all devices (see `docs/access_control.md`) -- a real
  deployment would provision one key or certificate per device, which would also let
  `security_event` attribute failed-auth attempts to a specific device rather than
  logging them as anonymous.
- No rate limiting on the ingestion endpoint -- a flood of valid-looking requests from an
  authenticated device isn't currently distinguished from normal traffic.
- `security_event` has no alerting/notification; it's a log a Quality Supervisor has to
  check, not a push alert.
