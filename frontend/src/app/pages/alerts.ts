import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';

import { Alert, ApiService } from '../shared/api.service';
import { AuthService } from '../shared/auth.service';
import { downloadCsv, fmtTs } from '../shared/format';

const KIND_TEXT: Record<string, string> = {
  excursion: 'Too warm',
  cold_excursion: 'Too cold',
  sensor_silent: 'Sensor offline',
  isolation_forest: 'Unusual behaviour',
};
const PAGE = 15;

@Component({
  selector: 'app-alerts',
  imports: [DecimalPipe],
  template: `
    <div class="page-head">
      <div>
        <h1>Alerts</h1>
        <p class="subtle">Temperature breaches, offline sensors and early warnings.</p>
      </div>
      @if (auth.hasRole('quality_supervisor')) {
        <button (click)="exportCsv()" [disabled]="!filtered().length">Export CSV</button>
      }
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <div class="stats">
      <div class="stat"><span class="stat-label">Open</span><span class="stat-value">{{ open().length }}</span></div>
      <div class="stat"><span class="stat-label">Critical</span><span class="stat-value crit">{{ countOpen('critical') }}</span></div>
      <div class="stat"><span class="stat-label">Warning</span><span class="stat-value warn">{{ countOpen('warning') }}</span></div>
      <div class="stat"><span class="stat-label">Early warnings</span><span class="stat-value">{{ countOpen('watch') }}</span></div>
    </div>

    <section class="card">
      <div class="row" style="margin-bottom: 12px">
        <div class="seg" role="group" aria-label="Status">
          @for (v of views; track v.key) {
            <button [class.on]="view() === v.key" (click)="setView(v.key)">{{ v.label }}</button>
          }
        </div>
        <span class="spacer"></span>
        <select aria-label="Severity" [value]="severity()" (change)="severity.set($any($event.target).value); page.set(0)">
          <option value="">All severities</option>
          <option value="critical">Critical</option>
          <option value="warning">Warning</option>
          <option value="watch">Early warning</option>
        </select>
        <select aria-label="Unit" [value]="unit()" (change)="unit.set($any($event.target).value); page.set(0)">
          <option value="">All units</option>
          @for (u of unitNames(); track u) { <option [value]="u">{{ u }}</option> }
        </select>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Severity</th><th>Unit</th><th>Issue</th><th>Started</th>
              <th class="num">Duration</th><th class="num">Peak</th>
              @if (auth.hasRole('quality_supervisor')) { <th></th> }
            </tr>
          </thead>
          <tbody>
            @for (a of pageRows(); track a.id) {
              <tr>
                <td><span class="pill" [class]="a.severity">{{ a.severity === 'watch' ? 'Early warning' : a.severity }}</span></td>
                <td><strong class="unit">{{ a.unit_name }}</strong></td>
                <td>{{ kindText(a.kind) }}</td>
                <td class="subtle">{{ fmt(a.start_ts) }}</td>
                <td class="num">{{ duration(a.minutes) }}</td>
                <td class="num">{{ a.peak_temp_c | number: '1.1-1' }}&deg;C</td>
                @if (auth.hasRole('quality_supervisor')) {
                  <td class="num">
                    @if (a.acknowledged) {
                      <span class="subtle small" [title]="'Acknowledged by ' + a.acknowledged_by">&#10003; {{ a.acknowledged_by }}</span>
                    } @else {
                      <button (click)="acknowledge(a)" [disabled]="acking() === a.id">{{ acking() === a.id ? 'Saving…' : 'Acknowledge' }}</button>
                    }
                  </td>
                }
              </tr>
            } @empty {
              <tr><td colspan="7" class="empty">{{ view() === 'open' ? 'No open alerts. All clear.' : 'No alerts match.' }}</td></tr>
            }
          </tbody>
        </table>
      </div>

      @if (filtered().length > pageSize) {
        <div class="table-foot">
          <span>{{ page() * pageSize + 1 }}–{{ min((page() + 1) * pageSize, filtered().length) }} of {{ filtered().length }}</span>
          <span class="row">
            <button (click)="page.set(page() - 1)" [disabled]="page() === 0">Previous</button>
            <button (click)="page.set(page() + 1)" [disabled]="(page() + 1) * pageSize >= filtered().length">Next</button>
          </span>
        </div>
      }
    </section>
  `,
  styles: [`
    .unit { font-weight: 600; }
  `],
})
export class AlertsPage implements OnInit {
  private api = inject(ApiService);
  auth = inject(AuthService);
  alerts = signal<Alert[]>([]);
  error = signal<string | null>(null);
  severity = signal('');
  unit = signal('');
  acking = signal<number | null>(null);
  view = signal<'open' | 'done' | 'all'>('open');
  page = signal(0);
  readonly pageSize = PAGE;
  readonly views = [
    { key: 'open' as const, label: 'Open' },
    { key: 'done' as const, label: 'Acknowledged' },
    { key: 'all' as const, label: 'All' },
  ];

  unitNames = computed(() => [...new Set(this.alerts().map((a) => a.unit_name))].sort());
  open = computed(() => this.alerts().filter((a) => !a.acknowledged));
  filtered = computed(() =>
    this.alerts().filter(
      (a) =>
        (this.view() === 'all' || (this.view() === 'open') === !a.acknowledged) &&
        (!this.severity() || a.severity === this.severity()) &&
        (!this.unit() || a.unit_name === this.unit()),
    ),
  );
  pageRows = computed(() => this.filtered().slice(this.page() * PAGE, (this.page() + 1) * PAGE));

  setView(v: 'open' | 'done' | 'all') {
    this.view.set(v);
    this.page.set(0);
  }

  countOpen(severity: string) {
    return this.open().filter((a) => a.severity === severity).length;
  }

  min(a: number, b: number) {
    return Math.min(a, b);
  }

  duration(m: number) {
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  }

  ngOnInit() {
    this.load();
  }

  load() {
    this.api.alerts(500).subscribe({
      next: (a) => this.alerts.set(a),
      error: () => this.error.set('Could not load alerts. Check the connection and refresh.'),
    });
  }

  fmt(ts: string) {
    return fmtTs(ts);
  }

  kindText(k: string) {
    return KIND_TEXT[k] ?? k;
  }

  acknowledge(a: Alert) {
    this.acking.set(a.id);
    this.api.acknowledgeAlert(a.id, '').subscribe({
      next: () => {
        this.acking.set(null);
        this.load();
      },
      error: () => {
        this.acking.set(null);
        this.error.set('Could not acknowledge this alert.');
      },
    });
  }

  exportCsv() {
    const rows: (string | number | null)[][] = [['severity', 'unit', 'type', 'start_utc', 'end_utc', 'minutes', 'peak_c', 'avg_trust', 'acknowledged', 'message']];
    for (const a of this.filtered()) {
      rows.push([a.severity, a.unit_name, a.kind, a.start_ts, a.end_ts, a.minutes, a.peak_temp_c, a.avg_trust, a.acknowledged ? 'yes' : 'no', a.message]);
    }
    downloadCsv('coldsense-alerts.csv', rows);
  }
}
