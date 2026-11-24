"""Unit registry, wall-clock silence and warm-up.

The first live feature after the mid-term review: a real probe in a real fridge has to be
registered without editing code, judged against the real clock when it goes quiet, and
given time to settle after it is fitted instead of opening with a false "Critical".

Tests marked `live` need the running API and database, and remove everything they write.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import requests
from pydantic import ValidationError

from analytics.config import UNITS, UnitConfig
from analytics.registry import load_units
from backend.app.routers.admin import RegisterUnitRequest, UnitLimits
from backend.app.routers.units import silence, warmup
from backend.tests.test_api import API, live, session, staff_session  # noqa: F401 - fixtures

NOW = pd.Timestamp("2026-11-18T10:00:00Z")
LIVE_UNIT = UnitConfig("Shop fridge", 4.0, 5.0, ("Dairy",), min_safe_c=0.0, clock="wall", warmup_min=30)


class TestWallClockSilence:
    def test_a_lone_live_probe_that_stopped_is_silent(self):
        """Judged against its siblings, a single probe is always level with itself and could
        never look silent. Against the wall clock, 45 minutes of nothing is silence."""
        last = NOW - pd.Timedelta(minutes=45)
        q = silence(last, last, last, now=NOW)
        assert q["channel_silent"] and q["premises_silent"] and q["silent_for_min"] == 45
        assert q["clock"] == "wall"

    def test_the_same_probe_by_the_replay_rule_would_look_fine(self):
        last = NOW - pd.Timedelta(minutes=45)
        q = silence(last, last, last)
        assert not q["channel_silent"] and not q["premises_silent"]

    def test_a_probe_reporting_on_schedule_is_not_silent(self):
        last = NOW - pd.Timedelta(minutes=4)
        q = silence(last, last, last, now=NOW)
        assert not q["channel_silent"] and not q["premises_silent"]


class TestWarmup:
    def test_active_until_the_settling_time_has_passed(self):
        w = warmup(LIVE_UNIT, NOW - pd.Timedelta(minutes=10), NOW)
        assert w is not None and w["remaining_min"] == 20

    def test_over_after_the_settling_time(self):
        assert warmup(LIVE_UNIT, NOW - pd.Timedelta(minutes=31), NOW) is None

    def test_replayed_units_never_warm_up(self):
        assert warmup(UNITS["BKK-012-MS"], NOW - pd.Timedelta(minutes=1), NOW) is None

    def test_zero_minutes_disables_it(self):
        cfg = UnitConfig("x", 4.0, 5.0, (), clock="wall", warmup_min=0)
        assert warmup(cfg, NOW, NOW) is None


class _FakeConn:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.rolled_back = rows or [], error, False

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                if conn.error:
                    raise conn.error

            def fetchall(self):
                return conn.rows

        return Cur()

    def rollback(self):
        self.rolled_back = True


class TestRegistryMerge:
    def test_registered_units_follow_the_demo_units(self):
        row = ("LIVE-001-MS", "Shop fridge", 4.0, 5.0, 0.0, ["Dairy"], "wall", 30)
        units = load_units(_FakeConn([row]))
        assert list(units)[:5] == list(UNITS) and list(units)[5] == "LIVE-001-MS"
        assert units["LIVE-001-MS"].clock == "wall" and units["LIVE-001-MS"].warmup_min == 30

    def test_a_database_row_overrides_a_demo_units_limits(self):
        row = ("BKK-012-TS", "Top Shelf Chiller", 3.0, 4.5, 0.5, ["Dairy"], "data", 30)
        cfg = load_units(_FakeConn([row]))["BKK-012-TS"]
        assert cfg.max_safe_c == 4.5 and cfg.warmup_min == 0  # replayed units never warm up

    def test_an_unmigrated_database_falls_back_to_the_demo_units(self):
        conn = _FakeConn(error=RuntimeError('column "monitored" does not exist'))
        assert load_units(conn) == dict(UNITS) and conn.rolled_back


class TestRegistrationValidation:
    def _req(self, **kw):
        base = dict(device_id="live-001-ms", unit_name="Shop fridge", site_id="LIVE-001", site_name="Corner shop",
                    setpoint_c=4.0, max_safe_c=5.0, min_safe_c=0.0)
        return RegisterUnitRequest(**{**base, **kw})

    def test_device_id_is_normalised(self):
        assert self._req().device_id == "LIVE-001-MS"

    @pytest.mark.parametrize("bad", ["LIVE-001", "LIVE-001-PT", "LIVE-001-AM", "LIVE 001 MS", "../etc-MS"])
    def test_device_id_must_end_in_a_cold_storage_zone(self, bad):
        """The quality layer picks its thresholds from the zone suffix, and PT/AM are
        reference channels with no safe band, so neither can be a monitored unit."""
        with pytest.raises(ValidationError):
            self._req(device_id=bad)

    @pytest.mark.parametrize("limits", [dict(max_safe_c=4.0), dict(max_safe_c=3.0), dict(min_safe_c=4.0), dict(min_safe_c=6.0)])
    def test_the_band_must_contain_the_setpoint(self, limits):
        with pytest.raises(ValidationError):
            self._req(**limits)

    def test_a_freezer_needs_no_lower_bound(self):
        assert UnitLimits(setpoint_c=-18, max_safe_c=-15).min_safe_c is None


# ------------------------------------------------------------------ live

TEST_SITE = "ZZTEST"


@pytest.fixture
def cleanup():
    """Everything a live test writes - units, readings, audit rows - is removed afterwards,
    so the test suite never leaves fake fridges or fake admin actions in the real record."""
    import psycopg

    from backend.app.db import DATABASE_URL

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) FROM audit_log")
        audit_before = cur.fetchone()[0]
        cur.execute("SELECT coalesce(max(id), 0) FROM security_event")
        security_before = cur.fetchone()[0]
    yield
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        like = f"{TEST_SITE}-%"
        cur.execute("DELETE FROM sensor_reading WHERE device_id LIKE %s", (like,))
        cur.execute("DELETE FROM sensor_unit WHERE device_id LIKE %s", (like,))
        cur.execute("DELETE FROM audit_log WHERE id > %s AND target LIKE %s", (audit_before, like))
        cur.execute("DELETE FROM security_event WHERE id > %s AND device_id LIKE %s", (security_before, like))


def _register(session, device_id, **kw):
    body = dict(device_id=device_id, unit_name="Test fridge", site_id=device_id.rsplit("-", 1)[0], site_name="Test shop",
                setpoint_c=4.0, max_safe_c=5.0, min_safe_c=0.0, categories=["Dairy"], warmup_min=30)
    return session.post(f"{API}/admin/units", json={**body, **kw}, timeout=30)


def _push(device_id, temp, ts=None):
    from backend.app.routers.ingest import DEVICE_API_KEY

    body = {"device_id": device_id, "temp": temp, "humidity": 50.0}
    if ts is not None:
        body["ts"] = ts.isoformat()
    r = requests.post(f"{API}/ingest/readings", json=body, headers={"X-Device-Key": DEVICE_API_KEY}, timeout=30)
    assert r.status_code == 201, r.text


def _state(session, device_id):
    return next(u for u in session.get(f"{API}/units", timeout=120).json() if u["device_id"] == device_id)


@live
@pytest.mark.usefixtures("cleanup")
class TestRegistryLive:
    def test_a_registered_probe_can_push_and_appears_on_the_dashboard(self, session):
        dev = f"{TEST_SITE}-A-MS"
        assert _register(session, dev).status_code == 201
        assert _state(session, dev)["status"] == "no_data"
        # a probe fitted a minute ago still reads room temperature
        _push(dev, 24.0)
        s = _state(session, dev)
        assert s["status"] == "warming_up" and s["breach"] is None and s["warmup"]["remaining_min"] > 25
        assert s["forecast"]["reason"] == "warming up"
        # the fall from room temperature is not judged as noise; there is no verdict yet
        assert s["trust"] is None

    def test_live_readings_do_not_make_the_replayed_units_look_silent(self, session):
        """A live probe's readings carry today's date. When they counted as the fleet's
        newest reading, every replayed unit showed "Whole premises silent for 4,883,983 min"."""
        dev = f"{TEST_SITE}-H-MS"
        _register(session, dev)
        _push(dev, 4.0)
        states = session.get(f"{API}/units", timeout=120).json()
        for s in states:
            if s["device_id"] in UNITS:
                assert s["connectivity"]["premises_silent"] is False, s["device_id"]

    def test_a_warm_probe_after_warm_up_is_critical(self, session):
        dev = f"{TEST_SITE}-B-MS"
        _register(session, dev, warmup_min=0)
        _push(dev, 9.0)
        s = _state(session, dev)
        assert s["status"] == "critical" and s["breach"] == "too_warm"

    def test_a_probe_that_went_quiet_is_silent_by_the_wall_clock(self, session):
        dev = f"{TEST_SITE}-C-MS"
        _register(session, dev)
        _push(dev, 4.0, ts=datetime.now(timezone.utc) - timedelta(minutes=50))
        s = _state(session, dev)
        # silence outranks warm-up: a probe that dies while settling is still dead
        assert s["status"] == "critical" and s["breach"] == "premises_silent"
        assert s["connectivity"]["silent_for_min"] >= 49

    def test_a_new_unit_is_not_marked_down_for_time_before_it_existed(self, session):
        dev = f"{TEST_SITE}-D-MS"
        _register(session, dev, warmup_min=0)
        now = datetime.now(timezone.utc)
        for m in (10, 5, 0):
            _push(dev, 4.0, ts=now - timedelta(minutes=m))
        s = _state(session, dev)
        assert s["trust"]["flags"]["missing"] is False

    def test_duplicates_and_bad_bands_are_refused(self, session):
        dev = f"{TEST_SITE}-E-MS"
        assert _register(session, dev).status_code == 201
        assert _register(session, dev).status_code == 409
        assert session.patch(f"{API}/admin/units/{dev}", json={"max_safe_c": 3.0}, timeout=30).status_code == 422

    def test_limit_changes_are_audited(self, session):
        dev = f"{TEST_SITE}-F-MS"
        _register(session, dev)
        r = session.patch(f"{API}/admin/units/{dev}", json={"max_safe_c": 6.0}, timeout=30)
        assert r.status_code == 200 and r.json()["max_safe_c"] == 6.0
        audit = session.get(f"{API}/admin/audit?limit=20", timeout=30).json()
        assert any(a["action"] == "unit_limits_changed" and a["target"] == dev and "-> " in a["detail"] for a in audit)

    def test_staff_cannot_register_units(self, staff_session):
        assert _register(staff_session, f"{TEST_SITE}-G-MS").status_code == 403
