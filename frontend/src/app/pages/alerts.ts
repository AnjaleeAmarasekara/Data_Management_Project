import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';

import { Alert, ApiService } from '../shared/api.service';
import { AuthService } from '../shared/auth.service';
import { downloadCsv, fmtTs } from '../shared/format';

const KIND_TEXT: Record<string, string> = { excursion: 'Safe-limit excursion', isolation_forest: 'Anomaly (early warning)' };

@Component({
  selector: 'app-alerts',
  imports: [DecimalPipe],
  template: `
    <div class="page-head">
      <div>
        <h1>Alerts</h1>
        <p class="subtle">
          Excursions are confirmed breaches of a unit's safe limit. Early warnings come from an Isolation Forest model that
          only ever sees quality-filtered data.
        </p>
      </div>
      @if (auth.hasRole('quality_supervisor')) {
        <button (click)="exportCsv()" [disabled]="!filtered().length">Export CSV</button>
      }
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <section class="card">
      <div class="row" style="margin-bottom: 12px">
        <label class="row">Severity
          <select [value]="severity()" (change)="severity.set($any($event.target).value)">
            <option value="">All</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="watch">Watch</option>
          </select>
        </label>
        <label class="row">Unit
          <select [value]="unit()" (change)="unit.set($any($event.target).value)">
            <option value="">All</option>
            @for (u of unitNames(); track u) { <option [value]="u">{{ u }}</option> }
          </select>
        </label>
        <span class="spacer"></span>
        <span class="subtle">{{ filtered().length }} of {{ alerts().length }} alerts</span>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Severity</th><th>Unit</th><th>Type</th><th>Started (UTC)</th>
              <th class="num">Duration (min)</th><th class="num">Peak (&deg;C)</th><th class="num">Data trust</th>
              @if (auth.hasRole('quality_supervisor')) { <th>Acknowledgement</th> }
            </tr>
          </thead>
          <tbody>
            @for (a of filtered(); track a.id) {
              <tr>
                <td><span class="pill" [class]="a.severity">{{ a.severity }}</span></td>
                <td>{{ a.unit_name }}</td>
                <td>{{ kindText(a.kind) }}</td>
                <td>{{ fmt(a.start_ts) }}</td>
                <td class="num">{{ a.minutes }}</td>
                <td class="num">{{ a.peak_temp_c | number: '1.1-1' }}</td>
                <td class="num">{{ a.avg_trust === null ? '-' : (a.avg_trust | number: '1.1-1') }}</td>
                @if (auth.hasRole('quality_supervisor')) {
                  <td>
                    @if (a.acknowledged) {
                      <span class="pill ok">Acknowledged</span>
                      <span class="subtle"> by {{ a.acknowledged_by }}</span>
                    } @else {
                      <button (click)="acknowledge(a)" [disabled]="acking() === a.id">{{ acking() === a.id ? 'Saving...' : 'Acknowledge' }}</button>
                    }
                  </td>
                }
              </tr>
            } @empty {
              <tr><td colspan="8" class="subtle">No alerts match.</td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>
  `,
})
export class AlertsPage implements OnInit {
  private api = inject(ApiService);
  auth = inject(AuthService);
  alerts = signal<Alert[]>([]);
  error = signal<string | null>(null);
  severity = signal('');
  unit = signal('');
  acking = signal<number | null>(null);

  unitNames = computed(() => [...new Set(this.alerts().map((a) => a.unit_name))].sort());
  filtered = computed(() =>
    this.alerts().filter((a) => (!this.severity() || a.severity === this.severity()) && (!this.unit() || a.unit_name === this.unit())),
  );

  ngOnInit() {
    this.load();
  }

  load() {
    this.api.alerts(500).subscribe({
      next: (a) => this.alerts.set(a),
      error: () => this.error.set('Cannot reach the API at localhost:8000. Start the backend (see README).'),
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
