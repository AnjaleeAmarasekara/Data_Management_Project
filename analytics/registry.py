"""The unit registry: which cold-storage units are monitored, and with what limits.

Two sources, merged:
- analytics/config.py UNITS - the demo premises, whose limits sit in code next to the
  measurements that justify them;
- sensor_unit rows with monitored = TRUE - units registered after deployment through the
  admin API, such as the live probe in a real fridge. Nobody should have to edit code on
  site to add a fridge.

A database row for a demo unit overrides its code default, so limits can be tuned without
a redeploy. Every caller that needs "the units" goes through load_units(); reading UNITS
directly would silently leave registered units out of the dashboard.
"""

import logging

from analytics.config import UNITS, UnitConfig

log = logging.getLogger(__name__)

# Device ids end in a zone code, and the quality layer picks its thresholds from that code
# (dq_engine/config.py). Only cold-storage zones can be monitored units: the product probe
# (PT) and ambient (AM) channels are references, with no safe band of their own.
MONITORABLE_ZONES = {
    "FC": "freezer",
    "TS": "chiller",
    "MS": "chiller",
    "VD": "crisper",
    "DS": "door",
}

_SELECT = """
    SELECT device_id, unit_name, setpoint_c, max_safe_c, min_safe_c, categories, clock, warmup_min
    FROM sensor_unit
    WHERE monitored
    ORDER BY registered_at, device_id
"""


def _from_row(row) -> UnitConfig:
    _, name, setpoint, max_safe, min_safe, categories, clock, warmup = row
    return UnitConfig(
        name=name,
        setpoint_c=setpoint,
        max_safe_c=max_safe,
        categories=tuple(categories or ()),
        min_safe_c=min_safe,
        clock=clock,
        warmup_min=warmup if clock == "wall" else 0,
    )


def load_units(conn) -> dict[str, UnitConfig]:
    """Every monitored unit, demo units first, in a stable order."""
    units = dict(UNITS)
    try:
        with conn.cursor() as cur:
            cur.execute(_SELECT)
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - a missing migration must not take the dashboard down
        # Before db/migrations/001_unit_registry.sql is applied the columns do not exist.
        # The demo units still work from code; registered units cannot exist yet anyway.
        conn.rollback()
        log.warning("unit registry unavailable, using code defaults only: %s", exc)
        return units
    for row in rows:
        units[row[0]] = _from_row(row)
    return units


def unit_names(conn) -> dict[str, str]:
    """device_id -> display name for every channel, monitored or not. Alerts and reports
    look names up here so an event on a unit that was later unregistered still renders."""
    with conn.cursor() as cur:
        cur.execute("SELECT device_id, unit_name FROM sensor_unit")
        names = dict(cur.fetchall())
    names.update({d: u.name for d, u in load_units(conn).items()})
    return names
