"""A software stand-in for the ESP32 probe in hardware/esp32_probe: a fridge, a thermostat and a
temperature probe, pushing live readings to the ingestion API exactly as the device does.

    python ingestion/virtual_probe.py LIVE-001-MS                          # normal running, live
    python ingestion/virtual_probe.py LIVE-001-MS --backfill-h 3           # 3 h of history first
    python ingestion/virtual_probe.py LIVE-001-MS --scenario door --fault-at 20
    python ingestion/virtual_probe.py LIVE-001-MS --dry-run --backfill-h 6 # print, send nothing

Why a model and not a replay. simulate_stream.py replays a historical slice under shifted
timestamps; that is a demo of the API, not of a sensor. This simulates the physics instead, so
it can be told what to go wrong: a door left open, a power cut, a thermostat stuck on, a probe
that dies. That makes each detector demonstrable on demand, and shows the same failures the
real probe would produce in a real fridge during the field trial.

The model, per simulated 10 s step:
  * heat leaks in from the room in proportion to the temperature difference (Newton cooling);
    an open door multiplies the leak;
  * the compressor, when running, removes heat, less of it as the air nears the evaporator
    coil's temperature. A thermostat with hysteresis
    switches it: on above setpoint + h, off below setpoint - h, which gives the saw-tooth
    cycle real fridges show;
  * the probe follows the air with a first-order lag (time constant ~4 min, a probe in a
    small housing), so a probe just fitted reads room temperature and settles over minutes -
    the reason registered units have a warm-up period;
  * readings are quantised to the DS18B20's 0.0625 C resolution, plus a little noise.

Like the firmware, it reports every 5 minutes by default, because that is the rate every
quality threshold was calibrated at (dq_engine/config.py), and it holds readings it could not
deliver and sends the backlog as one batch when the API is reachable again.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API_BASE = os.environ.get("API_BASE", "http://localhost:8000/api")
DS18B20_STEP = 0.0625
SIM_STEP_S = 10.0
BACKLOG_LIMIT = 2016  # one week at 5-minute reporting, the same cap as the firmware's flash buffer
BATCH_SIZE = 200

# Thermostat setpoint per compartment. The probe does not know the safe band the dashboard
# judges it by - a real fridge's thermostat does not either.
ZONE_SETPOINT = {"MS": 4.0, "TS": 3.5, "DS": 6.0, "VD": 6.0, "FC": -18.0}

SCENARIOS = {
    "normal": "runs normally",
    "door": "door left open for 25 minutes",
    "power": "power cut: the compressor stops and never restarts",
    "stuck-on": "thermostat stuck on: the compressor never stops and the unit freezes",
    "dies": "probe stops reporting (flat battery, unplugged, lost Wi-Fi)",
    "defrost": "freezer defrost cycle: heater on for 20 minutes",
}


@dataclass
class Fridge:
    setpoint: float = 4.0
    hysteresis: float = 0.8
    ambient: float = 30.0  # a Sri Lankan shop without air conditioning
    leak_per_min: float = 0.004  # fraction of the air-to-room difference gained per minute
    cooling_per_min: float = 0.25  # degrees removed per minute at the setpoint, compressor running
    evaporator_c: float = -8.0  # the coil cannot cool the air below its own temperature
    probe_tau_min: float = 4.0
    door_leak_factor: float = 8.0
    defrost_heat_per_min: float = 0.6
    noise_sd: float = 0.03
    air: float = field(init=False)
    probe: float = field(init=False)
    humidity: float = field(init=False, default=55.0)
    compressor: bool = field(init=False, default=False)

    def __post_init__(self):
        self.air = self.setpoint
        self.probe = self.ambient  # a probe being fitted starts at room temperature

    @classmethod
    def for_zone(cls, zone: str, setpoint: float | None = None) -> "Fridge":
        sp = ZONE_SETPOINT.get(zone, 4.0) if setpoint is None else setpoint
        if zone == "FC":  # a freezer works against a far larger difference to the room
            # thicker insulation, so it leaks heat at half the rate of a chiller
            return cls(setpoint=sp, hysteresis=1.5, leak_per_min=0.002, cooling_per_min=0.45, evaporator_c=-32.0, probe_tau_min=5.0)
        return cls(setpoint=sp)

    def step(self, dt_min: float, *, door_open=False, power=True, stuck_on=False, defrost=False) -> None:
        if stuck_on:
            self.compressor = True
        elif self.air > self.setpoint + self.hysteresis:
            self.compressor = True
        elif self.air < self.setpoint - self.hysteresis:
            self.compressor = False
        running = self.compressor and power and not defrost

        leak = self.leak_per_min * (self.door_leak_factor if door_open else 1.0)
        # cooling weakens as the air nears the coil temperature, so a thermostat stuck on
        # settles a chiller just below freezing rather than at freezer temperatures
        pull = max(0.0, (self.air - self.evaporator_c) / (self.setpoint - self.evaporator_c))
        d_air = leak * (self.ambient - self.air) - (self.cooling_per_min * pull if running else 0.0)
        if defrost:
            d_air += self.defrost_heat_per_min
        self.air += d_air * dt_min
        # the probe lags the air it sits in
        self.probe += (self.air - self.probe) * (1 - math.exp(-dt_min / self.probe_tau_min))
        # an open door lets humid shop air in; a closed, running fridge dries it
        target_rh = 85.0 if door_open else 55.0
        self.humidity += (target_rh - self.humidity) * (1 - math.exp(-dt_min / 6.0))

    def read(self, rng: random.Random) -> tuple[float, float]:
        t = self.probe + rng.gauss(0, self.noise_sd)
        return round(t / DS18B20_STEP) * DS18B20_STEP, round(self.humidity + rng.gauss(0, 0.8), 1)


def conditions(scenario: str, minute: float, fault_at: float) -> dict:
    """What is happening to the fridge at this minute of the run."""
    since = minute - fault_at
    if since < 0 or scenario == "normal":
        return {}
    if scenario == "door":
        return {"door_open": since < 25}
    if scenario == "power":
        return {"power": False}
    if scenario == "stuck-on":
        return {"stuck_on": True}
    if scenario == "defrost":
        return {"defrost": since < 20}
    return {}


def simulate(device_id: str, fridge: Fridge, start: datetime, minutes: float, interval_s: float,
             scenario="normal", fault_at=20.0, rng: random.Random | None = None, t0_min: float = 0.0):
    """Readings from start for `minutes`, one every interval_s. t0_min is the run's elapsed time at
    `start`, so a live run continuing a backfill keeps the fault schedule. A probe that has died
    produces nothing."""
    rng = rng or random.Random()
    out, elapsed, next_report = [], 0.0, 0.0
    while elapsed <= minutes * 60 + 1e-9:
        minute = t0_min + elapsed / 60
        if elapsed >= next_report:
            if not (scenario == "dies" and minute >= fault_at):
                temp, rh = fridge.read(rng)
                out.append({"device_id": device_id, "ts": (start + timedelta(seconds=elapsed)).isoformat(),
                            "temp": temp, "humidity": rh})
            next_report += interval_s
        fridge.step(SIM_STEP_S / 60, **conditions(scenario, minute, fault_at))
        elapsed += SIM_STEP_S
    return out


class Uplink:
    """Store-and-forward, as the firmware does it: every reading joins a queue; the queue is
    sent in batches; anything the network did not take stays queued for the next attempt."""

    def __init__(self, key: str, dry_run=False):
        self.key, self.dry_run, self.queue = key, dry_run, deque(maxlen=BACKLOG_LIMIT)

    def send(self, readings: list[dict]) -> None:
        if len(self.queue) + len(readings) > BACKLOG_LIMIT:
            print(f"  !! backlog full: the oldest {len(self.queue) + len(readings) - BACKLOG_LIMIT} readings are dropped")
        self.queue.extend(readings)
        if self.dry_run:
            for r in self.queue:
                print(f"  {r['ts']}  {r['temp']:7.3f} C  {r['humidity']:5.1f} %RH  (dry run)")
            self.queue.clear()
            return
        while self.queue:
            chunk = [self.queue[i] for i in range(min(BATCH_SIZE, len(self.queue)))]
            try:
                if len(chunk) == 1:
                    r = requests.post(f"{API_BASE}/ingest/readings", json=chunk[0], headers=self._h(), timeout=10)
                else:
                    r = requests.post(f"{API_BASE}/ingest/readings/batch", json={"readings": chunk}, headers=self._h(), timeout=30)
            except requests.RequestException as exc:
                print(f"  .. API unreachable ({type(exc).__name__}); holding {len(self.queue)} reading(s)")
                return
            if r.status_code >= 500:
                print(f"  .. API error {r.status_code}; holding {len(self.queue)} reading(s)")
                return
            if r.status_code >= 400:
                # refused for who we are, not for the network: retrying would only fill the
                # security log, so stop and say why
                raise SystemExit(f"  !! refused {r.status_code}: {r.text}")
            for _ in chunk:
                self.queue.popleft()
            last = chunk[-1]
            label = f"batch of {len(chunk)}" if len(chunk) > 1 else "sent"
            print(f"  {last['ts']}  {last['temp']:7.3f} C  {last['humidity']:5.1f} %RH  {label}", flush=True)

    def _h(self):
        return {"X-Device-Key": self.key}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 epilog="scenarios: " + "; ".join(f"{k} = {v}" for k, v in SCENARIOS.items()))
    ap.add_argument("device_id", help="a registered device ID, e.g. LIVE-001-MS (register it on the Devices page first)")
    ap.add_argument("--scenario", choices=SCENARIOS, default="normal")
    ap.add_argument("--fault-at", type=float, default=20.0, help="minutes into the run when the fault starts (default 20)")
    ap.add_argument("--setpoint", type=float, help="thermostat setpoint; default from the zone suffix")
    ap.add_argument("--interval", type=float, default=300.0, help="seconds between readings (default 300, the calibrated rate)")
    ap.add_argument("--backfill-h", type=float, default=0.0,
                    help="first send this many hours of history ending now, as one backlog (like a probe coming back online)")
    ap.add_argument("--fitted-cold", action="store_true", help="the probe was already in the fridge: no settling from room temperature")
    ap.add_argument("--count", type=int, help="stop after this many live readings")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--dry-run", action="store_true", help="print readings instead of sending them")
    args = ap.parse_args()

    device_id = args.device_id.strip().upper()
    zone = device_id.rsplit("-", 1)[-1]
    if zone not in ZONE_SETPOINT:
        raise SystemExit(f"{device_id}: the last part must be a compartment code, one of {', '.join(ZONE_SETPOINT)}")
    if args.interval != 300:
        print(f"Note: reporting every {args.interval:g} s. The quality thresholds are calibrated for 300 s, so noise "
              "and replay checks will behave differently.")

    key = "dry-run"
    if not args.dry_run:
        from backend.app.settings import require_secret
        key = require_secret("DEVICE_API_KEY")
    uplink = Uplink(key, dry_run=args.dry_run)
    rng = random.Random(args.seed)
    fridge = Fridge.for_zone(zone, args.setpoint)
    if args.fitted_cold:
        fridge.probe = fridge.air

    print(f"{device_id}: setpoint {fridge.setpoint:g} C, scenario '{args.scenario}' ({SCENARIOS[args.scenario]})"
          + (f" from minute {args.fault_at:g}" if args.scenario != "normal" else ""))

    elapsed_min = 0.0
    if args.backfill_h > 0:
        minutes = args.backfill_h * 60
        # ends one interval before now, so the first live reading follows on schedule
        start = datetime.now(timezone.utc) - timedelta(minutes=minutes, seconds=args.interval)
        history = simulate(device_id, fridge, start, minutes, args.interval, args.scenario, args.fault_at, rng)
        print(f"Backfilling {len(history)} readings from {start:%H:%M} UTC")
        uplink.send(history)
        elapsed_min = minutes + args.interval / 60  # the next reading is one interval after the last

    if args.dry_run and args.count is None:
        args.count = 0 if args.backfill_h > 0 else 12  # a dry run is a preview, not a service

    sent = 0
    try:
        while args.count is None or sent < args.count:
            if args.scenario == "dies" and elapsed_min >= args.fault_at:
                print(f"  (probe silent: minute {elapsed_min:.0f})", flush=True)
            else:
                temp, rh = fridge.read(rng)
                ts = datetime.now(timezone.utc) + (timedelta(minutes=elapsed_min) if args.dry_run else timedelta(0))
                uplink.send([{"device_id": device_id, "ts": ts.isoformat(), "temp": temp, "humidity": rh}])
            sent += 1
            # advance the physics through the interval while waiting for the next report
            for _ in range(int(args.interval / SIM_STEP_S)):
                fridge.step(SIM_STEP_S / 60, **conditions(args.scenario, elapsed_min, args.fault_at))
                elapsed_min += SIM_STEP_S / 60
            if args.count is None or sent < args.count:
                time.sleep(0 if args.dry_run else args.interval)
    except KeyboardInterrupt:
        print(f"\nStopped. {len(uplink.queue)} reading(s) still unsent.")


if __name__ == "__main__":
    main()
