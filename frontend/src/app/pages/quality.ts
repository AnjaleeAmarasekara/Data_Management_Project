import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { ChartConfiguration } from 'chart.js';

import { ApiService, FaultName, Flags, InjectResult, Point, SecurityEvents, TimelineItem, UnitQuality } from '../shared/api.service';
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
];

@Component({
  selector: 'app-quality',
  imports: [ChartComponent, DecimalPipe],
  template: `
    <div class="page-head">
      <div>
        <h1>Data Quality</h1>
        <p class="subtle">
          Every 30-minute window of sensor data is scored before analytics is allowed to use it.
          High &ge; 85, Medium &ge; 60, Low below that.
        </p>
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <div class="grid cols-3">
      @for (u of summary(); track u.device_id) {
        <article class="card">
          <div class="row">
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
      <h2>Fault injection demo</h2>
      <p class="subtle">
        Takes a known-clean 30-minute window of real data, corrupts a copy in memory with a synthetic fault, and runs the
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

    @if (auth.hasRole('quality_supervisor')) {
      <section class="card" style="margin-top: 14px">
        <h2>Ingestion Security</h2>
        <p class="subtle">
          Unauthorized attempts to push data into the ingestion API &mdash; a wrong or missing device key, or a push
          from a device_id that isn't a registered cold-storage unit. This is distinct from data-quality flags: it's
          about who tried to get in, not whether accepted data can be trusted.
        </p>
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
    .stackbar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #e8ecf2; }
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

  timelineChart = computed<ChartConfiguration | null>(() => {
    const t = this.timeline();
    if (!t.length) return null;
    return {
      type: 'line',
      data: {
        labels: t.map((w) => fmtTs(w.window_start)),
        datasets: [
          { label: 'Trust score', data: t.map((w) => w.trust), borderColor: '#0b63b6', backgroundColor: '#0b63b6', borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
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
          { label: 'After fault', data: xy(r.after.points), borderColor: '#0b63b6', borderWidth: 1.4, pointRadius: 0 },
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
