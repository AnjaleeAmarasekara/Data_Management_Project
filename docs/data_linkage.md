# Dataset Linkage Design

The two datasets used for development (real John Keells / SME data being confidential,
per the proposal) do not share a natural key. This document records the modeling
decisions made to integrate them, so they can be defended at review rather than
discovered by a reader of the code.

## The datasets as they actually are

- **Environmental Sensor Telemetry** (Kaggle, `garystafford/environmental-sensor-data-132k`):
  405,184 readings from **3 physical devices**, sampled roughly every 4-5 seconds, spanning
  **2020-07-12 to 2020-07-20** (8 days). Columns include `temp` and `humidity` (used here)
  plus `co`, `lpg`, `smoke`, `motion`, `light` (gas/air-quality signals from the source
  dataset's original use case, carried through the schema for provenance but not used by
  the data quality or analytics layers).
- **Supermarket Sales** (Kaggle, `zoroxide/supermarket-sales`): 150 transactions spanning
  **March 2024**, with `Product Category`, `Unit Price`, `Quantity`, `Total Sales`. There
  is no branch/store column and no sensor/device reference.

## Modeling decisions

1. **Devices → cold-storage units, one branch.** Since there's no branch field in the
   sales data, the 3 devices are modeled as 3 refrigeration units within a single
   retail branch, rather than 3 separate branches:
   - `b8:27:eb:bf:9d:51` → Freezer Unit 1
   - `00:0f:00:70:91:0a` → Dairy Fridge Unit 2
   - `1c:bf:ce:15:ec:4d` → Walk-in Cooler Unit 3
   This mapping lives in `sensor_unit` and is assigned at ingestion time in
   `ingestion/load_sensor_data.py`.

2. **Date alignment via relative day index, not calendar date.** The sensor data (2020)
   and sales data (2024) don't overlap in real time — expected, since they're unrelated
   public datasets standing in for confidential real data. Joining them meaningfully
   requires a synthetic timeline: each dataset's dates are re-indexed to
   `relative_day = (date - dataset_min_date).days + 1`, so both datasets start at day 1.
   Sensor data covers relative days 1-8; sales data covers relative days 1-31. Business
   impact analysis only uses the overlapping window (days 1-8), i.e. it evaluates "if
   this day's cold-chain conditions happened alongside this day's sales volume."
   This is stated explicitly here and in the report as a **modeled, not observed**,
   linkage.

3. **Categories → units.** Each perishable sales category is stored in exactly one unit,
   so no sale is counted twice: `Vegetables` -> Freezer Unit 1, `Dairy` -> Dairy Fridge
   Unit 2, `Fruits` and `Beverages` -> Walk-in Cooler Unit 3. `Snacks` and `Bakery` are
   ambient-shelf items and excluded from cold-chain exposure.

4. **Setpoint mapping (analytics layer only).** The "Freezer" device actually reads about
   22 C, because the source data comes from room-level environmental sensors, not
   refrigeration. The analytics layer therefore works on each unit's *deviation from its
   own baseline* (median of its clean readings) mapped onto a realistic setpoint:
   `cold_temp = setpoint + (temp - baseline)`, with setpoints of -18 C (freezer), 3 C
   (dairy fridge) and 2 C (walk-in cooler) and safe limits of -15 C, 5 C and 5 C
   (`analytics/config.py`). This is a simulation choice, labelled as such on the dashboard.
   Raw readings and the data quality layer never see it.

## What the data quality layer found in the real data

- Under 0.3% of readings on two of the three devices are isolated glitches (temperature
  reads 0-7 C while the surrounding readings sit near 20 C). They are flagged row by row,
  excluded from analytics, and shown as red markers on the dashboard.
- The third device (the "Freezer") is clean.
- Nearly all 30-minute windows score High. That is an honest result, not a tuning artefact:
  the fault-injection demo (dashboard -> Data Quality) is what shows the layer reacting to
  drift, dropouts, noise and gross calibration errors.

## Business impact model

For each unit and relative day:

```
degree_minutes       = sum over minutes of max(0, cold_temp - max_safe)
spoilage_probability = 1 - exp(-degree_minutes / 400)
revenue_at_risk      = sales_exposed * spoilage_probability
```

where `sales_exposed` is that day's sales of the categories stored in the unit. Each
estimate also carries the day's average data trust, so a large number resting on
low-trust data is visibly less reliable. This is a documented heuristic for demonstrating
the enterprise-data-integration idea end to end -- not a calibrated spoilage model, exactly
as the proposal notes that real industry data was unavailable and Kaggle substitutes were
used instead.

## Known limitations (worth stating in the report)

- **Subtle calibration bias is invisible to single-sensor checks.** A constant offset that
  stays inside the plausible range shifts the whole window uniformly, so window statistics
  cannot tell it from a genuinely warmer unit. Catching it needs cross-sensor comparison or
  periodic reference calibration. `test_moderate_calibration_bias_is_a_known_blind_spot`
  pins this down, and the dashboard demo shows it as "Not detected".
- Forecasting is a linear trend over the last 30 minutes; ARIMA (proposal ref [6]) is the
  planned upgrade for the analytics milestone.
- Spoilage probability is a heuristic (above), and the sensor/sales linkage is modeled.
- Login, role-based access and full report export are not built yet; alerts and impact
  tables export to CSV.
