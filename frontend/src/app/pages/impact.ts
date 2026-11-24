import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { ChartConfiguration } from 'chart.js';

import { ApiService, Impact } from '../shared/api.service';
import { AuthService } from '../shared/auth.service';
import { ChartComponent } from '../shared/chart';
import { downloadCsv } from '../shared/format';

const UNIT_COLORS = ['#0f766e', '#c2571a', '#5b7f2a', '#7a4fa3', '#2b6cb0'];

@Component({
  selector: 'app-impact',
  imports: [ChartComponent, DecimalPipe],
  template: `
    <div class="page-head">
      <div>
        <h1>Risk &amp; Business Impact</h1>
        <p class="subtle">
          What cold-chain excursions could cost: each unit's temperature history combined with that day's sales of the products it stores.
        </p>
      </div>
      @if (auth.hasRole('quality_supervisor')) {
        <button (click)="exportCsv()" [disabled]="!data()">Export CSV</button>
      }
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    @if (data(); as d) {
      <div class="grid cols-3">
        <div class="card">
          <div class="kpi-label">Estimated revenue at risk (LKR)</div>
          <div class="big" style="color: var(--crit)">{{ d.total_revenue_at_risk | number: '1.2-2' }}</div>
        </div>
        <div class="card">
          <div class="kpi-label">Sales exposed to cold-chain risk (LKR)</div>
          <div class="big">{{ d.total_sales_exposed | number: '1.2-2' }}</div>
        </div>
        <div class="card">
          <div class="kpi-label">Share of exposed sales at risk</div>
          <div class="big">{{ (d.total_revenue_at_risk / d.total_sales_exposed) * 100 | number: '1.1-1' }}%</div>
        </div>
      </div>

      <section class="card" style="margin-top: 14px">
        <h2>Revenue at risk per day (LKR)</h2>
        @if (chart(); as c) { <app-chart [config]="c" [height]="280" /> }
      </section>

      <section class="card" style="margin-top: 14px">
        <h2>By unit and day</h2>
        <div class="table-wrap">
          <table class="impact">
            <thead>
              <tr>
                <th>Unit</th><th class="num">Day</th><th>Products</th>
                <th class="num">Too warm (min)</th><th class="num">Too cold (min)</th><th class="num">Range (&deg;C)</th>
                <th class="num">Spoilage</th><th class="num">Sales (LKR)</th>
                <th class="num">At risk (LKR)</th><th>Based on</th><th class="num">Trust</th>
              </tr>
            </thead>
            <tbody>
              @for (r of d.rows; track r.device_id + r.relative_day) {
                <tr>
                  <td>{{ r.unit_name }}</td>
                  <td class="num">{{ r.relative_day }}</td>
                  <td class="wrap">{{ r.categories }}</td>
                  <td class="num">{{ r.excursion_minutes }}</td>
                  <td class="num">{{ r.cold_minutes }}</td>
                  <td class="num">{{ r.low_temp_c | number: '1.1-1' }} to {{ r.peak_temp_c | number: '1.1-1' }}</td>
                  <td class="num">{{ r.spoilage_probability * 100 | number: '1.0-0' }}%</td>
                  <td class="num">{{ r.sales_exposed | number: '1.2-2' }}</td>
                  <td class="num"><strong>{{ r.revenue_at_risk | number: '1.2-2' }}</strong></td>
                  <td>
                    <span class="chip" [class.clear]="r.temp_basis === 'product'">
                      {{ r.temp_basis === 'product' ? 'product temp' : 'air temp' }}
                    </span>
                  </td>
                  <td class="num">{{ r.avg_trust | number: '1.1-1' }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>

      <p class="subtle" style="margin-top: 12px">
        Revenue at risk = that day&rsquo;s sales of the unit&rsquo;s products &times; spoilage probability, which rises with time spent
        outside the safe band, warm or cold. An estimate, not a prediction.
      </p>
      <details class="about">
        <summary>How these numbers are built</summary>
        <div>
          <ul>
            <li>It is a documented heuristic that demonstrates the data integration, not a calibrated spoilage model.</li>
            <li>The sensor and sales records come from different sources, so the measurement window is anchored onto the retail
              calendar. That modelling decision is recorded in docs/data_linkage.md.</li>
            <li>Where a probe sits inside the goods, the estimate uses the product&rsquo;s own temperature rather than the air;
              the &ldquo;Based on&rdquo; column says which. Across the 123 refrigerators, 10 would raise a breach on air
              temperature the product never experienced, and 4 would miss a real one.</li>
            <li>Sales are converted to LKR using published Sri Lankan retail prices where available and indicative SME
              estimates otherwise.</li>
            <li>Beverages are monitored but carry no spoilage value: a warm door shelf costs a sale, not the stock.</li>
            <li>Data trust shows how reliable the sensor data was that day.</li>
          </ul>
        </div>
      </details>
    }
  `,
  styles: [`
    table.impact td.wrap { white-space: normal; min-width: 130px; max-width: 200px; }
    table.impact th, table.impact td { padding-left: 8px; padding-right: 8px; }
  `],
})
export class ImpactPage implements OnInit {
  private api = inject(ApiService);
  auth = inject(AuthService);
  data = signal<Impact | null>(null);
  error = signal<string | null>(null);

  chart = computed<ChartConfiguration | null>(() => {
    const d = this.data();
    if (!d) return null;
    const days = [...new Set(d.rows.map((r) => r.relative_day))].sort((a, b) => a - b);
    const units = [...new Set(d.rows.map((r) => r.unit_name))];
    return {
      type: 'bar',
      data: {
        labels: days.map((n) => `Day ${n}`),
        datasets: units.map((u, i) => ({
          label: u,
          backgroundColor: UNIT_COLORS[i % UNIT_COLORS.length],
          data: days.map((day) => d.rows.find((r) => r.unit_name === u && r.relative_day === day)?.revenue_at_risk ?? 0),
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'top', align: 'end' } },
        scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, title: { display: true, text: 'Revenue at risk' } } },
      },
    };
  });

  ngOnInit() {
    this.api.impact().subscribe({
      next: (d) => this.data.set(d),
      error: () => this.error.set('Cannot reach the API at localhost:8000. Start the backend (see README).'),
    });
  }

  exportCsv() {
    const d = this.data();
    if (!d) return;
    const rows: (string | number | null)[][] = [
      ['unit', 'relative_day', 'categories', 'minutes_too_warm', 'minutes_too_cold', 'low_c', 'peak_c', 'spoilage_probability', 'sales_exposed', 'revenue_at_risk', 'temp_basis', 'avg_trust'],
    ];
    for (const r of d.rows) {
      rows.push([r.unit_name, r.relative_day, r.categories, r.excursion_minutes, r.cold_minutes, r.low_temp_c, r.peak_temp_c, r.spoilage_probability, r.sales_exposed, r.revenue_at_risk, r.temp_basis, r.avg_trust]);
    }
    downloadCsv('coldsense-impact.csv', rows);
  }
}
