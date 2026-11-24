# ESP32 live probe

A real temperature probe in a real fridge, pushing to the ingestion API every five minutes.
It is the hardware half of the live sensor feed. `ingestion/virtual_probe.py` is the software
half: it simulates the same device and is useful when there is no board to hand.

## Parts (about LKR 3,000)

| Part | Notes |
|---|---|
| ESP32 DevKit (ESP32-WROOM-32) | any board the Arduino ESP32 core supports |
| DS18B20 waterproof probe, 1 m lead | stainless tip; the lead runs under the door seal |
| 4.7 kΩ resistor | pull-up for the 1-Wire data line |
| USB cable and 5 V supply | a phone charger is enough |

## Wiring

```
DS18B20 red    -> 3V3
DS18B20 black  -> GND
DS18B20 yellow -> GPIO 4
4.7 kΩ resistor between GPIO 4 and 3V3
```

Without the pull-up the probe reads -127 °C. The firmware drops that value and the unit then
shows as silent on the dashboard, which is the correct verdict for a probe that is not working.

## Flashing

1. Arduino IDE 2: install the **esp32** board package by Espressif. Then use Library Manager
   to install **OneWire** and **DallasTemperature**.
2. Copy `config.example.h` to `config.h` and fill it in. `config.h` is git-ignored because it
   holds the Wi-Fi password and the device key.
3. Board: *ESP32 Dev Module*. Upload, then open the Serial Monitor at 115200 baud.

## Before the first reading

1. **Register the probe.** On the dashboard's **Devices** page (admin), register it with the
   same device ID that is in `config.h`. An unregistered device is refused with a 404 and
   logged as a security event.
2. **Make the API reachable from Wi-Fi.** By default uvicorn listens only on the computer
   itself. Start it with:
   ```
   python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
   ```
   Then allow port 8000 through Windows Firewall for private networks. In `config.h`, set
   `API_BASE` to that computer's address on the Wi-Fi network (find it with `ipconfig`).

A healthy start looks like this on the Serial Monitor:

```
ColdSense probe LIVE-001-MS -> http://192.168.1.20:8000/api
sampled 27.8125 C, 1 buffered
sent 1, 0 still buffered
```

## Fitting it in the fridge

- Put the tip in the **air** of the compartment the device ID names (MS: middle shelf),
  away from the walls and the evaporator. Do not put it in a bottle of glycol. A buffered
  probe is far more damped, so it would look like a different kind of sensor to the
  quality checks, which were calibrated on air-temperature loggers.
- Run the lead under the door seal at the hinge side, where the seal is least disturbed.
- For the first 30 minutes (the warm-up set at registration) the dashboard shows
  **Warming up** instead of judging the unit. The probe is still coming down from room
  temperature.

## What happens when the network goes down

The firmware keeps every reading in a buffer until the API confirms it, with a 24-hour
capacity at five-minute reporting. When Wi-Fi comes back, the backlog goes up in batches with
its original timestamps, and the dashboard fills the gap. While the link is down the
dashboard shows the unit as silent: it cannot know the fridge is fine, and it does not
pretend to.

The buffer lives in RAM, so a power cut loses it. Keeping it in flash, giving each device its
own key and signing every reading are the next Gantt item (edge store-and-forward and
signed readings).
