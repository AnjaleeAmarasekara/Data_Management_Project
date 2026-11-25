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
        <h1>Monitoring</h1>
        <p class="subtle">Live status of every fridge and freezer.</p>
      </div>
      <div class="row subtle small" aria-live="polite">
        <span class="live-dot" [class.off]="error()"></span>
        {{ error() ? 'Offline' : 'Live' }}
        @if (updated(); as u) { <span>&middot; updated {{ u.toLocaleTimeString() }}</span> }
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    @if (units().length) {
      <div class="stats">
        <div class="stat"><span class="stat-label">Units</span><span class="stat-value">{{ units().length }}</span></div>
        <div class="stat"><span class="stat-label">Normal</span><span class="stat-value ok">{{ count('ok') }}</span></div>
        <div class="stat"><span class="stat-label">Needs attention</span><span class="stat-value warn">{{ count('warning') + count('warming_up') + count('no_data') }}</span></div>
        <div class="stat"><span class="stat-label">Critical</span><span class="stat-value crit">{{ count('critical') }}</span></div>
      </div>
    }

    <div class="grid cols-units">
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
          [title]="u.categories.join(', ')"
        >
          <div class="row nowrap">
            <span class="unit-name">{{ u.unit_name }}</span>
            <span class="spacer"></span>
            <span class="pill" [class]="u.status">{{ statusText(u.status) }}</span>
          </div>
          @if (u.clock === 'wall') { <p class="live-tag">Live probe &middot; {{ u.device_id }}</p> }

          @if (u.latest; as l) {
            <div class="reading">
              <span class="big">{{ l.temp_c | number: '1.1-1' }}&deg;C</span>
              @if (l.humidity !== null) { <span class="subtle small">{{ l.humidity | number: '1.0-0' }}% RH</span> }
            </div>
            <p class="subtle small band">
              @if (u.min_safe_c !== null && u.min_safe_c !== undefined) { Safe {{ u.min_safe_c }} to {{ u.max_safe_c }}&deg;C } @else { Safe up to {{ u.max_safe_c }}&deg;C }
            </p>
          } @else {
            <p class="subtle small band" style="margin-top: 14px">Waiting for the first reading.</p>
          }

          @if (issue(u); as i) { <div class="issue" [class]="i.tone">{{ i.text }}</div> }

          <div class="foot">
            @if (u.trust; as t) {
              <span class="subtle small">Data trust</span>
              <span class="trust-val" [class]="t.label">{{ t.value | number: '1.0-0' }}</span>
              @for (f of activeFlags(t.flags); track f) { <span class="chip">{{ f }}</span> }
            } @else {
              <span class="subtle small">Data trust not scored yet</span>
            }
          </div>
        </article>
      } @empty {
        @if (!error()) {
          <div class="skeleton" aria-busy="true" aria-label="Loading units"></div>
          <div class="skeleton"></div>
          <div class="skeleton"></div>
        }
      }
    </div>

    <section class="card" style="margin-top: 16px">
      <div class="row" style="margin-bottom: 12px">
        <h2 style="margin: 0">Temperature &middot; {{ selectedName() }}</h2>
        <span class="spacer"></span>
        <select aria-label="Unit" [value]="selected()" (change)="select($any($event.target).value)">
          @for (u of units(); track u.device_id) { <option [value]="u.device_id" [selected]="u.device_id === selected()">{{ u.unit_name }}</option> }
        </select>
        <div class="seg" role="group" aria-label="Time range">
          @for (r of ranges; track r.hours) {
            <button [class.on]="r.hours === hours()" (click)="setHours(r.hours)">{{ r.label }}</button>
          }
        </div>
      </div>
      @if (chart(); as c) {
        <app-chart [config]="c" [height]="300" />
      } @else {
        <p class="subtle">No readings in this period.</p>
      }
    </section>

    <section class="card" style="margin-top: 16px">
      <div class="row" style="margin-bottom: 6px">
        <h2 style="margin: 0">Recent alerts</h2>
        <span class="spacer"></span>
        <a class="link" routerLink="/alerts">View all</a>
      </div>
      @for (a of alerts(); track a.id) {
        <div class="alert-line">
          <span class="pill" [class]="a.severity">{{ a.severity }}</span>
          <span class="alert-text"><strong>{{ a.unit_name }}</strong><span class="subtle"> &middot; {{ alertText(a) }}</span></span>
          <span class="subtle small">{{ fmt(a.start_ts) }}</span>
        </div>
      } @empty {
        <p class="subtle">No alerts.</p>
      }
    </section>
  `,
  styles: [`
    .cols-units { grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
    .unit { cursor: pointer; display: flex; flex-direction: column; padding: 16px; transition: box-shadow 160ms var(--ease), border-color 160ms var(--ease), transform 160ms var(--ease); }
    .unit:hover { box-shadow: var(--shadow-raised); transform: translateY(-1px); }
    .unit.is-critical { box-shadow: inset 0 3px 0 var(--crit), var(--shadow); }
    .unit.is-warning { box-shadow: inset 0 3px 0 #d99a0b, var(--shadow); }
    .unit.selected { border-color: var(--accent); }
    .unit:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .unit-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
    .reading { display: flex; align-items: baseline; gap: 8px; margin: 12px 0 0; }
    .band { margin: 2px 0 0; }
    .issue { margin-top: 10px; padding: 6px 10px; border-radius: 7px; font-size: 12.5px; font-weight: 600; }
    .issue.crit { background: var(--crit-bg); color: var(--crit); }
    .issue.warn { background: var(--warn-bg); color: var(--warn); }
    .issue.info { background: var(--watch-bg); color: var(--watch); }
    .foot { margin-top: auto; padding-top: 12px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .foot .chip { margin: 0; }
    .trust-val { font-weight: 650; font-variant-numeric: tabular-nums; }
    .trust-val.High { color: var(--ok); }
    .trust-val.Medium { color: var(--warn); }
    .trust-val.Low { color: var(--crit); }
    .alert-line { display: grid; grid-template-columns: 84px 1fr auto; gap: 12px; align-items: center; padding: 9px 0; border-bottom: 1px solid var(--border); }
    .alert-line:last-child { border-bottom: 0; }
    .alert-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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

  count(status: string) {
    return this.units().filter((u) => u.status === status).length;
  }

  selectedName() {
    return this.units().find((u) => u.device_id === this.selected())?.unit_name ?? '';
  }

  /** One short line saying what is wrong, or nothing when the unit is fine.
   *  The forecast wording stays hedged ("trending"): the walk-forward backtest does not support
   *  stating a breach time as fact (docs/ml_evaluation_arima.json). */
  issue(u: UnitState): { text: string; tone: 'crit' | 'warn' | 'info' } | null {
    const by = this.outsideBandBy(u);
    const c = u.connectivity;
    switch (u.breach) {
      case 'too_warm': return { text: `${by?.toFixed(1)}°C above the safe limit`, tone: 'crit' };
      case 'too_cold': return { text: `${by?.toFixed(1)}°C below the safe limit`, tone: 'crit' };
      case 'silent': return { text: `No readings for ${Math.round(c?.silent_for_min ?? 0)} min`, tone: 'crit' };
      case 'premises_silent': return { text: `Site offline for ${Math.round(c?.premises_silent_for_min ?? 0)} min`, tone: 'crit' };
    }
    if (u.warmup) return { text: `Settling in, ${Math.round(u.warmup.remaining_min)} min left`, tone: 'info' };
    const f = u.forecast;
    if (f?.status === 'at_risk' && f.minutes_to_limit) {
      return { text: `Trending ${f.direction === 'cold' ? 'too cold' : 'too warm'}, ~${Math.round(f.minutes_to_limit)} min to limit`, tone: 'warn' };
    }
    return null;
  }

  /** The alert message without its trailing explanation ("... - chilled stock can freeze"). */
  alertText(a: Alert) {
    return a.message.split(' - ')[0];
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
      ['integrity', 'Possible tampering'],
    ];
    return names.filter(([k]) => f[k]).map(([, label]) => label);
  }

  private loadUnits() {
    this.api.units().subscribe({
      next: (units) => {
        this.units.set(units);
        this.error.set(null);
        this.updated.set(new Date());
        if (!this.selected() && units.length) this.select(units[0].device_id);
      },
      error: () => this.error.set('Connection lost. Reconnecting automatically.'),
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
        { label: `Upper limit (${r.max_safe_c}°C)`, data: r.points.map(() => r.max_safe_c ?? null), borderColor: '#b3261e', borderDash: [6, 4], borderWidth: 1.2, pointRadius: 0 },
        // the lower bound matters too: below it, chilled stock freezes
        ...(r.min_safe_c !== null && r.min_safe_c !== undefined
          ? [{ label: `Lower limit (${r.min_safe_c}°C)`, data: r.points.map(() => r.min_safe_c ?? null), borderColor: '#2b6cb0', borderDash: [2, 4], borderWidth: 1.2, pointRadius: 0 }]
          : []),
        { label: 'Flagged reading', data: flagged, showLine: false, borderColor: '#b3261e', backgroundColor: '#b3261e', pointStyle: 'triangle', pointRadius: 5 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 14, boxHeight: 2 } } },
      scales: {
        x: { ticks: { maxTicksLimit: 10, autoSkip: true }, grid: { display: false } },
        y: { title: { display: true, text: '°C' }, grid: { color: '#eef2f3' } },
      },
    },
  };
}
