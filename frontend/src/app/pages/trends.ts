import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { ChartConfiguration } from 'chart.js';

import { ApiService, TrendBucket, Trends, TrendsOverview, UnitState } from '../shared/api.service';
import { ChartComponent } from '../shared/chart';
import { downloadCsv, fmtTs } from '../shared/format';

const BUCKETS: { key: TrendBucket; label: string }[] = [
  { key: 'hour', label: 'Hourly' },
  { key: '6h', label: '6 h' },
  { key: 'day', label: 'Daily' },
];

// Deliberately avoids the orange and blue that mean "too warm" and "too cold" in the chart
// above: drawing the freezing top shelf in the warm colour would say the opposite of the data.
const UNIT_COLOURS = ['#455a64', '#8e5aa6', '#5d8a2f', '#b08900', '#1b8a8a'];

@Component({
  selector: 'app-trends',
  imports: [ChartComponent, DecimalPipe],
  template: `
    <div class="page-head">
      <div>
        <h1>Trends</h1>
        <p class="subtle">How each unit has performed over time.</p>
      </div>
      <div class="row">
        <select aria-label="Unit" (change)="select($any($event.target).value)">
          @for (u of units(); track u.device_id) { <option [value]="u.device_id" [selected]="u.device_id === selected()">{{ u.unit_name }}</option> }
        </select>
        <div class="seg" role="group" aria-label="Granularity">
          @for (b of buckets; track b.key) {
            <button [class.on]="b.key === bucket()" (click)="setBucket(b.key)">{{ b.label }}</button>
          }
        </div>
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    @if (data(); as d) {
      <div class="stats">
        <div class="stat"><span class="stat-label">Time too warm</span><span class="stat-value" [class.crit]="d.totals.minutes_too_warm">{{ hours(d.totals.minutes_too_warm) }}</span></div>
        <div class="stat"><span class="stat-label">Time too cold</span><span class="stat-value" [class.crit]="d.totals.minutes_too_cold">{{ hours(d.totals.minutes_too_cold) }}</span></div>
        <div class="stat"><span class="stat-label">Data received</span><span class="stat-value">{{ (d.totals.completeness ?? 0) * 100 | number: '1.0-0' }}%</span></div>
        <div class="stat"><span class="stat-label">Readings</span><span class="stat-value">{{ d.totals.readings | number }}</span><span class="stat-sub">{{ d.totals.excluded }} excluded as unreliable</span></div>
      </div>

      <section class="card">
        <h2>Temperature</h2>
        @if (tempChart(); as c) { <app-chart [config]="c" [height]="300"></app-chart> }
      </section>

      <div class="grid cols-2" style="margin-top: 16px">
        <section class="card">
          <h2>Time out of range</h2>
          @if (breachChart(); as c) { <app-chart [config]="c" [height]="220"></app-chart> }
        </section>
        <section class="card">
          <h2>Data trust</h2>
          @if (trustChart(); as c) { <app-chart [config]="c" [height]="220"></app-chart> }
        </section>
      </div>
    }

    @if (overviewChart(); as c) {
      <section class="card" style="margin-top: 16px">
        <h2>All units: daily time out of range</h2>
        <app-chart [config]="c" [height]="240"></app-chart>
      </section>
    }

    @if (data(); as d) {
      <section class="card" style="margin-top: 16px">
        <div class="row">
          <h2 style="margin: 0">Data table</h2>
          <span class="spacer"></span>
          <button (click)="showTable.set(!showTable())">{{ showTable() ? 'Hide' : 'Show' }}</button>
          <button (click)="exportCsv()">Export CSV</button>
        </div>
        @if (showTable()) {
          <div class="table-wrap" style="margin-top: 12px">
            <table>
              <thead>
                <tr>
                  <th>Period</th><th class="num">Min</th><th class="num">Mean</th><th class="num">Max</th>
                  @if (d.has_product_probe) { <th class="num">Product</th> }
                  <th class="num">Room</th><th class="num">Too warm</th><th class="num">Too cold</th>
                  <th class="num">Data received</th><th class="num">Trust</th>
                </tr>
              </thead>
              <tbody>
                @for (p of d.points; track p.bucket_start) {
                  <tr>
                    <td>{{ label(p.bucket_start) }}</td>
                    <td class="num">{{ p.temp_min | number: '1.1-1' }}</td>
                    <td class="num">{{ p.temp_mean | number: '1.1-1' }}</td>
                    <td class="num">{{ p.temp_max | number: '1.1-1' }}</td>
                    @if (d.has_product_probe) { <td class="num">{{ p.product_mean | number: '1.1-1' }}</td> }
                    <td class="num">{{ p.ambient_mean | number: '1.1-1' }}</td>
                    <td class="num">{{ p.minutes_too_warm ? p.minutes_too_warm + ' min' : '—' }}</td>
                    <td class="num">{{ p.minutes_too_cold ? p.minutes_too_cold + ' min' : '—' }}</td>
                    <td class="num">{{ p.completeness * 100 | number: '1.0-0' }}%</td>
                    <td class="num">{{ p.avg_trust | number: '1.0-0' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      </section>
    }
  `,
})
export class TrendsPage implements OnInit {
  private api = inject(ApiService);
  readonly buckets = BUCKETS;

  units = signal<UnitState[]>([]);
  selected = signal<string | null>(null);
  bucket = signal<TrendBucket>('6h');
  data = signal<Trends | null>(null);
  overview = signal<TrendsOverview | null>(null);
  error = signal<string | null>(null);
  showTable = signal(false);

  bucketNoun = computed(() => ({ hour: 'hour', '6h': 'six hours', day: 'day' })[this.bucket()]);

  ngOnInit() {
    this.api.units().subscribe({
      next: (u) => {
        this.units.set(u);
        if (u.length) this.select(u[0].device_id);
      },
      error: () => this.error.set('Could not load trends. Check the connection and refresh.'),
    });
    this.api.trendsOverview('day').subscribe({ next: (o) => this.overview.set(o), error: () => {} });
  }

  select(deviceId: string) {
    this.selected.set(deviceId);
    this.load();
  }

  setBucket(b: TrendBucket) {
    this.bucket.set(b);
    this.load();
  }

  private load() {
    const id = this.selected();
    if (!id) return;
    this.api.trends(id, this.bucket()).subscribe({
      next: (d) => this.data.set(d),
      error: () => this.error.set('Could not load trends for this unit.'),
    });
  }

  hours(minutes: number): string {
    if (!minutes) return '0 h';
    return minutes < 60 ? `${minutes} min` : `${(minutes / 60).toFixed(1)} h`;
  }

  label(iso: string): string {
    return this.bucket() === 'day' ? fmtTs(iso).split(' ').slice(0, 2).join(' ') : fmtTs(iso);
  }

  tempChart = computed<ChartConfiguration | null>(() => {
    const d = this.data();
    if (!d || !d.points.length) return null;
    const labels = d.points.map((p) => this.label(p.bucket_start));
    const flat = (v: number | null) => d.points.map(() => v);
    const datasets: ChartConfiguration['data']['datasets'] = [
      { label: 'Warmest', data: d.points.map((p) => p.temp_max), borderColor: 'rgba(11,99,182,0.25)', backgroundColor: 'rgba(11,99,182,0.12)', borderWidth: 1, pointRadius: 0, fill: '+1' },
      { label: 'Coldest', data: d.points.map((p) => p.temp_min), borderColor: 'rgba(11,99,182,0.25)', borderWidth: 1, pointRadius: 0, fill: false },
      { label: 'Mean', data: d.points.map((p) => p.temp_mean), borderColor: '#0f766e', borderWidth: 2, pointRadius: 0, tension: 0.2 },
    ];
    if (d.has_product_probe) {
      datasets.push({ label: 'Product temperature', data: d.points.map((p) => p.product_mean), borderColor: '#b45309', borderWidth: 2, pointRadius: 0, tension: 0.2 });
    }
    datasets.push({ label: `Safe limit (${d.max_safe_c}°C)`, data: flat(d.max_safe_c), borderColor: '#b3261e', borderDash: [6, 4], borderWidth: 1.2, pointRadius: 0 });
    if (d.min_safe_c !== null) {
      datasets.push({ label: `Lower limit (${d.min_safe_c}°C)`, data: flat(d.min_safe_c), borderColor: '#2b6cb0', borderDash: [2, 4], borderWidth: 1.2, pointRadius: 0 });
    }
    return {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 18 } } },
        scales: { y: { title: { display: true, text: '°C' } }, x: { ticks: { maxTicksLimit: 10 } } },
      },
    };
  });

  breachChart = computed<ChartConfiguration | null>(() => {
    const d = this.data();
    if (!d || !d.points.length) return null;
    return {
      type: 'bar',
      data: {
        labels: d.points.map((p) => this.label(p.bucket_start)),
        datasets: [
          { label: 'Too warm', data: d.points.map((p) => p.minutes_too_warm), backgroundColor: '#c2571a' },
          { label: 'Too cold', data: d.points.map((p) => p.minutes_too_cold), backgroundColor: '#2b6cb0' },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 18 } } },
        scales: { x: { stacked: true, ticks: { maxTicksLimit: 8 } }, y: { stacked: true, title: { display: true, text: 'minutes' } } },
      },
    };
  });

  trustChart = computed<ChartConfiguration | null>(() => {
    const d = this.data();
    if (!d || !d.points.length) return null;
    return {
      type: 'line',
      data: {
        labels: d.points.map((p) => this.label(p.bucket_start)),
        datasets: [
          { label: 'Mean trust', data: d.points.map((p) => p.avg_trust), borderColor: '#0f766e', borderWidth: 2, pointRadius: 0 },
          { label: 'Completeness (%)', data: d.points.map((p) => Math.round(p.completeness * 100)), borderColor: '#7b4fa6', borderDash: [4, 3], borderWidth: 1.5, pointRadius: 0 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 18 } } },
        scales: { y: { min: 0, max: 100 }, x: { ticks: { maxTicksLimit: 8 } } },
      },
    };
  });

  overviewChart = computed<ChartConfiguration | null>(() => {
    const o = this.overview();
    if (!o || !o.units.length) return null;
    const labels = o.units[0].points.map((p) => fmtTs(p.bucket_start).split(' ').slice(0, 2).join(' '));
    return {
      type: 'bar',
      data: {
        labels,
        datasets: o.units.map((u, i) => ({
          label: u.unit_name,
          data: u.points.map((p) => p.minutes_too_warm + p.minutes_too_cold),
          backgroundColor: UNIT_COLOURS[i % UNIT_COLOURS.length],
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 18 } } },
        scales: { y: { title: { display: true, text: 'minutes outside band' } } },
      },
    };
  });

  exportCsv() {
    const d = this.data();
    if (!d) return;
    const rows: (string | number | null)[][] = [
      ['period_start_utc', 'temp_min', 'temp_mean', 'temp_max', 'product_mean', 'ambient_mean', 'minutes_too_warm', 'minutes_too_cold', 'readings', 'excluded', 'completeness', 'avg_trust'],
      ...d.points.map((p) => [p.bucket_start, p.temp_min, p.temp_mean, p.temp_max, p.product_mean, p.ambient_mean, p.minutes_too_warm, p.minutes_too_cold, p.readings, p.excluded, p.completeness, p.avg_trust]),
    ];
    downloadCsv(`trends_${d.device_id}_${d.bucket}.csv`, rows);
  }
}
