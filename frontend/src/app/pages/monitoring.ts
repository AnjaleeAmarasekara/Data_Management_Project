import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ChartConfiguration } from 'chart.js';

import { Alert, ApiService, Flags, Readings, UnitState } from '../shared/api.service';
import { ChartComponent } from '../shared/chart';
import { fmtTs } from '../shared/format';

const STATUS_TEXT: Record<string, string> = {
  ok: 'Normal',
  warning: 'Warning',
  critical: 'Critical',
  no_data: 'Awaiting data',
  warming_up: 'Warming up',
};
const RANGES = [
  { hours: 2, label: '2 h' },
  { hours: 6, label: '6 h' },
  { hours: 24, label: '24 h' },
  { hours: 72, label: '72 h' },
];

@Component({
  selector: 'app-monitoring',
  imports: [ChartComponent, DecimalPipe, RouterLink],
  template: `
    <div class="page-head">
      <div>
        <h1>Real-Time Monitoring</h1>
        <p class="subtle">Latest state of each cold-storage unit, with the data-quality trust score behind every number.</p>
      </div>
      <div class="row subtle" aria-live="polite">
        <span class="pill" [class.ok]="!error()" [class.critical]="error()">{{ error() ? 'Offline' : 'Live' }}</span>
        @if (updated(); as u) { <span>Updated {{ u.toLocaleTimeString() }}</span> }
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <div class="grid cols-3" style="margin-top: 14px">
      @for (u of units(); track u.device_id) {
        <article
          class="card unit"
          [class.selected]="u.device_id === selected()"
          [class.is-critical]="u.status === 'critical'"
          [class.is-warning]="u.status === 'warning'"
          tabindex="0"
          (click)="select(u.device_id)"
          (keydown.enter)="select(u.device_id)"
          [attr.aria-label]="'Show chart for ' + u.unit_name"
        >
          <div class="row">
            <strong>{{ u.unit_name }}</strong>
            <span class="spacer"></span>
            <span class="pill" [class]="u.status">{{ statusText(u.status) }}</span>
          </div>
          @if (u.clock === 'wall') {
            <p class="live-tag" title="Silence is judged against the real clock, not against the replayed record">Live probe &middot; {{ u.device_id }}</p>
          }

          @if (u.status === 'no_data') {
            <p class="subtle meta" style="margin-top: 10px">
              Registered. No reading has arrived yet; the card goes live with the probe's first push.
            </p>
          }

          @if (u.latest; as l) {
            <div class="row" style="align-items: baseline; margin: 10px 0 2px">
              <span class="big">{{ l.temp_c | number: '1.1-1' }}&deg;C</span>
              @if (l.humidity !== null) { <span class="subtle">{{ l.humidity | number: '1.0-0' }}% RH</span> }
            </div>
            <p class="subtle meta">
              Setpoint {{ u.setpoint_c }}&deg;C &middot; safe band
              @if (u.min_safe_c !== null && u.min_safe_c !== undefined) { {{ u.min_safe_c }}&deg;C to }
              {{ u.max_safe_c }}&deg;C
            </p>
            <p class="cats">{{ u.categories.join(', ') }}</p>
            @if (u.breach === 'too_warm') {
              <div class="breach">
                <strong>{{ outsideBandBy(u) | number: '1.1-1' }}&deg;C above the safe limit</strong>
                <span>Spoilage risk grows with time spent above it.</span>
              </div>
            }
            @if (u.breach === 'too_cold') {
              <div class="breach">
                <strong>{{ outsideBandBy(u) | number: '1.1-1' }}&deg;C below the safe band</strong>
                <span>Chilled stock can freeze and be ruined.</span>
              </div>
            }
            @if (u.warmup; as w) {
              <div class="settling">
                <strong>Settling: {{ w.remaining_min | number: '1.0-0' }} of {{ w.warmup_min }} min left</strong>
                <span>A newly fitted probe starts at room temperature. Safe limits are enforced once it has settled; silence is reported straight away.</span>
              </div>
            }
            @if (u.breach === 'silent') {
              <div class="breach">
                <strong>No readings for {{ u.connectivity?.silent_for_min | number: '1.0-0' }} min</strong>
                <span>The rest of this premises is reporting, so the sensor or its battery has likely failed. The temperature shown is the last one received.</span>
              </div>
            }
            @if (u.breach === 'premises_silent') {
              <div class="breach">
                <strong>Whole premises silent for {{ u.connectivity?.premises_silent_for_min | number: '1.0-0' }} min</strong>
                <span>Check the gateway, network or power. The temperature shown is the last one received.</span>
              </div>
            }
          }

          @if (u.trust; as t) {
            <div class="trust">
              <div class="row">
                <span class="kpi-label">Data trust</span>
                <span class="spacer"></span>
                <strong>{{ t.value | number: '1.1-1' }}</strong><span class="subtle">/ 100</span>
                <span class="pill" [class]="t.label">{{ t.label }}</span>
              </div>
              <div class="meter" role="img" [attr.aria-label]="'Trust score ' + t.value + ' out of 100'">
                <div class="meter-fill" [class]="t.label" [style.width.%]="t.value"></div>
              </div>
              <div>
                @for (f of activeFlags(t.flags); track f) { <span class="chip">{{ f }}</span> }
                @empty { <span class="chip clear">No quality issues</span> }
              </div>
            </div>
          }

          @if (forecastText(u); as fc) { <p class="subtle forecast">{{ fc }}</p> }
        </article>
      } @empty {
        @if (!error()) {
          <div class="skeleton" aria-busy="true" aria-label="Loading units"></div>
          <div class="skeleton"></div>
          <div class="skeleton"></div>
        }
      }
    </div>

    <section class="card" style="margin-top: 14px">
      <div class="row" style="margin-bottom: 10px">
        <h2 style="margin: 0">Temperature history</h2>
        <span class="spacer"></span>
        <div class="seg" role="group" aria-label="Unit">
          @for (u of units(); track u.device_id) {
            <button [class.on]="u.device_id === selected()" (click)="select(u.device_id)">{{ u.unit_name }}</button>
          }
        </div>
        <div class="seg" role="group" aria-label="Time range">
          @for (r of ranges; track r.hours) {
            <button [class.on]="r.hours === hours()" (click)="setHours(r.hours)">{{ r.label }}</button>
          }
        </div>
      </div>
      @if (chart(); as c) {
        <app-chart [config]="c" [height]="320" />
        <p class="subtle" style="margin-top: 8px">
          Red markers are readings the data quality layer flagged as outliers or noise. They are excluded from
          analytics rather than plotted into the line. Temperatures are measured refrigeration readings, not simulated.
        </p>
      } @else {
        <p class="subtle">No readings to show.</p>
      }
    </section>

    <section class="card" style="margin-top: 14px">
      <div class="row" style="margin-bottom: 8px">
        <h2 style="margin: 0">Latest alerts</h2>
        <span class="spacer"></span>
        <a routerLink="/alerts">All alerts</a>
      </div>
      @for (a of alerts(); track a.id) {
        <div class="alert-line">
          <span class="pill" [class]="a.severity">{{ a.severity }}</span>
          <span><strong>{{ a.unit_name }}</strong> &mdash; {{ a.message }}</span>
          <span class="subtle">{{ fmt(a.start_ts) }}</span>
        </div>
      } @empty {
        <p class="subtle">No alerts.</p>
      }
    </section>
  `,
  styles: [`
    .unit { cursor: pointer; transition: box-shadow 160ms var(--ease), border-color 160ms var(--ease), transform 160ms var(--ease); }
    .unit:hover { box-shadow: var(--shadow-raised); transform: translateY(-1px); }
    .unit.is-critical { border-color: #efc4be; }
    .unit.is-warning { border-color: #ebd5a3; }
    .unit.selected { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent), var(--shadow); }
    .unit:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .trust { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
    .meter { height: 6px; background: var(--border); border-radius: 4px; overflow: hidden; }
    .meter-fill { height: 100%; }
    .meter-fill.High { background: var(--ok); }
    .meter-fill.Medium { background: #d99a0b; }
    .meter-fill.Low { background: var(--crit); }
    .forecast { margin: 10px 0 0; }
    .alert-line { display: grid; grid-template-columns: 76px 1fr auto; gap: 10px; align-items: center; padding: 7px 0; border-bottom: 1px solid var(--border); }
    .alert-line:last-child { border-bottom: 0; }
  `],
})
export class MonitoringPage implements OnInit, OnDestroy {
  private api = inject(ApiService);
  readonly ranges = RANGES;

  units = signal<UnitState[]>([]);
  selected = signal<string | null>(null);
  hours = signal(6);
  readings = signal<Readings | null>(null);
  alerts = signal<Alert[]>([]);
  error = signal<string | null>(null);
  updated = signal<Date | null>(null);
  private timers: ReturnType<typeof setInterval>[] = [];

  chart = computed<ChartConfiguration | null>(() => {
    const r = this.readings();
    if (!r || !r.points.length) return null;
    return buildChart(r, this.hours());
  });

  ngOnInit() {
    this.loadUnits();
    this.api.alerts(6).subscribe({ next: (a) => this.alerts.set(a), error: () => {} });
    this.timers.push(setInterval(() => this.loadUnits(), 5000));
    this.timers.push(setInterval(() => this.loadReadings(), 10000));
  }

  ngOnDestroy() {
    this.timers.forEach(clearInterval);
  }

  select(id: string) {
    this.selected.set(id);
    this.loadReadings();
  }

  setHours(h: number) {
    this.hours.set(h);
    this.loadReadings();
  }

  /** How far outside its safe band a unit is, in degrees, or null when it is inside.
   *
   *  The card states the magnitude because "Critical" on its own does not distinguish a unit a
   *  tenth of a degree over from one that has stopped cooling. Null for a silent sensor: the last
   *  reading says nothing about the temperature now, so a number there would be a false precision.
   */
  outsideBandBy(u: UnitState): number | null {
    const temp = u.latest?.temp_c;
    if (temp === undefined) return null;
    if (u.breach === 'too_warm') return temp - u.max_safe_c;
    if (u.breach === 'too_cold' && u.min_safe_c !== null) return u.min_safe_c - temp;
    return null;
  }

  statusText(s: string) {
    return STATUS_TEXT[s] ?? s;
  }

  fmt(ts: string) {
    return fmtTs(ts);
  }

  activeFlags(f: Flags): string[] {
    const names: [keyof Flags, string][] = [
      ['missing', 'Missing data'],
      ['outlier', 'Outliers'],
      ['drift', 'Drift'],
      ['noise', 'Noise'],
      ['integrity', 'Integrity (possible tampering)'],
    ];
    return names.filter(([k]) => f[k]).map(([, label]) => label);
  }

  // Wording is deliberately hedged. The walk-forward backtest (docs/ml_evaluation_arima.json)
  // shows the forecast beats a naive "no change" baseline on three of five units, and anticipated
  // four of fifteen excursions that began from a within-limit state. That does not support stating
  // a predicted breach time as fact, so the text reads as a watch signal, not a promise.
  //
  // Returns empty for a unit already outside its band: the breach line above the trust score
  // already says so, with the magnitude, and a forecast slot repeating it reads like a second,
  // weaker finding rather than the same one.
  forecastText(u: UnitState): string {
    const f = u.forecast;
    // a probe that has not reported, or is still settling, has its own message above
    if (u.status === 'no_data' || u.status === 'warming_up') return '';
    if (!f || f.status === 'insufficient_data') return 'Forecast unavailable (not enough recent data).';
    if (f.minutes_to_limit === 0) return '';
    if (f.status === 'at_risk') {
      const towards = f.direction === 'cold' ? 'freezing (the lower limit)' : 'the safe limit';
      return `Trending towards ${towards} — on current trend it could be reached within ${Math.round(
        f.minutes_to_limit ?? 0,
      )} min. Indicative only.`;
    }
    return `No breach indicated in the next ${f.horizon_min} min (trend points to ${f.predicted_c}°C).`;
  }

  private loadUnits() {
    this.api.units().subscribe({
      next: (units) => {
        this.units.set(units);
        this.error.set(null);
        this.updated.set(new Date());
        if (!this.selected() && units.length) this.select(units[0].device_id);
      },
      error: () => this.error.set('Cannot reach the API at localhost:8000. Start the backend (see README) and this page will reconnect.'),
    });
  }

  private loadReadings() {
    const id = this.selected();
    if (!id) return;
    this.api.readings(id, this.hours()).subscribe({ next: (r) => this.readings.set(r), error: () => {} });
  }
}

function buildChart(r: Readings, hours: number): ChartConfiguration {
  const withDate = hours > 12;
  const labels = r.points.map((p) => fmtTs(p.ts, withDate));
  const index = new Map(r.points.map((p, i) => [p.ts.slice(0, 16), i]));

  const flagged: (number | null)[] = r.points.map(() => null);
  for (const f of r.flags) {
    const i = index.get(f.ts.slice(0, 16));
    if (i !== undefined) flagged[i] = f.temp_c;
  }

  return {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Temperature (°C)', data: r.points.map((p) => p.temp_c), borderColor: '#0f766e', backgroundColor: '#0f766e', borderWidth: 1.6, pointRadius: 0, tension: 0.15 },
        { label: `Safe limit (${r.max_safe_c}°C)`, data: r.points.map(() => r.max_safe_c ?? null), borderColor: '#b3261e', borderDash: [6, 4], borderWidth: 1.2, pointRadius: 0 },
        // the lower bound matters too: below it, chilled stock freezes
        ...(r.min_safe_c !== null && r.min_safe_c !== undefined
          ? [{ label: `Lower limit (${r.min_safe_c}°C)`, data: r.points.map(() => r.min_safe_c ?? null), borderColor: '#2b6cb0', borderDash: [2, 4], borderWidth: 1.2, pointRadius: 0 }]
          : []),
        { label: 'Flagged by quality layer', data: flagged, showLine: false, borderColor: '#b3261e', backgroundColor: '#b3261e', pointStyle: 'triangle', pointRadius: 5 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { position: 'top', align: 'end' } },
      scales: {
        x: { ticks: { maxTicksLimit: 10, autoSkip: true }, grid: { display: false } },
        y: { title: { display: true, text: '°C' } },
      },
    },
  };
}
