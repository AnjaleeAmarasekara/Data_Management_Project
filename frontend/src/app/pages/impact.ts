import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { ChartConfiguration } from 'chart.js';

import { ApiService, Impact } from '../shared/api.service';
import { AuthService } from '../shared/auth.service';
import { ChartComponent } from '../shared/chart';
import { downloadCsv } from '../shared/format';

const UNIT_COLORS = ['#0b63b6', '#c2571a', '#5b7f2a', '#7a4fa3'];

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
          <div class="kpi-label">Estimated revenue at risk</div>
          <div class="big" style="color: var(--crit)">{{ d.total_revenue_at_risk | number: '1.2-2' }}</div>
        </div>
        <div class="card">
          <div class="kpi-label">Sales exposed to cold-chain risk</div>
          <div class="big">{{ d.total_sales_exposed | number: '1.2-2' }}</div>
        </div>
        <div class="card">
          <div class="kpi-label">Share of exposed sales at risk</div>
          <div class="big">{{ (d.total_revenue_at_risk / d.total_sales_exposed) * 100 | number: '1.1-1' }}%</div>
        </div>
      </div>

      <section class="card" style="margin-top: 14px">
        <h2>Revenue at risk per day</h2>
        @if (chart(); as c) { <app-chart [config]="c" [height]="280" /> }
      </section>

      <section class="card" style="margin-top: 14px">
        <h2>By unit and day</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Unit</th><th class="num">Day</th><th>Stored categories</th>
                <th class="num">Minutes over limit</th><th class="num">Peak (&deg;C)</th>
                <th class="num">Spoilage prob.</th><th class="num">Sales exposed</th>
                <th class="num">Revenue at risk</th><th class="num">Data trust</th>
              </tr>
            </thead>
            <tbody>
              @for (r of d.rows; track r.device_id + r.relative_day) {
                <tr>
                  <td>{{ r.unit_name }}</td>
                  <td class="num">{{ r.relative_day }}</td>
                  <td>{{ r.categories }}</td>
                  <td class="num">{{ r.excursion_minutes }}</td>
                  <td class="num">{{ r.peak_temp_c | number: '1.1-1' }}</td>
                  <td class="num">{{ r.spoilage_probability * 100 | number: '1.0-0' }}%</td>
                  <td class="num">{{ r.sales_exposed | number: '1.2-2' }}</td>
                  <td class="num"><strong>{{ r.revenue_at_risk | number: '1.2-2' }}</strong></td>
                  <td class="num">{{ r.avg_trust | number: '1.1-1' }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>

      <section class="notice info" style="margin-top: 14px">
        <strong>How to read these numbers.</strong>
        Revenue at risk = sales of the unit's stored categories on that day &times; spoilage probability, where probability rises with
        the degree-minutes spent above the safe limit. It is a documented heuristic for demonstrating the data integration, not a calibrated
        spoilage model. Sensor and sales datasets do not share real dates, so days are matched by relative day index (day 1 to day 8 of each).
        The data trust column shows how reliable the underlying sensor data was that day.
      </section>
    }
  `,
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
      ['unit', 'relative_day', 'categories', 'excursion_minutes', 'peak_c', 'spoilage_probability', 'sales_exposed', 'revenue_at_risk', 'avg_trust'],
    ];
    for (const r of d.rows) {
      rows.push([r.unit_name, r.relative_day, r.categories, r.excursion_minutes, r.peak_temp_c, r.spoilage_probability, r.sales_exposed, r.revenue_at_risk, r.avg_trust]);
    }
    downloadCsv('coldsense-impact.csv', rows);
  }
}
