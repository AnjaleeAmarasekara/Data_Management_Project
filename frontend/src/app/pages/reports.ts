import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ApiService, Report, UnitState } from '../shared/api.service';
import { fmtTs } from '../shared/format';

// The demo dataset runs 2020-07-12 to 2020-07-20 (see docs/data_linkage.md); default the
// range to it so the first preview shows real data instead of an empty "no data" report.
const DEFAULT_START = '2020-07-12T00:00';
const DEFAULT_END = '2020-07-20T00:00';

@Component({
  selector: 'app-reports',
  imports: [DecimalPipe, FormsModule],
  template: `
    <div class="page-head">
      <div>
        <h1>Reports</h1>
        <p class="subtle">
          A combined data-quality, alerts and business-impact summary for a date range, previewed here and available as
          a PDF. Restricted to Quality Supervisor and above.
        </p>
      </div>
    </div>

    <section class="card">
      <div class="row" style="margin-bottom: 4px">
        <label class="row">From (UTC) <input type="datetime-local" [(ngModel)]="start" /></label>
        <label class="row">To (UTC) <input type="datetime-local" [(ngModel)]="end" /></label>
      </div>
      <div class="row" style="margin: 10px 0">
        <span class="subtle">Units</span>
        @for (u of units(); track u.device_id) {
          <label class="row" style="gap: 4px">
            <input type="checkbox" [checked]="selected().has(u.device_id)" (change)="toggle(u.device_id)" />
            {{ u.unit_name }}
          </label>
        }
      </div>
      <div class="row">
        <button class="primary" (click)="preview()" [disabled]="loading() || !selected().size">{{ loading() ? 'Loading...' : 'Preview report' }}</button>
        <button (click)="downloadPdf()" [disabled]="downloading() || !selected().size">{{ downloading() ? 'Preparing PDF...' : 'Download PDF' }}</button>
      </div>
      @if (error(); as e) { <div class="notice error" style="margin-top: 10px" role="alert">{{ e }}</div> }
    </section>

    @if (report(); as r) {
      <section class="card" style="margin-top: 14px">
        <h2>Data quality summary</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Unit</th><th class="num">Windows</th><th class="num">Avg trust</th><th class="num">High</th><th class="num">Medium</th><th class="num">Low</th></tr></thead>
            <tbody>
              @for (u of r.units; track u.device_id) {
                <tr>
                  <td>{{ u.unit_name }}</td>
                  <td class="num">{{ u.windows }}</td>
                  <td class="num">{{ u.avg_trust === null ? '-' : (u.avg_trust | number: '1.1-1') }}</td>
                  <td class="num">{{ u.high }}</td><td class="num">{{ u.medium }}</td><td class="num">{{ u.low }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>

      <section class="card" style="margin-top: 14px">
        <h2>Alerts in range ({{ r.alerts.length }})</h2>
        @if (r.alerts.length) {
          <div class="table-wrap">
            <table>
              <thead><tr><th>Unit</th><th>Type</th><th>Severity</th><th>Started (UTC)</th><th class="num">Minutes</th><th class="num">Peak &deg;C</th></tr></thead>
              <tbody>
                @for (a of r.alerts; track a.start_ts + a.device_id) {
                  <tr>
                    <td>{{ a.unit_name }}</td><td>{{ a.kind }}</td>
                    <td><span class="pill" [class]="a.severity">{{ a.severity }}</span></td>
                    <td>{{ fmt(a.start_ts) }}</td><td class="num">{{ a.minutes }}</td><td class="num">{{ a.peak_temp_c | number: '1.1-1' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <p class="subtle">No alerts in this range.</p>
        }
      </section>

      <section class="card" style="margin-top: 14px">
        <h2>Business impact in range</h2>
        @if (r.impact.length) {
          <div class="table-wrap">
            <table>
              <thead><tr><th>Unit</th><th class="num">Day</th><th>Categories</th><th class="num">Spoilage prob.</th><th class="num">Sales exposed</th><th class="num">Revenue at risk</th></tr></thead>
              <tbody>
                @for (i of r.impact; track i.device_id + i.relative_day) {
                  <tr>
                    <td>{{ i.unit_name }}</td><td class="num">{{ i.relative_day }}</td><td>{{ i.categories }}</td>
                    <td class="num">{{ i.spoilage_probability * 100 | number: '1.0-0' }}%</td>
                    <td class="num">{{ i.sales_exposed | number: '1.2-2' }}</td>
                    <td class="num"><strong>{{ i.revenue_at_risk | number: '1.2-2' }}</strong></td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p style="margin-top: 10px"><strong>Total revenue at risk: {{ r.total_revenue_at_risk | number: '1.2-2' }}</strong></p>
        } @else {
          <p class="subtle">No impact events in this range.</p>
        }
      </section>
    }
  `,
  styles: [`input[type="datetime-local"] { font: inherit; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; }`],
})
export class ReportsPage implements OnInit {
  private api = inject(ApiService);

  units = signal<UnitState[]>([]);
  selected = signal<Set<string>>(new Set());
  start = DEFAULT_START;
  end = DEFAULT_END;
  report = signal<Report | null>(null);
  loading = signal(false);
  downloading = signal(false);
  error = signal<string | null>(null);

  ngOnInit() {
    this.api.units().subscribe({
      next: (u) => {
        this.units.set(u);
        this.selected.set(new Set(u.map((x) => x.device_id)));
        this.preview();
      },
      error: () => this.error.set('Cannot reach the API at localhost:8000. Start the backend (see README).'),
    });
  }

  toggle(id: string) {
    const s = new Set(this.selected());
    s.has(id) ? s.delete(id) : s.add(id);
    this.selected.set(s);
  }

  private range(): [string, string] {
    return [new Date(this.start).toISOString(), new Date(this.end).toISOString()];
  }

  preview() {
    const [start, end] = this.range();
    this.loading.set(true);
    this.error.set(null);
    this.api.reportSummary(start, end, [...this.selected()]).subscribe({
      next: (r) => {
        this.report.set(r);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err?.status === 403 ? 'Your role does not have access to reports.' : 'Could not load the report.');
      },
    });
  }

  downloadPdf() {
    const [start, end] = this.range();
    this.downloading.set(true);
    this.api.reportPdf(start, end, [...this.selected()]).subscribe({
      next: (blob) => {
        this.downloading.set(false);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'coldsense-report.pdf';
        a.click();
        URL.revokeObjectURL(url);
      },
      error: () => {
        this.downloading.set(false);
        this.error.set('Could not generate the PDF.');
      },
    });
  }

  fmt(ts: string) {
    return fmtTs(ts);
  }
}
