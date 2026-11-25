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
        <p class="subtle">Every reading is checked before it is used. Low-trust data never reaches alerts or reports.</p>
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <div class="tabs" role="tablist">
      @for (t of tabs(); track t.key) {
        <button role="tab" [class.on]="tab() === t.key" [attr.aria-selected]="tab() === t.key" (click)="tab.set(t.key)">{{ t.label }}</button>
      }
    </div>

    @if (tab() === 'overview') {
      @if (overall(); as o) {
        <div class="stats">
          <div class="stat"><span class="stat-label">Average trust</span><span class="stat-value ok">{{ o.trust | number: '1.1-1' }}</span><span class="stat-sub">out of 100</span></div>
          <div class="stat"><span class="stat-label">Readings checked</span><span class="stat-value">{{ o.readings | number }}</span></div>
          <div class="stat"><span class="stat-label">Excluded as unreliable</span><span class="stat-value">{{ o.excludedPct | number: '1.1-1' }}%</span><span class="stat-sub">{{ o.excluded | number }} readings</span></div>
          <div class="stat"><span class="stat-label">Low-trust periods</span><span class="stat-value" [class.crit]="o.low > 0">{{ o.low }}</span><span class="stat-sub">of {{ o.windows | number }} two-hour windows</span></div>
        </div>
      }

      <section class="card">
        <h2>By unit</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Unit</th><th class="num">Trust</th><th style="width: 30%">High / Medium / Low periods</th><th>Issues found</th><th class="num">Excluded</th></tr></thead>
            <tbody>
              @for (u of summary(); track u.device_id) {
                <tr class="clickable" [class.sel]="u.device_id === timelineDevice()" (click)="loadTimeline(u.device_id)">
                  <td><strong>{{ u.unit_name }}</strong></td>
                  <td class="num"><strong>{{ u.avg_trust | number: '1.1-1' }}</strong></td>
                  <td>
                    <div class="stackbar" role="img" [attr.aria-label]="labelSummary(u)" [title]="labelSummary(u)">
                      @for (l of labels; track l) {
                        @if (u.labels[l] > 0) { <div [class]="'seg-' + l" [style.flex-grow]="u.labels[l]"></div> }
                      }
                    </div>
                  </td>
                  <td>
                    @for (f of flagList(u.windows_flagged); track f[0]) { @if (f[1] > 0) { <span class="chip">{{ f[0] }} {{ f[1] }}</span> } }
                    @if (noFlags(u)) { <span class="subtle">None</span> }
                  </td>
                  <td class="num subtle">{{ (u.excluded_from_analytics / u.readings) * 100 | number: '1.1-1' }}%</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>

      <section class="card" style="margin-top: 16px">
        <div class="row" style="margin-bottom: 10px">
          <h2 style="margin: 0">Trust over time &middot; {{ timelineName() }}</h2>
          <span class="spacer"></span>
          <span class="subtle small">Select a unit above to switch</span>
        </div>
        @if (timelineChart(); as c) { <app-chart [config]="c" [height]="240" /> }
      </section>
    }

    @if (tab() === 'test') {
      <section class="card">
        <h2>Test a fault</h2>
        <p class="subtle">Corrupt a clean sample of real data and see whether the checks catch it. Nothing is saved.</p>

        @if (!canRunDemo()) {
          <div class="notice info">Running a test needs the Quality Supervisor role or above.</div>
        }

        <div class="row" style="margin: 12px 0">
          <select (change)="setDemoDevice($any($event.target).value)" aria-label="Unit for the test" [disabled]="!canRunDemo()">
            @for (u of summary(); track u.device_id) {
              <option [value]="u.device_id" [selected]="u.device_id === demoDevice()">{{ u.unit_name }}</option>
            }
          </select>
        </div>
        <div class="row" style="margin-bottom: 16px">
          @for (f of faults; track f.name) {
            <button [class.primary]="result()?.fault === f.name" [disabled]="running() || !canRunDemo()" (click)="inject(f.name)">{{ f.label }}</button>
          }
        </div>

        @if (result(); as r) {
          <div class="verdict" [class.miss]="!r.detected">
            <span class="pill" [class]="r.detected ? 'ok' : 'warning'">{{ r.detected ? 'Detected' : 'Not detected' }}</span>
            <span>{{ r.description }}</span>
            <span class="spacer"></span>
            <span class="score">Trust {{ r.before.score.trust | number: '1.0-0' }} &rarr; <strong>{{ r.after.score.trust | number: '1.0-0' }}</strong></span>
          </div>
          <p class="subtle small" style="margin: 8px 0 12px">
            {{ verdictText(r) }}
            @for (f of activeFlags(r.after.score.flags); track f) { <span class="chip">{{ f }}</span> }
          </p>
          @if (demoChart(); as c) { <app-chart [config]="c" [height]="260" /> }
          @if (r.note; as n) { <p class="subtle small" style="margin-top: 10px">{{ n }}</p> }
        }
      </section>
    }

    @if (tab() === 'network') {
      @if (network(); as net) {
        <div class="stats">
          <div class="stat"><span class="stat-label">Sites</span><span class="stat-value">{{ net.totals.sites | number }}</span></div>
          <div class="stat"><span class="stat-label">Sensors</span><span class="stat-value">{{ net.totals.channels | number }}</span></div>
          <div class="stat"><span class="stat-label">Readings</span><span class="stat-value">{{ net.totals.readings | number }}</span></div>
          <div class="stat"><span class="stat-label">Offline now</span><span class="stat-value" [class.crit]="net.totals.silent_channels > 0">{{ net.totals.silent_channels }}</span><span class="stat-sub">sensors</span></div>
        </div>
        <section class="card">
          <h2>Sites needing attention</h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Site</th><th class="num">Units</th><th class="num">Trust</th><th class="num">Low-trust periods</th><th class="num">Possible tampering</th><th class="num">Offline sensors</th></tr></thead>
              <tbody>
                @for (s of networkTop(); track s.site_id) {
                  <tr>
                    <td>{{ s.site_name }}</td>
                    <td class="num">{{ s.units }}</td>
                    <td class="num"><strong>{{ s.avg_trust | number: '1.1-1' }}</strong></td>
                    <td class="num">{{ s.low_windows }}</td>
                    <td class="num">{{ s.integrity_windows }}</td>
                    <td class="num">
                      @if (s.premises_silent) { <span class="pill Low">Whole site</span> } @else { {{ s.silent_channels }} }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <div class="table-foot"><span>Lowest {{ networkTop().length }} of {{ net.sites.length }} sites by trust</span></div>
        </section>
      }
    }

    @if (tab() === 'accuracy') {
      @if (evaluation(); as ev) {
        <div class="stats">
          <div class="stat"><span class="stat-label">Precision</span><span class="stat-value">{{ ev.overall.precision | number: '1.2-2' }}</span><span class="stat-sub">flags that were real faults</span></div>
          <div class="stat"><span class="stat-label">Recall</span><span class="stat-value">{{ ev.overall.recall | number: '1.2-2' }}</span><span class="stat-sub">faults that were caught</span></div>
          <div class="stat"><span class="stat-label">F1 score</span><span class="stat-value">{{ ev.overall.f1 | number: '1.2-2' }}</span><span class="stat-sub">balance of the two</span></div>
          <div class="stat"><span class="stat-label">Accuracy</span><span class="stat-value">{{ ev.overall.accuracy | number: '1.2-2' }}</span><span class="stat-sub">correct calls overall</span></div>
        </div>

        <section class="card">
          <h2>Detection rate by fault type</h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Fault</th><th>Caught by</th><th class="num">Detection rate</th><th></th></tr></thead>
              <tbody>
                @for (f of ev.per_fault; track f.fault) {
                  <tr>
                    <td>{{ f.fault }}</td>
                    <td class="subtle">{{ f.expected_check || '—' }}</td>
                    <td class="num"><strong>{{ f.recall * 100 | number: '1.0-0' }}%</strong></td>
                    <td>
                      @if (f.recall >= 0.99) { <span class="pill ok">Detected</span> }
                      @else if (f.recall > 0) { <span class="pill warning">Partly</span> }
                      @else { <span class="pill critical">Not detected</span> }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>

          <details class="about">
            <summary>Method and detail</summary>
            <div>
              <p>
                {{ ev.sample.positive_windows_per_fault | number }} clean windows &times; {{ ev.per_fault.length }} fault types were
                corrupted on purpose and scored with the production checks, against {{ ev.sample.negative_windows | number }} untouched
                real windows. Caught {{ ev.overall.tp | number }}, missed {{ ev.overall.fn | number }}, false alarms
                {{ ev.overall.fp | number }}, correctly left alone {{ ev.overall.tn | number }}.
              </p>
              <p>
                A subtle calibration offset cannot be seen in one sensor's data alone; the planned cross-sensor check addresses it.
                Measured {{ fmtTime(ev.generated_at) }}.
              </p>
            </div>
          </details>
        </section>
      } @else {
        <div class="card empty">The accuracy evaluation has not been run yet.</div>
      }
    }

    @if (tab() === 'security' && auth.hasRole('quality_supervisor')) {
      <section class="card">
        <h2>Blocked access attempts</h2>
        <p class="subtle">Devices that tried to send data with a wrong key or without being registered.</p>
        @if (security(); as sec) {
          <div class="row" style="margin-bottom: 12px">
            <span class="pill" [class]="sec.total ? 'warning' : 'ok'">{{ sec.total }} in the last 24 hours</span>
          </div>
          @if (sec.events.length) {
            <div class="table-wrap">
              <table>
                <thead><tr><th>Reason</th><th>Device</th><th>When</th></tr></thead>
                <tbody>
                  @for (e of sec.events.slice(0, 15); track e.id) {
                    <tr>
                      <td><span class="pill warning">{{ eventText(e.event_type) }}</span></td>
                      <td>{{ e.device_id || '—' }}</td>
                      <td class="subtle">{{ fmtTime(e.occurred_at) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          } @else {
            <div class="empty">No blocked attempts.</div>
          }
        }
      </section>
    }
  `,
  styles: [`
    .stackbar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--border); min-width: 120px; }
    .seg-High { background: var(--ok); }
    .seg-Medium { background: #d99a0b; }
    .seg-Low { background: var(--crit); }
    tr.clickable { cursor: pointer; }
    tr.sel td { background: var(--accent-soft); }
    td .chip { margin-bottom: 0; }
    .verdict { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 14px; border-radius: 10px; background: var(--ok-bg); }
    .verdict.miss { background: var(--warn-bg); }
    .score { font-variant-numeric: tabular-nums; }
    details.about p { margin: 0 0 6px; }
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
  tab = signal<'overview' | 'test' | 'network' | 'accuracy' | 'security'>('overview');
  tabs = computed(() => [
    { key: 'overview' as const, label: 'Overview' },
    { key: 'test' as const, label: 'Test a fault' },
    { key: 'network' as const, label: 'All sites' },
    { key: 'accuracy' as const, label: 'Accuracy' },
    ...(this.auth.hasRole('quality_supervisor') ? [{ key: 'security' as const, label: 'Security' }] : []),
  ]);

  overall = computed(() => {
    const u = this.summary();
    if (!u.length) return null;
    const readings = u.reduce((a, x) => a + x.readings, 0);
    const excluded = u.reduce((a, x) => a + x.excluded_from_analytics, 0);
    const windows = u.reduce((a, x) => a + x.windows, 0);
    return {
      readings,
      excluded,
      excludedPct: (excluded / readings) * 100,
      windows,
      low: u.reduce((a, x) => a + x.labels.Low, 0),
      trust: u.reduce((a, x) => a + x.avg_trust * x.windows, 0) / windows,
    };
  });

  timelineName = computed(() => this.summary().find((u) => u.device_id === this.timelineDevice())?.unit_name ?? '');

  noFlags(u: UnitQuality) {
    return Object.values(u.windows_flagged).every((v) => !v);
  }

  eventText(t: string) {
    return t === 'auth_failed' ? 'Wrong device key' : t === 'unknown_device' ? 'Unregistered device' : t;
  }

  timelineChart = computed<ChartConfiguration | null>(() => {
    const t = this.timeline();
    if (!t.length) return null;
    return {
      type: 'line',
      data: {
        labels: t.map((w) => fmtTs(w.window_start)),
        datasets: [
          { label: 'Trust score', data: t.map((w) => w.trust), borderColor: '#0f766e', backgroundColor: '#0f766e', borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
          { label: 'High (85+)', data: t.map(() => 85), borderColor: '#14743f', borderDash: [6, 4], borderWidth: 1, pointRadius: 0 },
          { label: 'Low (below 60)', data: t.map(() => 60), borderColor: '#b3261e', borderDash: [6, 4], borderWidth: 1, pointRadius: 0 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 14, boxHeight: 2 } } },
        scales: { x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } }, y: { min: 0, max: 100, grid: { color: '#eef2f3' } } },
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
          { label: 'Flagged', data: xy(flaggedPts), showLine: false, borderColor: '#b3261e', backgroundColor: '#b3261e', pointStyle: 'triangle', pointRadius: 5 },
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
      error: () => this.error.set('Could not load data quality. Check the connection and refresh.'),
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
        this.error.set('The test could not run. Try again.');
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
      ['noise', 'Noise'],
      ['integrity', 'Possible tampering'],
    ];
    return names.filter(([k]) => f[k]).map(([, label]) => label);
  }

  verdictText(r: InjectResult): string {
    if (!r.detected) return 'This fault cannot be seen from one sensor alone.';
    if (r.after.score.label === 'High') return 'The bad readings are removed; the rest of the data stays trusted.';
    return r.after.score.label === 'Low' ? 'The whole period is marked untrustworthy.' : 'Trust for the whole period drops to Medium.';
  }
}
