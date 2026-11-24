"""The virtual probe stands in for the ESP32 in demos and tests, so its fridge has to behave
like a fridge: hold its band when healthy, and fail in the recognisable way each scenario names.
Seeded, so every assertion is about the model, not about luck."""

import random
from datetime import datetime, timezone

import pytest
import requests

from ingestion.virtual_probe import DS18B20_STEP, Fridge, Uplink, simulate

START = datetime(2026, 11, 19, tzinfo=timezone.utc)


def run(zone="MS", scenario="normal", hours=4.0, fault_at=60.0, interval=300.0):
    r = simulate(f"LIVE-001-{zone}", Fridge.for_zone(zone), START, hours * 60, interval, scenario, fault_at, random.Random(7))
    return r, [x["temp"] for x in r]


def test_reports_every_five_minutes_with_utc_timestamps():
    r, _ = run(hours=1)
    assert len(r) == 13
    assert r[1]["ts"] == "2026-11-19T00:05:00+00:00"


def test_a_probe_just_fitted_reads_room_temperature_then_settles():
    """Why registered units get a warm-up: the first reading is the shop, not the fridge."""
    _, t = run(hours=1)
    assert t[0] > 25
    assert 3 <= t[6] <= 5  # within 30 minutes it reads the fridge


def test_a_healthy_chiller_cycles_inside_its_band():
    _, t = run(hours=6)
    settled = t[6:]
    assert 0 < min(settled) and max(settled) < 5
    assert max(settled) - min(settled) > 0.5  # it cycles; a flat line would look like a replay


def test_readings_have_the_ds18b20_resolution():
    _, t = run(hours=1)
    assert all(abs(v / DS18B20_STEP - round(v / DS18B20_STEP)) < 1e-9 for v in t)


@pytest.mark.parametrize("scenario, check", [
    ("door", lambda after: max(after) > 8 and after[-1] < 5),  # breaches, then recovers
    ("power", lambda after: after[-1] > 10 and after[-1] == max(after)),  # warms and keeps warming
    ("stuck-on", lambda after: min(after) < 0),  # too cold: freezes produce
])
def test_each_chiller_fault_looks_like_itself(scenario, check):
    _, t = run(scenario=scenario)
    assert check(t[12:])


def test_a_dead_probe_stops_sending():
    r, _ = run(scenario="dies", fault_at=60)
    assert len(r) == 12  # nothing after minute 60


def test_a_freezer_holds_and_its_defrost_is_a_visible_excursion():
    _, normal = run(zone="FC")
    assert max(normal[6:]) < -15
    _, defrost = run(zone="FC", scenario="defrost")
    assert max(defrost[12:]) > -15  # what an abnormal-defrost detector has to tell apart from a failure
    assert defrost[-1] < -15


class _Resp:
    def __init__(self, code):
        self.status_code, self.text = code, ""


def test_store_and_forward_holds_readings_until_the_api_is_back(monkeypatch):
    calls = []

    def down(*a, **kw):
        raise requests.ConnectionError("refused")

    up = Uplink("k")
    monkeypatch.setattr(requests, "post", down)
    r, _ = run(hours=0.5)
    up.send(r[:3])
    up.send(r[3:4])
    assert len(up.queue) == 4

    def ok(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return _Resp(201)

    monkeypatch.setattr(requests, "post", ok)
    up.send(r[4:5])
    assert not up.queue
    assert calls[0][0].endswith("/batch") and len(calls[0][1]["readings"]) == 5
    assert [x["ts"] for x in calls[0][1]["readings"]] == [x["ts"] for x in r[:5]]  # original times, in order


def test_a_refusal_stops_the_probe_instead_of_retrying(monkeypatch):
    """A 404 means unregistered, not unlucky: retrying would fill the security log."""
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _Resp(404))
    r, _ = run(hours=0.1)
    with pytest.raises(SystemExit):
        Uplink("k").send(r[:1])
