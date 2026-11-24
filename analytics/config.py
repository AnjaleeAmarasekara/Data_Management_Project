"""Per-unit cold-chain configuration for the analytics layer.

The sensor data is real refrigeration data (see docs/data_linkage.md), so the analytics
layer reads the measured temperature directly. The earlier development dataset was
room-level telemetry, which had to be mapped onto invented setpoints before it could be
treated as cold-chain data; that simulation step is gone.

One analytics row is one sensor sample, five minutes apart (STEP_MINUTES). Durations are
therefore counted in steps and reported in minutes, never by assuming one row per minute.

Safe limits follow common cold-chain practice: frozen goods at or below -15C, chilled
dairy/deli at or below 5C, produce at or below 8C, and beverages, which are drunk rather
than stored long, at or below 10C.
"""

from dataclasses import dataclass

from dq_engine.config import DEFAULT_INTERVAL_S

# One analytics row is one sensor sample. Derived from the quality layer's declared
# sampling interval rather than restated, so the two can never silently disagree: every
# horizon, duration and degree-minute total below is scaled by this number, and a
# mismatch would corrupt all of them without raising an error.
STEP_MINUTES = int(DEFAULT_INTERVAL_S // 60)

# Forecast settings, in steps. 36 steps = 3 hours of history, 6 steps = 30 minutes ahead.
FORECAST_LOOKBACK_STEPS = 36
FORECAST_HORIZON_STEPS = 6

# A day needs this many usable samples to be scored for business impact
# (a complete day holds 288 samples at five-minute spacing).
MIN_DAY_COVERAGE_STEPS = 200

SPOILAGE_DEGREE_MINUTES_SCALE = 400.0  # degree-minutes at which spoilage probability reaches ~63%

# The premises whose units the dashboard and analytics work on. Every other instrumented
# refrigerator is still ingested and scored by the data quality layer, which is what makes
# the scoring run a genuine scalability measurement rather than a claim.
DEMO_SITE_ID = "BKK-012"


@dataclass(frozen=True)
class UnitConfig:
    name: str
    setpoint_c: float
    max_safe_c: float
    categories: tuple  # sales categories stored in this unit (each category maps to one unit)
    # Lower bound, where running too cold is itself a failure. Chilled goods are ruined by
    # freezing: dairy separates, produce suffers chilling injury, eggs crack. This is not a
    # hypothetical - across the 123 refrigerators, top shelves sit at or below 0 C for 28.8%
    # of the time on average, and 53 of them for more than 5% of the time. A system that
    # only watches the upper limit calls all of that "Normal".
    # None means no meaningful lower bound: a freezer being colder than its setpoint wastes
    # energy but does not harm frozen stock, so it is not a cold-chain alert.
    min_safe_c: float | None = None
    # What "now" means for this unit. 'data': a replayed historical record, so silence is
    # judged against the newest reading from its premises and the fleet. 'wall': a live
    # device, so silence is judged against the real clock - a probe that stops reporting
    # at 2 a.m. is silent at 2:30 even if nothing else is reporting either.
    clock: str = "data"
    # Minutes after a live unit's first reading during which temperature limits are not
    # enforced. A probe placed in a fridge starts at room temperature and needs time to
    # settle; without this, every installation opens with a false "Critical". Silence is
    # still enforced during warm-up. Zero for replayed units, which have no installation.
    warmup_min: int = 0


# Zone -> unit mapping for the demo premises. Each perishable category sits in the zone
# that physically stores it, so no category is counted against two units.
#
# Beverages are deliberately absent. They are chilled so they can be sold cold, not to keep
# them safe: a door shelf drifting to 11C costs a sale, not the stock. Counting them as
# spoilage exposure would have put the largest money figure on the dashboard behind the
# least credible claim. They are still monitored, and the door shelf still raises quality
# and excursion alerts; they simply carry no spoilage value.
UNITS = {
    "BKK-012-FC": UnitConfig(
        "Freezer Compartment", -18.0, -15.0, ("Frozen Foods", "Meats", "Seafood", "Poultry")
    ),
    "BKK-012-TS": UnitConfig("Top Shelf Chiller", 4.0, 5.0, ("Dairy",), min_safe_c=0.0),
    "BKK-012-MS": UnitConfig("Middle Shelf Chiller", 4.0, 5.0, ("Deli", "Prepared Foods"), min_safe_c=0.0),
    "BKK-012-VD": UnitConfig("Vegetable Drawer", 6.0, 8.0, ("Produce",), min_safe_c=0.0),
    "BKK-012-DS": UnitConfig("Door Shelf", 7.0, 10.0, ("Eggs",), min_safe_c=0.0),
}

# The product probe measures the temperature of a meat-simulating gel on the middle shelf.
# Spoilage depends on product temperature, not on the air around it, so the business-impact
# model prefers this channel for the unit it sits in. Its position was inferred from the
# data (see ingestion/load_bangkok_sensors.py).
PRODUCT_PROBE_FOR = {"BKK-012-MS": "BKK-012-PT"}
