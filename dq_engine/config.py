"""Settings shared by the batch scorer and the live API scoring.

Window length is driven by the sampling rate. The loggers report every five minutes, so a
two-hour window holds about 24 readings -- enough for the quartile and trend statistics in
the checks to mean anything, while still locating a fault to within a couple of hours. The
earlier 30-minute window came from a dataset sampled every few seconds; keeping it here
would leave six readings per window, too few to compute a spread from.
"""

WINDOW_MINUTES = 120
DEFAULT_INTERVAL_S = 300.0
# Every TagPlus-TH logger in the source dataset reports on the same five-minute schedule,
# so there is no per-device override; DEFAULT_INTERVAL_S applies to all of them.
EXPECTED_INTERVAL_S: dict[str, float] = {}

# How far a reading may move from the one before it, in degrees, before it counts towards
# the noise check. This is equipment physics, not a universal constant: a freezer runs a
# defrost cycle and swings hard, while a vegetable drawer is damped by the produce in it.
# Calibrated against the measured 5-minute change distribution over all 123 refrigerators
# (95th percentile: freezer 2.0C, every chilled zone at or below 0.9C). At these settings
# the share of clean windows flagged as noisy is 0.1% for freezers and 0.0% elsewhere;
# the previous single 2.0C threshold flagged 25% of freezer windows.
# PT (the gel probe) and AM (ambient air) are far more damped than any cold zone: their
# 99th-percentile step change is 0.5 C, against 2.0 C for a freezer. Leaving them on the
# chilled default of 3.0 would be safe but useless - the check could never fire. 1.5 is
# three times their measured 99th percentile, so it stays silent on clean data while
# still being able to catch real jitter.
NOISE_RATE_THRESHOLD_BY_ZONE = {"FC": 6.0, "PT": 1.5, "AM": 1.5}
DEFAULT_NOISE_RATE_THRESHOLD = 3.0

# Drift thresholds, as (slope per reading, shift between window halves in degrees).
# Set at roughly the 99th percentile of each zone's own behaviour across all 123
# refrigerators, so about 1% of clean windows are flagged: freezers run defrost cycles
# and legitimately shift several degrees within two hours, chilled zones do not.
# Measured flag rate on clean data: freezer 1.0%, top shelf 1.2%, elsewhere under 1%.
#
# Known limit: a two-hour window can only reveal a trend inside that window -- a door left
# open, a failing compressor. True calibration drift over weeks needs a longer baseline
# (comparing a unit against its own history or a reference sensor), which is future work.
DRIFT_THRESHOLDS_BY_ZONE = {"FC": (0.7, 9.0)}
DEFAULT_DRIFT_THRESHOLDS = (0.3, 3.5)


def _zone_of(device_id: str) -> str:
    """Zone is the suffix of the device id, e.g. BKK-012-FC is a freezer compartment."""
    return device_id.rsplit("-", 1)[-1]


# Replay / frozen-signal detection, re-validated against this dataset.
#
# This check was previously disabled outright. On the earlier development dataset the
# sensor cached a reading between its own sampling cycles, so a median of ~83% of readings
# were exact repeats of the one before and no threshold could separate natural from
# injected. The TagPlus-TH loggers used here do not behave that way: exact repeats run at
# 7.6% in freezer compartments and 11.4% on top shelves, so the check becomes usable.
#
# It stays off for vegetable drawers. Those are thermally damped by the produce in them and
# genuinely sit still for long stretches: 48.6% of their readings repeat. A check that cries
# wolf that often is worse than no check, so the drawer is a declared blind spot rather than
# a false-alarm generator.
#
# Thresholds are set by one rule: every enabled zone must stay under a 2% false-positive
# rate on clean windows. At min_run 12 / min_ratio 0.49 the measured rates are freezer
# 0.6%, top shelf 0.7%, middle shelf 1.2%, door shelf 1.9% -- and the vegetable drawer
# 8.4%, which is why it is excluded. Looser settings were measured and rejected: at
# min_run 8 / min_ratio 0.3 the door shelf reaches 7.1% and the network-wide Low rate
# tripled, which is too noisy for a signal that means "possible tampering".
#
# The cost of this strictness is stated rather than hidden: detection now needs a frozen
# run of at least 12 samples (one hour) covering about half the window, so a brief replay
# goes unseen. That is the deliberate trade -- a security alert nobody trusts is worthless.
# A channel that has sent nothing for this long is treated as silent - a dead sensor, a
# flat battery, or a gateway that has lost its connection. Six missed readings at the
# five-minute interval. Silence is measured against the newest reading from the same
# premises (its sibling channels) and the premises against the newest reading anywhere,
# not against the wall clock: the development data is a historical record, so "now"
# would make every sensor look dead. In a live deployment the newest reading anywhere
# tracks the wall clock closely, so the same rule applies unchanged.
SILENCE_THRESHOLD_MIN = 30

# Humidity rate-of-change threshold, measured the same way as the temperature ones.
# At 8 points per sample 13.0% of clean windows flag; at 20 it is 1.1%.
HUMIDITY_NOISE_RATE = 20.0

# PT and AM join the vegetable drawer as declared blind spots for replay detection, and
# for the same reason measured rather than assumed: the gel probe repeats 92.1% of its
# readings and ambient air 63.8%, because both are thermally massive and barely move
# between samples. No threshold separates that from an injected frozen value.
REPLAY_DISABLED_ZONES = {"VD", "PT", "AM"}
REPLAY_MIN_RUN = 12
REPLAY_MIN_RATIO = 0.49


def noise_rate_threshold(device_id: str) -> float:
    return NOISE_RATE_THRESHOLD_BY_ZONE.get(_zone_of(device_id), DEFAULT_NOISE_RATE_THRESHOLD)


def replay_enabled(device_id: str) -> bool:
    return _zone_of(device_id) not in REPLAY_DISABLED_ZONES


def drift_thresholds(device_id: str) -> tuple[float, float]:
    return DRIFT_THRESHOLDS_BY_ZONE.get(_zone_of(device_id), DEFAULT_DRIFT_THRESHOLDS)
