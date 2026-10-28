"""Per-unit cold-chain configuration for the analytics layer.

The source sensor data comes from room-level environmental sensors (units read ~20-26C),
not from real refrigeration. So the analytics layer works on each unit's *deviation from
its own baseline* and maps it onto a realistic setpoint for the unit type it models
(cold_temp = setpoint + (temp - baseline)). The mapping is a documented simulation
choice (docs/data_linkage.md); the raw readings and the data quality layer never see it.

max_safe_c follows common cold-chain limits: frozen goods at or below -15C, dairy at or
below 5C, chilled produce/beverages at or below 5C.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitConfig:
    name: str
    setpoint_c: float
    max_safe_c: float
    categories: tuple  # sales categories stored in this unit (each category maps to one unit)

    def to_cold(self, temp, baseline):
        return self.setpoint_c + (temp - baseline)


UNITS = {
    "b8:27:eb:bf:9d:51": UnitConfig("Freezer Unit 1", -18.0, -15.0, ("Vegetables",)),
    "00:0f:00:70:91:0a": UnitConfig("Dairy Fridge Unit 2", 3.0, 5.0, ("Dairy",)),
    "1c:bf:ce:15:ec:4d": UnitConfig("Walk-in Cooler Unit 3", 2.0, 5.0, ("Fruits", "Beverages")),
}

MIN_DAY_COVERAGE_MIN = 360  # a sensor day needs this many usable minutes to be scored
SPOILAGE_DEGREE_MINUTES_SCALE = 400.0  # degree-minutes at which spoilage probability reaches ~63%
