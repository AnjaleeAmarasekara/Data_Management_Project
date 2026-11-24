"""API-level tests. The unit tests cover functions; these cover what a browser actually
receives. The alerts page once returned HTTP 500 because a new event kind had no message,
and only a test that calls the endpoint could have caught that.

Tests marked `live` call the running API (python -m uvicorn backend.app.main:app) and
skip cleanly when it is not up.
"""

import os

import pytest
import requests
from pydantic import ValidationError

from backend.app.auth import User, create_token
from backend.app.routers.alerts import LABELS, MESSAGES, describe
from backend.app.routers.ingest import ReadingIn
from backend.app.settings import MissingSecretError, require_secret


API = os.environ.get("API_BASE", "http://localhost:8000/api")


def _api_up() -> bool:
    try:
        return requests.get(f"{API}/health", timeout=3).ok
    except requests.RequestException:
        return False


live = pytest.mark.skipif(not _api_up(), reason="API not running")


class TestAlertDescriptions:
    def test_every_kind_analytics_produces_is_described(self):
        for kind in ("excursion", "cold_excursion", "isolation_forest"):
            assert kind in MESSAGES and kind in LABELS

    def test_cold_message_reports_a_low_not_a_peak(self):
        assert "low -3.0 C" in describe("cold_excursion", 25, -3.0)

    def test_an_unknown_kind_degrades_instead_of_crashing(self):
        assert "mystery_kind" in describe("mystery_kind", 5, 1.0)


class TestSecrets:
    def test_missing_secret_refuses_to_start(self, monkeypatch):
        monkeypatch.delenv("SOME_TEST_SECRET", raising=False)
        with pytest.raises(MissingSecretError):
            require_secret("SOME_TEST_SECRET")

    @pytest.mark.parametrize("value", ["change-me-to-a-random-string", "dev-only-secret-change-me", "short"])
    def test_placeholder_or_short_secret_is_rejected(self, monkeypatch, value):
        monkeypatch.setenv("SOME_TEST_SECRET", value)
        with pytest.raises(MissingSecretError):
            require_secret("SOME_TEST_SECRET")

    def test_a_generated_secret_is_accepted(self, monkeypatch):
        monkeypatch.setenv("SOME_TEST_SECRET", "Zt7pQ2vX9kLm4Rw8Yb3Nc6Hd1Fg5Js0AeUoPi")
        assert require_secret("SOME_TEST_SECRET")


class TestIngestValidation:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_numbers_are_rejected(self, bad):
        with pytest.raises(ValidationError):
            ReadingIn(device_id="BKK-012-MS", temp=bad)

    def test_an_implausible_but_finite_value_is_accepted_for_the_quality_layer_to_judge(self):
        assert ReadingIn(device_id="BKK-012-MS", temp=999.0).temp == 999.0


@pytest.fixture(scope="module")
def session():
    """A session for a real, active administrator. The API rejects tokens for accounts that
    do not exist or have been disabled, so a made-up user would be refused - correctly."""
    import psycopg

    from backend.app.db import DATABASE_URL

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, full_name, role, role_level FROM app_user "
            "WHERE role = 'admin' AND is_active ORDER BY id LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("no active admin account - run backend/seed_users.py")
    # signed with the same secret the server reads from .env, so no password is needed
    token = create_token(User(id=row[0], email=row[1], full_name=row[2], role=row[3], role_level=row[4]))
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    return s


@pytest.fixture(scope="module")
def staff_session():
    """The lowest role. Used to check that read-only pages really are readable by everyone,
    rather than only appearing to be because every test runs as an administrator."""
    import psycopg

    from backend.app.db import DATABASE_URL

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, full_name, role, role_level FROM app_user "
            "WHERE role = 'staff' AND is_active ORDER BY id LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("no active staff account - run backend/seed_users.py")
    token = create_token(User(id=row[0], email=row[1], full_name=row[2], role=row[3], role_level=row[4]))
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    return s


@live
class TestEndpointsLive:

    @pytest.mark.parametrize(
        "path",
        [
            "/units",
            "/alerts?limit=500",
            "/impact",
            "/quality/summary",
            "/quality/network",
            "/reports/summary?start=2017-08-01T00:00:00Z&end=2017-08-07T00:00:00Z",
        ],
    )
    def test_dashboard_endpoints_respond(self, session, path):
        assert session.get(f"{API}{path}", timeout=60).status_code == 200

    def test_every_alert_has_a_readable_message(self, session):
        for alert in session.get(f"{API}/alerts?limit=500", timeout=60).json():
            assert alert["message"] and alert["kind"] in LABELS

    def test_unregistered_device_is_refused(self):
        import psycopg

        from backend.app.db import DATABASE_URL
        from backend.app.routers.ingest import DEVICE_API_KEY

        probe = "test-suite-unregistered-sensor"
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(id), 0) FROM security_event")
            before = cur.fetchone()[0]
        try:
            r = requests.post(
                f"{API}/ingest/readings",
                json={"device_id": probe, "temp": 4.0},
                headers={"X-Device-Key": DEVICE_API_KEY},
                timeout=30,
            )
            assert r.status_code == 404
        finally:
            # the refusal is logged as a security event; a test must not leave fake
            # intrusions in the real security log
            # Only rows this test could have caused: the probe's own refusal, or an
            # auth_failed if the server is running with a different key (e.g. not yet
            # restarted after a rotation).
            with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM security_event WHERE id > %s AND "
                    "(device_id = %s OR (event_type = 'auth_failed' AND device_id IS NULL))",
                    (before, probe),
                )


@live
def test_a_nan_reading_is_rejected_cleanly_not_with_a_server_error():
    """The validator rejected NaN correctly, but echoing NaN in the error body crashed the
    response encoder and returned 500. A rejection must look like a rejection."""
    from backend.app.routers.ingest import DEVICE_API_KEY

    r = requests.post(
        f"{API}/ingest/readings",
        data='{"device_id": "BKK-012-MS", "temp": NaN}',
        headers={"X-Device-Key": DEVICE_API_KEY, "content-type": "application/json"},
        timeout=30,
    )
    assert r.status_code == 422


@live
def test_impact_rows_carry_the_temperature_basis(session):
    """The impact page's "Based on" column once read a field the endpoint never sent, so
    every row silently showed "air". The middle shelf has a product probe and must say so."""
    rows = session.get(f"{API}/impact", timeout=60).json()["rows"]
    assert rows and all(r["temp_basis"] in ("air", "product") for r in rows)
    assert any(r["temp_basis"] == "product" for r in rows)
    assert all("cold_minutes" in r for r in rows)


class TestLiveSilence:
    """The dashboard used to measure "latest" from each sensor's own last reading, so a dead
    sensor kept showing its final values as current."""

    def test_a_channel_behind_its_premises_is_silent(self):
        import pandas as pd
        from backend.app.routers.units import silence

        t = pd.Timestamp("2017-08-05T12:00:00Z")
        q = silence(t - pd.Timedelta(minutes=45), t, t)
        assert q["channel_silent"] and not q["premises_silent"] and q["silent_for_min"] == 45

    def test_a_whole_premises_behind_the_fleet_is_silent(self):
        import pandas as pd
        from backend.app.routers.units import silence

        t = pd.Timestamp("2017-08-05T12:00:00Z")
        q = silence(t - pd.Timedelta(hours=2), t - pd.Timedelta(hours=2), t)
        assert q["premises_silent"] and not q["channel_silent"]

    def test_a_reporting_channel_is_not_silent(self):
        import pandas as pd
        from backend.app.routers.units import silence

        t = pd.Timestamp("2017-08-05T12:00:00Z")
        q = silence(t - pd.Timedelta(minutes=5), t, t)
        assert not q["channel_silent"] and not q["premises_silent"]


@live
class TestTrendsLive:
    def test_trends_cover_the_whole_record_with_full_completeness(self, session):
        d = session.get(f"{API}/trends?device_id=BKK-012-MS&bucket=day", timeout=60).json()
        assert len(d["points"]) == 6
        # the record has no outages, so a partial final day must not read as incomplete
        assert d["totals"]["completeness"] == 1.0
        assert d["has_product_probe"] is True

    def test_trends_report_both_directions_outside_the_band(self, session):
        d = session.get(f"{API}/trends?device_id=BKK-012-TS&bucket=6h", timeout=60).json()
        assert d["totals"]["minutes_too_cold"] > 0  # the top shelf that freezes dairy

    def test_overview_compares_every_unit(self, session):
        from analytics.config import UNITS

        o = session.get(f"{API}/trends/overview?bucket=day", timeout=60).json()
        # every demo unit; units registered later for live probes come after them
        assert [u["device_id"] for u in o["units"]][: len(UNITS)] == list(UNITS)


@live
class TestQualityEvaluationLive:
    """The evaluation figures a reviewer reads on the dashboard must be the measured ones."""

    def test_metrics_match_the_evaluation_on_file(self, session):
        import json
        from backend.app.routers.quality import EVALUATION_FILE

        ev = session.get(f"{API}/quality/evaluation", timeout=60).json()
        on_file = json.loads(EVALUATION_FILE.read_text(encoding="utf-8"))
        assert ev["overall"] == {
            k: on_file["window_level_overall"][k]
            for k in ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "accuracy")
        }
        # precision and recall must be derivable from the matrix shown beside them, or the
        # dashboard would be presenting two numbers that do not describe the same experiment
        o = ev["overall"]
        assert o["precision"] == pytest.approx(o["tp"] / (o["tp"] + o["fp"]), abs=0.001)
        assert o["recall"] == pytest.approx(o["tp"] / (o["tp"] + o["fn"]), abs=0.001)

    def test_every_fault_and_check_is_named_for_a_reader(self, session):
        ev = session.get(f"{API}/quality/evaluation", timeout=60).json()
        assert len(ev["per_fault"]) == 10 and len(ev["per_check"]) == 5
        # internal flag names must not leak through to the page
        assert not any("_flag" in c["check"] for c in ev["per_check"])
        assert not any("_" in f["fault"] for f in ev["per_fault"])

    def test_the_known_blind_spot_is_reported_rather_than_hidden(self, session):
        ev = session.get(f"{API}/quality/evaluation", timeout=60).json()
        subtle = next(f for f in ev["per_fault"] if f["fault"] == "Calibration error (subtle)")
        assert subtle["recall"] == 0.0 and subtle["expected_check"] is None

    def test_staff_can_read_the_evaluation(self, staff_session):
        assert staff_session.get(f"{API}/quality/evaluation", timeout=60).status_code == 200


@live
def test_network_reports_silence(session):
    net = session.get(f"{API}/quality/network", timeout=60).json()
    assert "silent_channels" in net["totals"] and "silent_premises" in net["totals"]


@live
def test_the_silent_sensor_fault_is_detected(session):
    import psycopg

    from backend.app.db import DATABASE_URL

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) FROM audit_log")
        before = cur.fetchone()[0]
    try:
        r = session.post(f"{API}/demo/inject", json={"device_id": "BKK-012-MS", "fault": "sensor_silent"}, timeout=60).json()
        assert r["detected"] is True
        assert r["after"]["score"]["label"] == "Low"
    finally:
        # every demo run is audited; a test run must not add entries to the real audit trail
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM audit_log WHERE id > %s AND action = 'demo_run' AND detail LIKE 'sensor_silent%%'", (before,))
