import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { ChartConfiguration } from 'chart.js';

import { ApiService, FaultName, Flags, InjectResult, NetworkSummary, Point, QualityEvaluation, SecurityEvents, TimelineItem, UnitQuality } from '../shared/api.service';
import { AuthService } from '../shared/auth.service';
import { ChartComponent } from '../shared/chart';
import { fmtTs } from '../shared/format';

const FAULTS: { name: FaultName; label: string }[] = [
  { name: 'dropout', label: 'Sensor dropout' },
  { name: 'spike', label: 'Interference spike' },
  { name: 'drift', label: 'Sensor drift' },
  { name: 'noise', label: 'Electrical noise' },
  { name: 'duplicate_timestamp', label: 'Duplicate timestamp' },
  { name: 'calibration_bias_subtle', label: 'Calibration error (subtle)' },
  { name: 'calibration_bias_extreme', label: 'Calibration error (gross)' },
  { name: 'replay_attack', label: 'Replay attack' },
  { name: 'data_injection', label: 'Data injection attack' },
  { name: 'sensor_silent', label: 'Sensor goes silent' },
];

@Component({
  selector: 'app-quality',
  imports: [ChartComponent, DecimalPipe],
  template: `
    <div class="page-head">
      <div>
        <h1>Data Quality</h1>
        <p class="subtle">
          Every 2-hour window of sensor data is scored before analytics is allowed to use it.
          High &ge; 85, Medium &ge; 60, Low below that.
        </p>
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <div class="grid cols-3">
      @for (u of summary(); track u.device_id) {
        <article class="card">
          <div class="row nowrap">
            <strong>{{ u.unit_name }}</strong>
            <span class="spacer"></span>
            <span class="subtle">avg trust</span>
            <strong>{{ u.avg_trust | number: '1.1-1' }}</strong>
          </div>
          <p class="subtle" style="margin: 6px 0 10px">{{ u.readings | number }} readings in {{ u.windows }} windows</p>

          <div class="stackbar" role="img" [attr.aria-label]="labelSummary(u)">
            @for (l of labels; track l) {
              @if (u.labels[l] > 0) {
                <div [class]="'seg-' + l" [style.flex-grow]="u.labels[l]" [title]="l + ': ' + u.labels[l]"></div>
              }
            }
          </div>
          <div class="row subtle" style="margin: 6px 0 12px">
            @for (l of labels; track l) { <span><span class="dot" [class]="'seg-' + l"></span>{{ l }} {{ u.labels[l] }}</span> }
          </div>

          <h3>Windows with a fault flagged</h3>
          <div style="margin-bottom: 10px">
            @for (f of flagList(u.windows_flagged); track f[0]) {
              <span class="chip" [class.clear]="f[1] === 0">{{ f[0] }} {{ f[1] }}</span>
            }
          </div>
          <p class="subtle" style="margin: 0">
            {{ u.excluded_from_analytics | number }} readings excluded from analytics
            ({{ (u.excluded_from_analytics / u.readings) * 100 | number: '1.2-2' }}% of all readings).
          </p>
        </article>
      }
    </div>

    <section class="card" style="margin-top: 14px">
      <div class="row" style="margin-bottom: 10px">
        <h2 style="margin: 0">Trust score over time</h2>
        <span class="spacer"></span>
        <div class="seg" role="group" aria-label="Unit">
          @for (u of summary(); track u.device_id) {
            <button [class.on]="u.device_id === timelineDevice()" (click)="loadTimeline(u.device_id)">{{ u.unit_name }}</button>
          }
        </div>
      </div>
      @if (timelineChart(); as c) { <app-chart [config]="c" [height]="240" /> }
    </section>

    <section class="card" style="margin-top: 14px">
      <h2>Fault injection test</h2>
      <p class="subtle">
        Takes a known-clean 2-hour window of real data, corrupts a copy in memory with a synthetic fault, and runs the
        real quality layer on both. Nothing is written to the database.
      </p>

      @if (!canRunDemo()) {
        <div class="notice info">
          Running the fault-injection demo requires the Quality Supervisor role or above. Your account
          ({{ auth.user()?.role }}) can view the results below but not trigger a new one.
        </div>
      }

      <div class="row" style="margin: 10px 0">
        <span class="subtle">Unit</span>
        <select (change)="setDemoDevice($any($event.target).value)" aria-label="Unit for the demo" [disabled]="!canRunDemo()">
          @for (u of summary(); track u.device_id) {
            <option [value]="u.device_id" [selected]="u.device_id === demoDevice()">{{ u.unit_name }}</option>
          }
        </select>
      </div>
      <div class="row" style="margin-bottom: 14px">
        @for (f of faults; track f.name) {
          <button [class.primary]="result()?.fault === f.name" [disabled]="running() || !canRunDemo()" (click)="inject(f.name)">{{ f.label }}</button>
        }
      </div>

      @if (result(); as r) {
        <div class="grid cols-2" style="margin-bottom: 12px">
          <div class="notice">
            <div class="kpi-label">Injected fault</div>
            <div>{{ r.description }}</div>
          </div>
          <div class="notice" [class.info]="!r.detected" [class.error]="false">
            <div class="kpi-label">Verdict</div>
            <div>
              <span class="pill" [class]="r.detected ? 'ok' : 'warning'">{{ r.detected ? 'Detected' : 'Not detected' }}</span>
              {{ verdictText(r) }}
            </div>
          </div>
        </div>

        <div class="row" style="margin-bottom: 12px">
          <div class="scorebox">
            <div class="kpi-label">Before</div>
            <span class="big">{{ r.before.score.trust | number: '1.1-1' }}</span>
            <span class="pill" [class]="r.before.score.label">{{ r.before.score.label }}</span>
          </div>
          <span aria-hidden="true">&rarr;</span>
          <div class="scorebox">
            <div class="kpi-label">After</div>
            <span class="big">{{ r.after.score.trust | number: '1.1-1' }}</span>
            <span class="pill" [class]="r.after.score.label">{{ r.after.score.label }}</span>
          </div>
          <div>
            @for (f of activeFlags(r.after.score.flags); track f) { <span class="chip">{{ f }}</span> }
            @empty { <span class="chip clear">No flags raised</span> }
          </div>
        </div>

        @if (demoChart(); as c) { <app-chart [config]="c" [height]="280" /> }
        @if (r.note; as n) { <div class="notice info" style="margin-top: 10px">{{ n }}</div> }
      }
    </section>

    @if (network(); as net) {
      <section class="card" style="margin-top: 14px">
        <h2>Across the network</h2>
        <p class="subtle">
          {{ net.totals.readings | number }} readings from {{ net.totals.channels | number }} sensor channels across
          {{ net.totals.sites | number }} premises, in {{ net.totals.windows | number }} scored windows.
          {{ net.totals.silent_channels }} sensors and {{ net.totals.silent_premises }} premises are silent right now
          (no reading for over {{ net.silence_threshold_min }} min). Weakest premises first.
        </p>
        <details class="about">
          <summary>How to read this</summary>
          <div>
            The quality layer scores every instrumented premises, not only the demo one above. Each premises has
            {{ net.totals.cold_units | number }} cold units in total, plus a product probe and an ambient sensor, and all
            of them are scored. Forecasting and business impact run on the demo premises only; scoring runs on all of
            them, which is what makes the figures here a measurement rather than a claim.
          </div>
        </details>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Premises</th><th class="num">Units</th><th class="num">Windows</th>
                <th class="num">Avg trust</th><th class="num">Low</th><th class="num">Possible tampering</th>
                <th class="num">Silent sensors</th>
              </tr>
            </thead>
            <tbody>
              @for (s of networkTop(); track s.site_id) {
                <tr>
                  <td>{{ s.site_name }}</td>
                  <td class="num">{{ s.units }}</td>
                  <td class="num">{{ s.windows | number }}</td>
                  <td class="num"><strong>{{ s.avg_trust | number: '1.1-1' }}</strong></td>
                  <td class="num">{{ s.low_windows }}</td>
                  <td class="num">{{ s.integrity_windows }}</td>
                  <td class="num">
                    @if (s.premises_silent) { <span class="pill Low">whole premises</span> }
                    @else { {{ s.silent_channels }} }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        <p class="subtle" style="margin-top: 8px">Showing the {{ networkTop().length }} weakest of {{ net.sites.length }} premises.</p>
      </section>
    }

    @if (evaluation(); as ev) {
      <section class="card" style="margin-top: 14px">
        <h2>How accurate is this quality layer?</h2>
        <p class="subtle">
          Known faults are injected into windows that scored clean, and the same scoring code used everywhere else
          decides whether each one is caught.
        </p>
        <details class="about">
          <summary>Why it is measured this way</summary>
          <div>
            Real sensor data carries no record of which windows were faulty, so there is nothing to check the layer
            against directly. Injecting faults of known type and position gives a ground truth. Every outcome then
            falls into one of four boxes, which is what the measures below are computed from.
          </div>
        </details>
        <div class="grid cols-4" style="margin-top: 12px">
          <div class="card"><div class="kpi-label">Precision</div>
            <div class="big">{{ ev.overall.precision | number: '1.2-2' }}</div>
            <p class="subtle">Of the flags raised, how many were real faults</p></div>
          <div class="card"><div class="kpi-label">Recall</div>
            <div class="big">{{ ev.overall.recall | number: '1.2-2' }}</div>
            <p class="subtle">Of the faults present, how many were caught</p></div>
          <div class="card"><div class="kpi-label">F1 score</div>
            <div class="big">{{ ev.overall.f1 | number: '1.2-2' }}</div>
            <p class="subtle">Both together: high only when neither is sacrificed</p></div>
          <div class="card"><div class="kpi-label">Accuracy</div>
            <div class="big">{{ ev.overall.accuracy | number: '1.2-2' }}</div>
            <p class="subtle">Correct calls overall, on an even mix of faulty and real windows</p></div>
        </div>

        <div class="grid cols-2" style="margin-top: 14px">
          <div>
            <h3 style="margin: 0 0 8px">Confusion matrix</h3>
            <div class="table-wrap">
              <table class="confusion">
                <thead><tr><th></th><th class="num">Flag raised</th><th class="num">No flag</th></tr></thead>
                <tbody>
                  <tr>
                    <th>Fault injected</th>
                    <td class="num hit"><strong>{{ ev.overall.tp | number }}</strong><span class="cell-note">caught</span></td>
                    <td class="num miss">{{ ev.overall.fn | number }}<span class="cell-note">missed</span></td>
                  </tr>
                  <tr>
                    <th>Real window, untouched</th>
                    <td class="num miss">{{ ev.overall.fp | number }}<span class="cell-note">false alarm</span></td>
                    <td class="num hit"><strong>{{ ev.overall.tn | number }}</strong><span class="cell-note">left alone</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="subtle" style="margin-top: 8px">
              {{ ev.sample.positive_windows_per_fault | number }} clean windows &times; {{ ev.per_fault.length }} fault types,
              against {{ ev.sample.negative_windows | number }} untouched real windows.
            </p>
          </div>
          <div>
            <h3 style="margin: 0 0 8px">Per check</h3>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Check</th><th class="num">Precision</th><th class="num">Recall</th><th class="num">F1</th></tr></thead>
                <tbody>
                  @for (c of ev.per_check; track c.check) {
                    <tr>
                      <td>{{ c.check }}</td>
                      <td class="num">{{ c.precision | number: '1.2-2' }}</td>
                      <td class="num">{{ c.recall | number: '1.2-2' }}</td>
                      <td class="num"><strong>{{ c.f1 | number: '1.2-2' }}</strong></td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <p class="subtle" style="margin-top: 8px">
              Tampering scores lowest on recall because replay detection is deliberately switched off in the
              vegetable drawer, where produce holds the temperature so steady that repeated readings are normal.
            </p>
          </div>
        </div>

        <h3 style="margin: 16px 0 8px">Detection rate per fault type</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Injected fault</th><th>Check expected to catch it</th><th class="num">Windows tested</th><th class="num">Detection rate</th><th></th></tr></thead>
            <tbody>
              @for (f of ev.per_fault; track f.fault) {
                <tr>
                  <td>{{ f.fault }}</td>
                  <td class="subtle">{{ f.expected_check || 'none — see note below' }}</td>
                  <td class="num">{{ f.windows | number }}</td>
                  <td class="num"><strong>{{ f.recall * 100 | number: '1.0-1' }}%</strong></td>
                  <td>
                    @if (f.recall >= 0.99) { <span class="pill ok">detected</span> }
                    @else if (f.recall > 0) { <span class="pill warning">partly detected</span> }
                    @else { <span class="pill critical">not detected</span> }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>

        <p class="subtle" style="margin-top: 12px">
          A check flag fires on {{ ev.false_alarm_rate.rate * 100 | number: '1.0-1' }}% of real windows, but only
          {{ ev.false_alarm_rate_label_level.rate * 100 | number: '1.0-1' }}% drop below High: most flags are a single stray
          reading that is excluded while the window stays trusted.
        </p>
        <details class="about">
          <summary>Why the two figures differ</summary>
          <div>
            A flag fired on {{ ev.false_alarm_rate.flagged }} of {{ ev.false_alarm_rate.of }} real windows, but only
            {{ ev.false_alarm_rate_label_level.flagged }} of {{ ev.false_alarm_rate_label_level.of }} fell below High. The real
            windows were not filtered for cleanliness first, so some are genuinely faulty and the false-alarm rate is an
            upper bound. Judged on the label alone, recall is {{ ev.label_level.recall | number: '1.2-2' }} with precision
            {{ ev.label_level.precision | number: '1.2-2' }}: an isolated fault is removed rather than alarmed.
          </div>
        </details>

        <p class="subtle" style="margin-top: 10px">
          A subtle calibration offset has no check to catch it: a steady in-range shift looks like a warmer unit to one sensor.
          Measured {{ fmtTime(ev.generated_at) }}.
          <code>python -m dq_engine.evaluation.evaluate_quality</code> reproduces it.
        </p>
      </section>
    }

    @if (auth.hasRole('quality_supervisor')) {
      <section class="card" style="margin-top: 14px">
        <h2>Ingestion Security</h2>
        <p class="subtle">
          Rejected attempts to push data: a wrong or missing device key, or a device that is not registered.
        </p>
        <details class="about">
          <summary>How this differs from a quality flag</summary>
          <div>
            A quality flag asks whether data that was accepted can be trusted. This panel is about who tried to get
            in at all, before any reading is stored.
          </div>
        </details>
        @if (security(); as sec) {
          <div class="row" style="margin-bottom: 10px">
            <span class="pill" [class]="sec.total ? 'warning' : 'ok'">{{ sec.total }} event(s) in last 24h</span>
            @for (t of securityTypeEntries(sec); track t[0]) { <span class="chip">{{ t[0] }}: {{ t[1] }}</span> }
          </div>
          @if (sec.events.length) {
            <div class="table-wrap">
              <table>
                <thead><tr><th>Type</th><th>Device attempted</th><th>Detail</th><th>When (UTC)</th></tr></thead>
                <tbody>
                  @for (e of sec.events.slice(0, 10); track e.id) {
                    <tr>
                      <td><span class="pill warning">{{ e.event_type }}</span></td>
                      <td>{{ e.device_id || '—' }}</td>
                      <td class="subtle">{{ e.detail }}</td>
                      <td>{{ fmtTime(e.occurred_at) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          } @else {
            <p class="subtle">No unauthorized access attempts recorded.</p>
          }
        }
      </section>
    }
  `,
  styles: [`
    table.confusion th { font-weight: 600; color: var(--muted); }
    table.confusion td { font-size: 22px; font-variant-numeric: tabular-nums; vertical-align: middle; }
    table.confusion td.hit { background: var(--ok-bg); color: var(--ok); }
    table.confusion td.miss { background: var(--warn-bg); color: var(--warn); }
    .cell-note { display: block; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.75; }
    .stackbar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: var(--border); }
    .seg-High { background: var(--ok); }
    .seg-Medium { background: #d99a0b; }
    .seg-Low { background: var(--crit); }
    .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; }
    .scorebox { display: flex; align-items: center; gap: 8px; padding: 8px 14px; border: 1px solid var(--border); border-radius: 8px; }
    .scorebox .kpi-label { margin-right: 4px; }
  `],
})
export class QualityPage implements OnInit {
  private api = inject(ApiService);
  auth = inject(AuthService);
  readonly faults = FAULTS;
  canRunDemo = () => this.auth.hasRole('quality_supervisor');
  readonly labels = ['High', 'Medium', 'Low'] as const;

  summary = signal<UnitQuality[]>([]);
  error = signal<string | null>(null);
  timelineDevice = signal<string | null>(null);
  timeline = signal<TimelineItem[]>([]);
  demoDevice = signal<string>('');
  result = signal<InjectResult | null>(null);
  running = signal(false);
  security = signal<SecurityEvents | null>(null);
  network = signal<NetworkSummary | null>(null);
  evaluation = signal<QualityEvaluation | null>(null);
  networkTop = computed(() => (this.network()?.sites ?? []).slice(0, 12));

  timelineChart = computed<ChartConfiguration | null>(() => {
    const t = this.timeline();
    if (!t.length) return null;
    return {
      type: 'line',
      data: {
        labels: t.map((w) => fmtTs(w.window_start)),
        datasets: [
          { label: 'Trust score', data: t.map((w) => w.trust), borderColor: '#0f766e', backgroundColor: '#0f766e', borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
          { label: 'High threshold (85)', data: t.map(() => 85), borderColor: '#14743f', borderDash: [6, 4], borderWidth: 1, pointRadius: 0 },
          { label: 'Medium threshold (60)', data: t.map(() => 60), borderColor: '#b3261e', borderDash: [6, 4], borderWidth: 1, pointRadius: 0 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'top', align: 'end' } },
        scales: { x: { ticks: { maxTicksLimit: 10 }, grid: { display: false } }, y: { min: 0, max: 100, title: { display: true, text: 'Trust' } } },
      },
    };
  });

  demoChart = computed<ChartConfiguration | null>(() => {
    const r = this.result();
    if (!r) return null;
    const t0 = new Date(r.before.points[0].ts).getTime();
    const xy = (pts: Point[]) => pts.map((p) => ({ x: (new Date(p.ts).getTime() - t0) / 60000, y: p.temp_c }));

    const temps = r.before.points.map((p) => p.temp_c).sort((a, b) => a - b);
    const mid = temps[Math.floor(temps.length / 2)];
    const flaggedTs = new Set(Object.values(r.after.flagged).flat());
    const flaggedPts = r.after.points.filter((p) => flaggedTs.has(p.ts));

    return {
      type: 'line',
      data: {
        datasets: [
          { label: 'Before (clean)', data: xy(r.before.points), borderColor: '#a3adbd', borderWidth: 1.4, pointRadius: 0 },
          { label: 'After fault', data: xy(r.after.points), borderColor: '#0f766e', borderWidth: 1.4, pointRadius: 0 },
          { label: 'Flagged by quality layer', data: xy(flaggedPts), showLine: false, borderColor: '#b3261e', backgroundColor: '#b3261e', pointStyle: 'triangle', pointRadius: 5 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        plugins: { legend: { position: 'top', align: 'end' } },
        scales: {
          x: { type: 'linear', title: { display: true, text: 'Minutes into window' } },
          y: { suggestedMin: mid - 2, suggestedMax: mid + 2, title: { display: true, text: '°C' } },
        },
      },
    };
  });

  ngOnInit() {
    this.api.qualitySummary().subscribe({
      next: (s) => {
        this.summary.set(s.units);
        if (s.units.length) {
          const weakest = [...s.units].sort((a, b) => a.avg_trust - b.avg_trust)[0];
          this.demoDevice.set(weakest.device_id);
          this.loadTimeline(weakest.device_id);
        }
      },
      error: () => this.error.set('Cannot reach the API at localhost:8000. Start the backend (see README).'),
    });

    this.api.qualityNetwork().subscribe({ next: (n) => this.network.set(n), error: () => {} });
    // Absent until the evaluation has been run at least once; the section simply does not appear.
    this.api.qualityEvaluation().subscribe({ next: (e) => this.evaluation.set(e), error: () => {} });

    if (this.auth.hasRole('quality_supervisor')) {
      this.api.securityEvents().subscribe({ next: (sec) => this.security.set(sec), error: () => {} });
    }
  }

  securityTypeEntries(sec: SecurityEvents): [string, number][] {
    return Object.entries(sec.last_24h_by_type);
  }

  fmtTime(ts: string) {
    return fmtTs(ts);
  }

  setDemoDevice(id: string) {
    this.demoDevice.set(id);
    this.result.set(null);
  }

  loadTimeline(id: string) {
    this.timelineDevice.set(id);
    this.api.qualityTimeline(id).subscribe({ next: (t) => this.timeline.set(t), error: () => {} });
  }

  inject(fault: FaultName) {
    this.running.set(true);
    this.api.inject(this.demoDevice(), fault).subscribe({
      next: (r) => {
        this.result.set(r);
        this.running.set(false);
      },
      error: () => {
        this.error.set('The fault injection request failed. Is the backend running?');
        this.running.set(false);
      },
    });
  }

  labelSummary(u: UnitQuality) {
    return `High ${u.labels.High}, Medium ${u.labels.Medium}, Low ${u.labels.Low} windows`;
  }

  flagList(f: Record<keyof Flags, number>): [string, number][] {
    return [['Missing', f.missing], ['Outlier', f.outlier], ['Drift', f.drift], ['Noise', f.noise], ['Integrity', f.integrity]];
  }

  activeFlags(f: Flags): string[] {
    const names: [keyof Flags, string][] = [
      ['missing', 'Missing data'],
      ['outlier', 'Outliers'],
      ['drift', 'Drift'],
      ['noise', 'Noise / timestamps'],
      ['integrity', 'Integrity (possible tampering)'],
    ];
    return names.filter(([k]) => f[k]).map(([, label]) => label);
  }

  verdictText(r: InjectResult): string {
    if (!r.detected) return 'The quality layer could not see this fault from the data alone.';
    if (r.after.score.label === 'High') {
      return 'The affected readings are flagged and excluded downstream, while the window as a whole stays trusted.';
    }
    return 'The whole window loses trust because the fault affects many readings.';
  }
}
