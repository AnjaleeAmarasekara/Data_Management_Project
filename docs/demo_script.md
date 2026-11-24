# Review demo script (about 12 minutes)

Before starting: database up (`docker compose up -d`), API and dashboard running (see README),
browser on http://localhost:4200. Optionally start the live feed in a third terminal. Sign in
as `staff@coldsense.lk` to start (password `ColdSense#2025` for every account).

## 1. The problem (1 min)

Cold-chain sensors are trusted blindly. Drift, dropouts, noise and calibration errors quietly
corrupt the data, and decisions made on it cost real money. ColdSense-DQ puts a data quality
layer between ingestion and analytics, and an enterprise access model in front of both.

Worth saying up front: the development data is **real refrigeration telemetry** — 123
instrumented refrigerators in Bangkok, a tropical climate comparable to Sri Lanka's, giving
1,264,809 readings across 861 sensor channels (615 cold zones plus a product probe and an
ambient sensor per premises). Nothing about the temperatures is simulated.

## 2. Sign in as Staff — the view-only role (1 min)

- The sidebar shows only the four viewing pages; no Reports, no User Management.
- On **Data Quality**, the fault-injection buttons are disabled with a notice explaining the
  required role. Staff see the *results* of the last demo run, not the trigger.
- On **Alerts**, no Export CSV and no Acknowledge column. Same data, fewer actions — the role
  boundary is about what you can *do*, not what you can *see*.

## 3. Real-Time Monitoring (2 min)

- One premises, five monitored zones, each with a **data trust score** beside its temperature.
  The score is computed live from the newest 2-hour window, using the same code as the batch job.
- The five cards tell a real story on their own, and it is worth reading them out:
  - *Freezer Compartment* about −21 °C — healthy.
  - *Top Shelf Chiller* about −1 °C — flagged **Critical / too cold**. Dairy is stored there and
    freezing ruins it, so this is a real failure, not over-performance. Worth dwelling on: a
    warm-only system calls this "Normal", and 53 of the 123 fridges in the dataset spend more
    than 5% of their time below freezing on that shelf.
  - *Middle Shelf Chiller*, *Vegetable Drawer* and *Door Shelf* — all **Critical**, sitting above
    their safe limits. This fridge genuinely runs warm in its lower zones.
- Switch the chart to **Freezer Compartment / 6 h** and point out the saw-tooth: that is the real
  compressor cycle, not noise. Then the dashed safe limit and any red markers — readings the
  quality layer flagged and excluded rather than plotted into the line.
- The forecast line under each card is an ARIMA model (order chosen by AIC each run), falling
  back to a linear trend when there is not enough clean history. Note the deliberately hedged
  wording ("could be reached within…, indicative only") and say why in section 6.
- If the live feed is running, mention it pushes each reading to the real ingestion API
  (`POST /api/ingest/readings`) with a device key — the "Sensor Data Stream" path from the
  architecture diagram, distinct from the retail "Batch Upload" path.

## 4. Sign out, sign in as Quality Supervisor (1 min)

Sidebar now includes **Reports**, and on **Data Quality** the fault-injection buttons are enabled.

## 5. Data Quality — the core contribution (4 min)

- Per-unit summary: 1,469 readings per zone in 62 two-hour windows, with the share excluded as
  row-level glitches. Across the whole 123-premises network the layer scored **53,382 windows in
  under four minutes** with six parallel workers — that number is the scalability evidence,
  measured rather than claimed, and the parallel output is identical to a serial run.
- **Fault injection demo**, in this order:
  1. *Interference spike* — detected and flagged, but the window stays High because the bad
     reading is excluded downstream. Isolated faults are handled at row level.
  2. *Sensor drift* — window drops to Medium. Drift biases every reading, so the whole window
     loses trust.
  3. *Sensor dropout* — missing data, trust falls.
  4. *Calibration error (gross)* — physically impossible values, trust collapses to Low.
  5. *Calibration error (subtle)* — **Not detected.** A constant in-range offset is
     indistinguishable from a genuinely warmer unit using one sensor's data. It needs
     cross-sensor comparison or reference calibration. Naming a limit yourself is stronger
     than being asked about it.
  6. *Data injection attack* — trust collapses to 20, flagged `integrity`. A fabricated reading
     beyond the unit's thermal mass is treated as a confirmed tampering signature.
  7. *Replay attack* — **now detected**, and this is the best story on the page. It was
     originally disabled: the earlier development sensor cached readings, so 83% of values were
     exact repeats and no threshold worked. Re-measuring on the refrigeration data gave 7.6%
     repeats in a freezer and 11.4% on a top shelf, so the check was re-enabled with thresholds
     set so every zone stays under a 2% false-positive rate. It stays **off for the vegetable
     drawer**, where 48.6% of readings repeat naturally — a declared blind spot rather than a
     false-alarm source. Run it on the Vegetable Drawer to show that honestly.
  8. *Sensor goes silent* — **detected**, trust drops to Low. Tell the story behind it: until
     this fix, a sensor that stopped part-way through a window left nothing to measure, and on real
     data a 6-hour outage produced no missing-data flag at all. Windows are now laid on a fixed grid
     and every one is scored, so silence costs trust like any other missing data. On the
     Monitoring page a silent sensor reads Critical, "No readings for N min", and withholds its
     forecast rather than extrapolating from stale data.
- Scroll to **Across the network**: every one of the 123 premises, weakest first. This is the
  scalability evidence, and it is measured rather than claimed — the quality layer scored all
  of them. Say plainly that forecasting and business impact run on the demo premises only.
- Scroll on to **How accurate is this quality layer?** — the answer to "but how good is it?",
  on the page rather than on a slide. Precision 0.91, recall 0.88, F1 0.89, measured from 300 clean
  windows x 10 fault types against 600 untouched real windows. Point at the confusion matrix, then at
  the per-fault table where the subtle calibration offset reads **0% / not detected**: naming the blind
  spot inside the product is stronger than being asked about it. If the 8.8% false-alarm figure is
  challenged, the answer is on the page — the real windows are deliberately *not* pre-filtered, so it
  is an upper bound, and only 1.7% of windows actually change label.
- Threshold calibration is worth one sentence: moving to five-minute sampling broke the old
  thresholds. The noise check was flagging **25% of clean freezer windows** because freezers
  defrost, and drift was flagging **45.8%**. Both were recalibrated per equipment type against
  the measured distribution across all 123 fridges. That is the data-mining work.
- If asked about unauthorized access rather than tampering: scroll to **Ingestion Security**,
  which logs failed device-key attempts and pushes from unregistered device ids.

## 5b. Historical Trends (1 min)

- Open **Historical Trends**, pick **Top Shelf Chiller** and **Daily**: 99 hours too cold over five
  days, more than 1,000 minutes below freezing on every full day, with 100% data completeness and
  high trust. That combination is the point: the data is trustworthy, so the problem is real.
- Switch to **Middle Shelf Chiller**: the green line is the product's own temperature, lagging the
  air because food has thermal mass.
- **All units compared** shows at a glance which unit is the chronic problem.
- If asked about time-of-day patterns: the loggers record elapsed time, not clock time, so a
  "warmest in the afternoon" pattern would be invented by the anchoring, and the page says so.

## 6. Alerts, Impact and Reports (2 min)

- On **Alerts**, acknowledge one: it records who acknowledged it and when.
- Alerts come in two kinds, and be straight about the difference in confidence:
  - *Safe-limit excursions* are rule-based and dependable.
  - *Early warnings* come from an Isolation Forest, and the evaluation shows it performs
    **poorly on real refrigeration data** (F1 roughly 0.05–0.12 even with a matched alert
    budget). Real fridges cycle hard, so an injected sub-limit bump is not distinguishable from
    normal compressor behaviour. On the earlier, unrealistically smooth dataset the same model
    scored about 0.5 — so the honest reading is that the old number was an artefact of easy data.
    Rule-based detection is the layer to trust.
- **Impact**: revenue at risk in **LKR**, using published Sri Lankan retail prices where
  available (CBSL, WFP) and indicative estimates otherwise. Point at the **Based on** column:
  where a probe sits inside the goods, the estimate uses the product's own temperature rather
  than the air. The two disagree for 14 of 123 fridges, in both directions. Note that beverages are monitored but
  carry no spoilage value — a warm door shelf costs a sale, not the stock.
- **Reports**: pick units and date range (defaults to the measurement window), preview, then
  **Download PDF**.
- State the caveats: the impact model is a documented heuristic, the household refrigerators
  stand in for retail cabinets, and the sensor/sales linkage is modelled because real SME data
  is confidential.

## 7. Sign in as Administrator (1 min)

**User Management**: create an account, change a role, disable one. Then the **audit log** below:
who did what and when, including every fault-injection run on this dashboard.

## Likely questions

- **Why is most of the network High trust?** Because the data genuinely is clean — these are
  research-grade loggers with local storage, so almost nothing is lost to network problems. A
  field deployment streaming over a shop's Wi-Fi would show far more missing data. The layer is
  not tuned to manufacture problems, and the fault-injection panel is how its behaviour is shown.
- **Why household fridges?** No open dataset pairs retail refrigeration with sales. These are the
  closest realistic match: small cabinets, tropical ambient, real door openings and defrost
  cycles. The substitution is stated on the dashboard and in `docs/data_linkage.md`.
- **Why is the anomaly detector weak?** Answer with the measurement, not a defence — and with the
  diagnosis: the features are raw temperature, humidity, change and volatility, which cannot
  separate an anomaly from a compressor cycle. Modelling each unit's *own expected cycle* and
  detecting deviation from it is the clear next step.
- **How are the two datasets linked?** By anchoring the refrigerator measurement window onto the
  retail calendar, and assigning each perishable family to the zone that physically stores it.
  Both are modelling decisions, recorded in `docs/data_linkage.md`.
