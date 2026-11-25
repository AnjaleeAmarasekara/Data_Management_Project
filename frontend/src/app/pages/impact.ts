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
        <h1>Business Impact</h1>
        <p class="subtle">What temperature problems could cost, based on the stock each unit holds.</p>
      </div>
      @if (auth.hasRole('quality_supervisor')) {
        <button (click)="exportCsv()" [disabled]="!data()">Export CSV</button>
      }
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    @if (data(); as d) {
      <div class="stats">
        <div class="stat"><span class="stat-label">Revenue at risk</span><span class="stat-value crit">LKR {{ d.total_revenue_at_risk | number: '1.0-0' }}</span></div>
        <div class="stat"><span class="stat-label">Sales exposed</span><span class="stat-value">LKR {{ d.total_sales_exposed | number: '1.0-0' }}</span></div>
        <div class="stat"><span class="stat-label">Share at risk</span><span class="stat-value">{{ (d.total_revenue_at_risk / d.total_sales_exposed) * 100 | number: '1.0-0' }}%</span></div>
        @if (byUnit()[0]; as top) {
          <div class="stat"><span class="stat-label">Highest risk</span><span class="stat-value small-v">{{ top.unit }}</span><span class="stat-sub">LKR {{ top.risk | number: '1.0-0' }}</span></div>
        }
      </div>

      <section class="card">
        <h2>Revenue at risk per day</h2>
        @if (chart(); as c) { <app-chart [config]="c" [height]="260" /> }
      </section>

      <section class="card" style="margin-top: 16px">
        <div class="row" style="margin-bottom: 12px">
          <h2 style="margin: 0">Breakdown</h2>
          <span class="spacer"></span>
          <div class="seg" role="group" aria-label="View">
            <button [class.on]="view() === 'unit'" (click)="view.set('unit')">By unit</button>
            <button [class.on]="view() === 'day'" (click)="view.set('day')">By day</button>
          </div>
        </div>
        <div class="table-wrap">
          @if (view() === 'unit') {
            <table>
              <thead><tr><th>Unit</th><th>Stock</th><th class="num">Time out of range</th><th class="num">Sales exposed</th><th class="num">At risk</th><th style="width: 18%"></th></tr></thead>
              <tbody>
                @for (u of byUnit(); track u.unit) {
                  <tr>
                    <td><strong>{{ u.unit }}</strong></td>
                    <td class="subtle wrap">{{ u.categories }}</td>
                    <td class="num">{{ hours(u.minutes) }}</td>
                    <td class="num">{{ u.sales | number: '1.0-0' }}</td>
                    <td class="num"><strong>{{ u.risk | number: '1.0-0' }}</strong></td>
                    <td><div class="bar"><div [style.width.%]="(u.risk / byUnit()[0].risk) * 100"></div></div></td>
                  </tr>
                }
              </tbody>
            </table>
          } @else {
            <table>
              <thead><tr><th>Day</th><th>Unit</th><th class="num">Too warm</th><th class="num">Too cold</th><th class="num">Range</th><th class="num">Spoilage</th><th class="num">At risk (LKR)</th></tr></thead>
              <tbody>
                @for (r of d.rows; track r.device_id + r.relative_day) {
                  <tr>
                    <td class="subtle">Day {{ r.relative_day }}</td>
                    <td>{{ r.unit_name }}</td>
                    <td class="num">{{ r.excursion_minutes ? hours(r.excursion_minutes) : '—' }}</td>
                    <td class="num">{{ r.cold_minutes ? hours(r.cold_minutes) : '—' }}</td>
                    <td class="num subtle">{{ r.low_temp_c | number: '1.1-1' }} to {{ r.peak_temp_c | number: '1.1-1' }}&deg;C</td>
                    <td class="num">{{ r.spoilage_probability * 100 | number: '1.0-0' }}%</td>
                    <td class="num"><strong>{{ r.revenue_at_risk | number: '1.0-0' }}</strong></td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </div>
        <details class="about">
          <summary>How this is calculated</summary>
          <div>
            <p>
              Revenue at risk = the day&rsquo;s sales of a unit&rsquo;s products &times; the chance they spoiled, which rises with time
              spent too warm or too cold. It is an estimate, not a prediction. Where a probe sits inside the goods, the product&rsquo;s
              own temperature is used instead of the air. Prices are Sri Lankan retail prices in LKR.
            </p>
          </div>
        </details>
      </section>
    }
  `,
  styles: [`
    td.wrap { white-space: normal; min-width: 140px; }
    .bar { height: 6px; border-radius: 3px; background: var(--border); overflow: hidden; }
    .bar > div { height: 100%; background: var(--crit); opacity: 0.8; }
    .small-v { font-size: 17px; padding-top: 4px; }
    details.about p { margin: 0; }
  `],
})
export class ImpactPage implements OnInit {
  private api = inject(ApiService);
  auth = inject(AuthService);
  data = signal<Impact | null>(null);
  error = signal<string | null>(null);
  view = signal<'unit' | 'day'>('unit');

  byUnit = computed(() => {
    const m = new Map<string, { unit: string; categories: string; minutes: number; sales: number; risk: number }>();
    for (const r of this.data()?.rows ?? []) {
      const u = m.get(r.unit_name) ?? { unit: r.unit_name, categories: r.categories, minutes: 0, sales: 0, risk: 0 };
      u.minutes += r.excursion_minutes + r.cold_minutes;
      u.sales += r.sales_exposed;
      u.risk += r.revenue_at_risk;
      m.set(r.unit_name, u);
    }
    return [...m.values()].sort((a, b) => b.risk - a.risk);
  });

  hours(m: number) {
    if (m < 60) return `${m} min`;
    return `${(m / 60).toFixed(m < 600 ? 1 : 0)} h`;
  }

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
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 12, boxHeight: 12 } } },
        scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, title: { display: true, text: 'LKR' }, grid: { color: '#eef2f3' } } },
      },
    };
  });

  ngOnInit() {
    this.api.impact().subscribe({
      next: (d) => this.data.set(d),
      error: () => this.error.set('Could not load business impact. Check the connection and refresh.'),
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
