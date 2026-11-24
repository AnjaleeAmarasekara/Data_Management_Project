# Dataset Selection and Linkage Design

Real Sri Lankan SME cold-chain data is confidential (per the proposal), so development uses
public datasets as stand-ins. This document records which datasets were chosen, why, and
every modelling decision made to join them — so they can be defended at review rather than
discovered by a reader of the code.

## Why these datasets were chosen

An earlier version of this project used the Kaggle *Environmental Sensor Telemetry* dataset
(405,184 readings, 3 devices) with *Supermarket Sales* (150 transactions). Both were
replaced, for reasons worth stating plainly:

| Problem with the earlier pair | Consequence |
|---|---|
| The sensors were room-level environmental monitors reading 20–26 °C | Nothing in the data was a cold chain. Fridge temperatures had to be *simulated* by mapping each device onto an invented setpoint |
| The sales data had 150 transactions, no store column, dated 2024 vs sensors dated 2020 | The join was entirely synthetic |
| Product categories were generic | "Dairy goes in the dairy fridge" was an arbitrary assignment |

The replacements remove the simulation entirely and make the category-to-unit mapping
physical rather than arbitrary.

### Sensor data: Bangkok domestic refrigerators

Paviet-Salomon, Y. et al., *Dataset of household cold-chain conditions: temperature and
relative humidity monitoring in domestic refrigerators in Bangkok, Thailand*, Recherche
Data Gouv (INRAE), <https://doi.org/10.57745/TMWYBQ>. Licence **CC BY-NC-SA 4.0**
(non-commercial — fine for coursework, not for selling the system).

- 123 instrumented refrigerators: 41 single-door, 77 two-door top-freezer, 5 two-door
  bottom-freezer.
- Seven sensor channels per fridge, **all seven now ingested**: five cold zones (freezer
  compartment, top shelf, middle shelf, vegetable drawer, door shelf) plus two reference
  channels, ambient air and a meat-simulating gel that measures product temperature.
- TagPlus-TH loggers, 5-minute interval, 0.1 °C resolution, ±0.5 °C expanded uncertainty.
- 1,469 samples per fridge (5 days retained after stabilisation).

Why it suits a Sri Lankan SME:

- **It is actually refrigeration.** Freezer compartments sit near −20 °C and chilled
  shelves at 1–7 °C, so the analytics layer reads measured temperature directly. The
  setpoint simulation is gone.
- **Tropical ambient conditions.** Ambient air averages 22–33 °C across the fleet, which is
  the condition Sri Lankan cold equipment actually fights against — unlike a temperate-climate
  dataset.
- **Small cabinets, not industrial walk-ins.** A Sri Lankan grocery, pharmacy or café runs
  exactly this class of equipment.
- **Real failures are already in the data.** Across the fleet, vegetable drawers reach
  26.5 °C and door shelves 28.8 °C; many freezers never get below −10 °C.

Honest caveat: these are *household* refrigerators, not retail display cases. They stand in
for the small cabinets an SME uses, and that substitution is stated on the dashboard.

### Sales data: Favorita store sales

*Store Sales — Time Series Forecasting* (Corporación Favorita, Ecuador), via Kaggle.
54 stores, daily sales per product family, 2013-01-01 to 2017-08-15 (3,000,888 rows).
Licence: Kaggle competition terms — academic use with citation, not a clean CC licence.

Chosen because it carries **real, named perishable families**: DAIRY, MEATS, POULTRY,
SEAFOOD, PRODUCE, FROZEN FOODS, EGGS, DELI, PREPARED FOODS, BEVERAGES. Named families are
what make the category-to-zone mapping physical instead of arbitrary.

Honest caveat: Ecuador, not Sri Lanka. It is a tropical, comparable-income retail market,
which is the closest open equivalent found; no Sri Lankan transaction-level retail dataset
is publicly available (the Department of Census and Statistics publishes retail *prices*,
not sales volumes).

### Datasets considered and rejected

- **FreshRetailNet-50K** (898 stores, hourly, 865 perishable SKUs, CC BY 4.0). Larger and
  finer-grained than Favorita, but every category is an anonymised integer ID, so the
  dashboard would have to say "Category 5" instead of "Dairy".
- **Uganda IoT cold-storage fruit spoilage** (10,996 rows with Good/Bad spoilage labels,
  CC BY 4.0). Tempting, because real spoilage labels would allow a trained classifier. Rejected:
  its temperatures are 21–27 °C, which is ambient storage rather than a cold chain, and it
  has 2,880 duplicate rows and inconsistent labels (`Bad` and `BAD`). Using it to model
  spoilage in a 2–5 °C fridge would misrepresent what it measures.
- **Strawberry cold-chain shipments** and **FRISBEE logs**: transport rather than retail, and
  no accessible download.

## Modelling decisions

### 1. One refrigerator = one SME premises; one zone = one cold unit

Each instrumented fridge is modelled as a separate small retail premises (`site_id`), and
each of its five cold zones as one monitored unit (`device_id` = `BKK-<shop>-<zone>`).
A small shop really does run frozen, dairy, produce and door stock in separate zones of one
or two cabinets, so this is a physical mapping rather than an invented one.

This yields **123 sites × 7 channels = 861 channels (615 of them cold units) and
1,264,809 readings** — more than double the
earlier dataset, and it makes the quality-layer run a genuine scalability measurement
(53,382 windows across all 861 channels scored in 218 s with six parallel workers, and
identical row for row to a serial run) instead of a claim.

The dashboard and analytics work on one demo premises, `BKK-012`, a two-door top-freezer
fridge chosen because it shows a realistic spread rather than a uniform picture:

| Zone | Median | Safe limit | % of time over limit |
|---|---|---|---|
| Freezer compartment | −21.2 °C | −15 °C | 1.8% |
| Top shelf chiller | −2.3 °C | 5 °C | 0.7% |
| Middle shelf chiller | 3.8 °C | 5 °C | 16.6% |
| Vegetable drawer | 8.9 °C | 8 °C | 83.5% |
| Door shelf | 6.2 °C | 10 °C | 20.6% |

A healthy freezer, two intermittently warm zones and a chronically warm vegetable drawer.

### 1b. Reference channels: product temperature and ambient air

Two channels per fridge are monitored but are not cold-storage units. They carry no safe
limit and raise no excursion alerts, yet both are quality-scored like any other channel.

**Product probe (`PT`).** A sensor embedded in a meat-simulating gel. This is the
temperature *of the goods*, which is what actually drives spoilage; air temperature is only
a proxy. The source README does not say which shelf the gel sits on, so its position was
inferred from the data: across all 123 refrigerators it correlates 0.72 with the middle
shelf and sits 0.67 °C from it on average, against 0.43 and 3.02 °C for the next closest
zone. It is therefore treated as the middle shelf's product probe, and the business-impact
model uses it in preference to air temperature for that unit.

Why this matters, measured on the middle shelf across the fleet:

| | Air | Product |
|---|---|---|
| Mean temperature | 3.86 °C | 4.01 °C |
| Swing within a file | 6.44 °C | 4.54 °C (damped by thermal mass) |
| Time above 5 °C | 35.4% | 36.9% |

- 10 of 123 fridges would raise a breach on air temperature that the product never experienced.
- 4 of 123 would miss a real product breach that the air hid.

Every impact row records a `temp_basis` of `product` or `air`, and the dashboard shows it,
so no figure silently implies a precision it does not have.

**Ambient air (`AM`).** Distinguishes "this unit is failing" from "the shop is 33 °C
today". Fleet ambient runs 22.3 to 33.0 °C and correlates 0.36 with shelf temperature.

Both channels are far more damped than any cold zone, so their thresholds were measured
separately: their 99th-percentile step change is 0.5 °C against 2.0 °C for a freezer, so
their noise threshold is 1.5 °C rather than the chilled default of 3.0. Replay detection is
disabled for both — the gel repeats 92.1% of its readings and ambient air 63.8%, because
neither moves much between samples.

### 1c. Cold-side limits

Chilled units have a lower bound as well as an upper one. Freezing ruins chilled goods:
dairy separates, produce suffers chilling injury, eggs crack. This is not hypothetical in
this dataset:

| Zone | Mean time at or below 0 °C | Fridges over 5% of the time |
|---|---|---|
| Top shelf | 28.8% | **53 of 123** |
| Middle shelf | 10.3% | 28 of 123 |
| Door shelf | 4.2% | 12 of 123 |
| Vegetable drawer | 2.1% | 6 of 123 |

The demo premises' own top shelf sits at a median of −2.3 °C with Dairy assigned to it. A
system watching only the upper limit reported that as "Normal". Chilled zones now carry
`min_safe_c = 0 °C` and raise `cold_excursion` events; the demo shop produces 38 of them,
27 critical. Freezers have no lower bound, because a freezer colder than its setpoint
wastes energy but does not harm frozen stock.

Freezing also counts in the business-impact model, not just in the alerts: degree-minutes
are accumulated below the lower bound as well as above the upper one. This is the largest
single change to the headline figure. The demo top shelf spent 5,860 minutes - about 98 of
its 120 hours - below 0 °C with Dairy on it, so its revenue at risk rose from LKR 7,800 to
LKR 362,147, and the premises total from 23.6% to 38.0% of exposed sales. The same
degree-minute scale is used on both sides; that is a simplification, stated rather than
hidden, because freezing damage is in reality more abrupt than gradual warm spoilage.

### 2. Date alignment

The refrigerator record carries elapsed minutes, not calendar dates, so it is anchored to
**2017-08-01 00:00 UTC** — inside the Favorita sales window. Sensor and sales data then share
real calendar dates and join on date. The *choice of anchor* is still a modelling decision
(the two datasets were not collected together) and is stated on the impact page.

Sales are loaded for 2017-07-01 to 2017-08-15 so the reports page has history either side of
the 6-day sensor window; business impact only uses the overlapping days.

### 3. Categories → zones

Each perishable family is stored in exactly one zone, so no sale is counted twice.

| Zone | Temperature role | Families |
|---|---|---|
| Freezer compartment | frozen, ≤ −15 °C | Frozen Foods, Meats, Seafood, Poultry |
| Top shelf chiller | chilled, ≤ 5 °C | Dairy |
| Middle shelf chiller | chilled, ≤ 5 °C | Deli, Prepared Foods |
| Vegetable drawer | cool, ≤ 8 °C | Produce |
| Door shelf | cool, ≤ 10 °C | Eggs |

**Beverages are deliberately excluded from spoilage exposure.** They are chilled so they can
be sold cold, not to keep them safe: a door shelf drifting to 11 °C costs a sale, not the
stock. Counting them would have put the largest money figure on the dashboard behind the
least credible claim. The door shelf is still monitored and still raises alerts.

### 4. Currency

Favorita records **units sold**, not money. Values are converted to LKR so the business impact
is expressed in the currency a Sri Lankan SME works in. Prices are per unit sold:

| Family | LKR | Basis |
|---|---|---|
| Produce | 400 | CBSL Daily Price Report, 23 Oct 2026 — retail average of beans, carrot, cabbage, tomato, brinjal, pumpkin |
| Seafood | 1,400 | CBSL, same report — mid-range of Kelawalla, Thalapath, Paraw, Katta |
| Eggs | 30 | WFP Sri Lanka Market Monitor — approx. LKR 30 per egg |
| Poultry | 1,100 | indicative SME retail estimate |
| Meats | 1,800 | indicative SME retail estimate |
| Dairy | 450 | indicative SME retail estimate |
| Frozen Foods | 900 | indicative SME retail estimate |
| Deli | 800 | indicative SME retail estimate |
| Prepared Foods | 500 | indicative SME retail estimate |
| Beverages | 350 | indicative (not used for spoilage exposure) |

Three are sourced from published Sri Lankan price data; the rest are indicative estimates and
are labelled as such. Replacing them with a retailer's own price list is a config change.

## What the data quality layer found

Scored over all 861 channels (53,382 two-hour windows), with humidity validated alongside
temperature:

- **98.6% High, 0.8% Medium, 0.6% Low**; mean trust 99.2.
- **0.61% of readings flagged** and excluded from analytics (6,489 outliers, 1,173 noise,
  2 impossible jumps). Most of the rise in noise flags against the temperature-only run is
  humidity: it was never checked before, and it moves far more than temperature.
- 330 windows carry an integrity flag. Almost all come from replay detection firing on
  naturally still stretches, which is the ~1% false-positive rate the thresholds were set
  to allow. A frozen run caps the window's trust but does not remove its readings, so
  these do not reduce the data available to analytics.

This is *cleaner* than the earlier dataset, and that is itself a finding worth stating: these
are research-grade loggers with local storage, so almost nothing is lost to network problems.
A field deployment streaming over a shop's Wi-Fi would show far more missing data — which is
why the missing-data and noise checks are demonstrated by injection rather than by what they
happen to catch here.

## Outages and silent sensors

A sensor that stops reporting is the failure a cold-chain monitor most needs to catch: its last
temperature says nothing about the stock now. Two separate defects meant the system could miss it.

**In the quality record, outages vanished.** Windows used to be built from whichever readings
arrived, so a window in which the sensor sent nothing produced no row at all, and silence at the
edge of a window left no gap to measure. Measured on real data: removing 3 hours of readings across
a window boundary, or 6 hours covering whole windows, produced **no missing-data flag anywhere**.
The fault-injection "dropout" only ever worked because it removed readings from the middle of a
window.

The fix has three parts:

- Windows are laid on a **fixed grid** over each premises' observation period, and every slot is
  scored. A slot with no readings is scored as fully missing with trust 0.
- The missing-data check measures the silence **before the first and after the last reading**
  against the window's bounds, not only the gaps between readings.
- **Trust cannot exceed the share of the window actually observed.** The missing component carries
  a quarter of the weight, so without this cap a window that was 90% silent still scored about 77
  and read as Medium.

The grid is set by the premises, not the device: a gateway uploads every zone of a fridge together,
so a zone that falls silent while its siblings carry on is measured against them. After the change
the same outages produce Low windows (trust 0 to 33), the intact record is unchanged (53,382
windows, 98.6% High, no missing-data flags), and the fault demo has a new **Sensor goes silent**
case that the old check could never see.

**On the dashboard, a dead sensor looked alive.** "Latest" meant each sensor's own last reading,
so a sensor that stopped kept showing its final values as current. Silence is now measured at two
levels, with a 30-minute threshold (six missed readings):

| Level | Compared against | Points to |
|---|---|---|
| Channel silent | the newest reading from the same premises | the sensor or its battery |
| Premises silent | the newest reading anywhere in the fleet | the gateway, network or power |

Silence is deliberately not measured against the wall clock. The development data is a 2017 record,
so "now" would make every sensor look dead. In a live deployment the newest reading anywhere tracks
the wall clock closely, so the same rule applies unchanged. Verified end to end through the real
ingestion API: one reading pushed an hour past the record made the four other zones of that shop
report silent for 60 minutes, and 122 of 123 premises report silent at fleet level. A silent unit
shows Critical, its forecast is withheld rather than extrapolated from stale data, and historical
silences appear on the Alerts page as `sensor_silent` events.

## Threshold calibration against the real data

Changing sampling rate from ~5 seconds to 5 minutes invalidated several thresholds. Each was
recalibrated against the measured distribution across all 123 refrigerators, not guessed.

| Setting | Before | After | Why |
|---|---|---|---|
| Scoring window | 30 min | 120 min | At 5-minute sampling, 30 min holds only 6 readings — too few for a quartile or a trend |
| Noise rate threshold | 2.0 °C for every unit | 6.0 °C freezer, 3.0 °C chilled, 1.5 °C reference channels | At 2.0 °C, **25.1% of clean freezer windows** were flagged: a defrost cycle genuinely moves a freezer several degrees in 5 minutes. Now 0.1% freezer, 0.0% elsewhere |
| Humidity checked at all | **not checked** | outlier + noise, same as temperature | Humidity feeds the anomaly detector's features, so leaving it unvalidated broke "quality before analytics" for one of the two measured quantities |
| Humidity spread floor | 1.5 points (inherited, unused) | 10.0 points | At the temperature-style floor, **18.3% of clean humidity windows** flagged. At 10.0 it is 2.1% |
| Humidity noise rate | — | 20 points per sample | At 8 points, 13.0% of clean windows flagged; at 20 it is 1.1% |
| Noise min ratio | 0.02 | 0.15 | Noise means *sustained* jitter. At 0.02 a single defrost step flagged the whole window |
| Drift slope / shift | 0.05 / 1.5 for every unit | 0.7 / 9.0 freezer, 0.3 / 3.5 chilled | At the old values, **45.8% of clean freezer windows** flagged as drifting. Set near each zone's 99th percentile, giving ~1% |

Known limit kept in view: a 2-hour window can only reveal a trend *inside* that window — a
door left open, a failing compressor. True calibration drift over weeks needs a longer
baseline, which remains future work.

## Known limitations

1. Household refrigerators stand in for retail cabinets; Ecuadorian sales stand in for
   Sri Lankan sales.
2. The date anchor and the category-to-zone mapping are modelling decisions, not observations.
3. Seven of ten LKR prices are indicative estimates.
4. Forecasting and business impact run on the demo premises only. Quality scoring runs on all
   123, which is what makes the scoring throughput a measurement rather than a claim, but
   ARIMA has never been exercised at fleet scale.
5. The data is research-grade and nearly complete, so the missing-data check finds little
   naturally.
