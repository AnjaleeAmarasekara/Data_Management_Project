import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ApiService, Report, UnitState } from '../shared/api.service';
import { fmtTs } from '../shared/format';

// The refrigerator record runs 2017-08-01 to 2017-08-06 (see docs/data_linkage.md); default the
// range to it so the first preview shows real data instead of an empty "no data" report.
const DEFAULT_START = '2017-08-01T00:00';
const DEFAULT_END = '2017-08-07T00:00';

@Component({
  selector: 'app-reports',
  imports: [DecimalPipe, FormsModule],
  template: `
    <div class="page-head">
      <div>
        <h1>Reports</h1>
        <p class="subtle">A summary of data quality, alerts and costs for any period, ready to download as a PDF.</p>
      </div>
    </div>

    <section class="card controls">
      <label>From <input type="datetime-local" [(ngModel)]="start" /></label>
      <label>To <input type="datetime-local" [(ngModel)]="end" /></label>
      <div class="units">
        <span class="lbl">Units</span>
        <div class="row" style="gap: 6px">
          @for (u of units(); track u.device_id) {
            <button class="toggle" [class.on]="selected().has(u.device_id)" (click)="toggle(u.device_id)">{{ u.unit_name }}</button>
          }
        </div>
      </div>
      <div class="row actions">
        <button (click)="preview()" [disabled]="loading() || !selected().size">{{ loading() ? 'Updating…' : 'Update preview' }}</button>
        <button class="primary" (click)="downloadPdf()" [disabled]="downloading() || !selected().size">{{ downloading() ? 'Preparing…' : 'Download PDF' }}</button>
      </div>
    </section>
    @if (error(); as e) { <div class="notice error" style="margin-top: 12px" role="alert">{{ e }}</div> }

    @if (report(); as r) {
      <div class="stats" style="margin-top: 16px">
        <div class="stat"><span class="stat-label">Average data trust</span><span class="stat-value ok">{{ avgTrust(r) | number: '1.1-1' }}</span></div>
        <div class="stat"><span class="stat-label">Alerts</span><span class="stat-value">{{ r.alerts.length }}</span><span class="stat-sub">{{ countSev(r, 'critical') }} critical</span></div>
        <div class="stat"><span class="stat-label">Time out of range</span><span class="stat-value">{{ hours(totalMinutes(r)) }}</span></div>
        <div class="stat"><span class="stat-label">Revenue at risk</span><span class="stat-value crit">LKR {{ r.total_revenue_at_risk | number: '1.0-0' }}</span></div>
      </div>

      <section class="card">
        <h2>By unit</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Unit</th><th class="num">Data trust</th><th class="num">Critical</th><th class="num">Warning</th><th class="num">Early warnings</th><th class="num">Time out of range</th><th class="num">Revenue at risk (LKR)</th></tr></thead>
            <tbody>
              @for (u of rows(); track u.device_id) {
                <tr>
                  <td><strong>{{ u.unit_name }}</strong></td>
                  <td class="num">{{ u.trust === null ? '—' : (u.trust | number: '1.1-1') }}</td>
                  <td class="num">{{ u.critical || '—' }}</td>
                  <td class="num">{{ u.warning || '—' }}</td>
                  <td class="num">{{ u.watch || '—' }}</td>
                  <td class="num">{{ u.minutes ? hours(u.minutes) : '—' }}</td>
                  <td class="num"><strong>{{ u.risk | number: '1.0-0' }}</strong></td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        <p class="subtle small" style="margin: 12px 0 0">The PDF also lists every alert and the day-by-day cost breakdown.</p>
      </section>
    }
  `,
  styles: [`
    .controls { display: flex; flex-wrap: wrap; gap: 16px 20px; align-items: flex-end; }
    .controls label, .units { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--muted); font-weight: 500; }
    .lbl { font-size: 12.5px; }
    .actions { margin-left: auto; }
    button.toggle { min-height: 30px; padding: 3px 11px; border-radius: 999px; font-size: 13px; color: var(--muted); }
    button.toggle.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); font-weight: 600; }
  `],
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

  /** One line per unit: what the PDF says in full, condensed. */
  rows = computed(() => {
    const r = this.report();
    if (!r) return [];
    return r.units.map((u) => {
      const alerts = r.alerts.filter((a) => a.device_id === u.device_id);
      const impact = r.impact.filter((i) => i.device_id === u.device_id);
      return {
        device_id: u.device_id,
        unit_name: u.unit_name,
        trust: u.avg_trust,
        critical: alerts.filter((a) => a.severity === 'critical').length,
        warning: alerts.filter((a) => a.severity === 'warning').length,
        watch: alerts.filter((a) => a.severity === 'watch').length,
        minutes: alerts.filter((a) => a.kind !== 'isolation_forest').reduce((s, a) => s + a.minutes, 0),
        risk: impact.reduce((s, i) => s + i.revenue_at_risk, 0),
      };
    });
  });

  avgTrust(r: Report) {
    const u = r.units.filter((x) => x.avg_trust !== null);
    const w = u.reduce((s, x) => s + x.windows, 0);
    return w ? u.reduce((s, x) => s + (x.avg_trust ?? 0) * x.windows, 0) / w : null;
  }

  countSev(r: Report, sev: string) {
    return r.alerts.filter((a) => a.severity === sev).length;
  }

  totalMinutes(r: Report) {
    return r.alerts.filter((a) => a.kind !== 'isolation_forest').reduce((s, a) => s + a.minutes, 0);
  }

  hours(m: number) {
    return m < 60 ? `${m} min` : `${(m / 60).toFixed(m < 600 ? 1 : 0)} h`;
  }

  ngOnInit() {
    this.api.units().subscribe({
      next: (u) => {
        this.units.set(u);
        this.selected.set(new Set(u.map((x) => x.device_id)));
        this.preview();
      },
      error: () => this.error.set('Could not load units. Check the connection and refresh.'),
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
