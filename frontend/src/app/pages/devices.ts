import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService, RegisteredUnit, RegisterUnitRequest } from '../shared/api.service';
import { fmtTs } from '../shared/format';

const ZONES = [
  { code: 'MS', label: 'Chiller (shelf)', setpoint: 4, max: 5, min: 0 as number | null },
  { code: 'TS', label: 'Chiller (top shelf)', setpoint: 4, max: 5, min: 0 as number | null },
  { code: 'DS', label: 'Door shelf', setpoint: 7, max: 10, min: 0 as number | null },
  { code: 'VD', label: 'Vegetable drawer', setpoint: 6, max: 8, min: 0 as number | null },
  { code: 'FC', label: 'Freezer', setpoint: -18, max: -15, min: null as number | null },
];

function blankForm() {
  return {
    site_id: '',
    unit: '',
    zone: 'MS',
    unit_name: '',
    site_name: '',
    setpoint_c: 4,
    max_safe_c: 5,
    min_safe_c: 0 as number | null,
    categories: '',
    warmup_min: 30,
  };
}

@Component({
  selector: 'app-devices',
  imports: [FormsModule],
  template: `
    <div class="page-head">
      <div>
        <h1>Devices</h1>
        <p class="subtle">
          Register a live probe so it may push readings. An unregistered device is refused at the ingestion API and logged as a
          security event. Administrator only.
        </p>
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }
    @if (notice(); as n) { <div class="notice" role="status">{{ n }}</div> }

    <section class="card">
      <h2>Registered live units</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Device ID</th><th>Unit</th><th>Site</th><th>Safe band</th><th>Warm-up</th><th>Readings</th><th>Last reading (UTC)</th><th>Monitoring</th></tr>
          </thead>
          <tbody>
            @for (u of units(); track u.device_id) {
              <tr>
                <td><code>{{ u.device_id }}</code></td>
                <td>{{ u.unit_name }}<div class="subtle small">{{ u.categories.join(', ') || 'No categories' }}</div></td>
                <td>{{ u.site_name }}</td>
                <td>
                  @if (editing() === u.device_id) {
                    <span class="row tight">
                      <input type="number" step="0.5" [(ngModel)]="edit.min_safe_c" name="emin" aria-label="Lower limit" placeholder="none" />
                      to
                      <input type="number" step="0.5" [(ngModel)]="edit.max_safe_c" name="emax" aria-label="Upper limit" />
                      &deg;C, setpoint
                      <input type="number" step="0.5" [(ngModel)]="edit.setpoint_c" name="eset" aria-label="Setpoint" />
                    </span>
                    <span class="row tight" style="margin-top: 6px">
                      <button class="primary" (click)="saveLimits(u)">Save</button>
                      <button (click)="editing.set(null)">Cancel</button>
                    </span>
                  } @else {
                    @if (u.min_safe_c !== null) { {{ u.min_safe_c }} to } @else { up to }
                    {{ u.max_safe_c }}&deg;C <span class="subtle">(set {{ u.setpoint_c }})</span>
                    <button class="link" (click)="startEdit(u)">Edit</button>
                  }
                </td>
                <td>{{ u.warmup_min }} min</td>
                <td>{{ u.readings }}</td>
                <td>
                  @if (u.last_reading) { {{ fmt(u.last_reading) }} } @else { <span class="pill no_data">Awaiting first reading</span> }
                </td>
                <td>
                  <button (click)="toggleMonitored(u)">{{ u.monitored ? 'Pause' : 'Resume' }}</button>
                </td>
              </tr>
            } @empty {
              <tr><td colspan="8" class="subtle">No live units registered yet. Use the form below for the first probe.</td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>

    <section class="card" style="margin-top: 14px">
      <h2>Register a probe</h2>
      <p class="subtle">
        The device ID is built as SITE-UNIT-ZONE. The zone sets which data-quality thresholds apply, so pick the compartment the probe
        actually sits in. The probe must send exactly this ID with its readings.
      </p>
      <form class="register" (ngSubmit)="register()">
        <label>Site code <input [(ngModel)]="form.site_id" name="site_id" placeholder="LIVE" required maxlength="12" /></label>
        <label>Unit number <input [(ngModel)]="form.unit" name="unit" placeholder="001" required maxlength="12" /></label>
        <label>Compartment
          <select [(ngModel)]="form.zone" name="zone" (ngModelChange)="zoneChanged($event)">
            @for (z of zones; track z.code) { <option [value]="z.code">{{ z.label }} ({{ z.code }})</option> }
          </select>
        </label>
        <label>Unit name <input [(ngModel)]="form.unit_name" name="unit_name" placeholder="Shop fridge" required /></label>
        <label>Premises name <input [(ngModel)]="form.site_name" name="site_name" placeholder="Corner shop, Kandy" required /></label>
        <label>Setpoint &deg;C <input type="number" step="0.5" [(ngModel)]="form.setpoint_c" name="setpoint_c" /></label>
        <label>Upper limit &deg;C <input type="number" step="0.5" [(ngModel)]="form.max_safe_c" name="max_safe_c" /></label>
        <label>Lower limit &deg;C <input type="number" step="0.5" [(ngModel)]="form.min_safe_c" name="min_safe_c" placeholder="none" /></label>
        <label>Warm-up (min) <input type="number" min="0" max="240" [(ngModel)]="form.warmup_min" name="warmup_min" /></label>
        <label class="wide">Stock categories (comma-separated) <input [(ngModel)]="form.categories" name="categories" placeholder="Dairy, Deli" /></label>
        <div class="wide row">
          <span class="subtle">Device ID: <code>{{ deviceId() || '—' }}</code></span>
          <span class="spacer"></span>
          <button type="submit" class="primary" [disabled]="saving()">{{ saving() ? 'Registering...' : 'Register probe' }}</button>
        </div>
      </form>
    </section>
  `,
  styles: [
    `
      input, select { font: inherit; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }
      .register { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 12px; margin-top: 10px; }
      .register label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
      .register .wide { grid-column: 1 / -1; }
      .tight { gap: 6px; flex-wrap: wrap; }
      .tight input { width: 70px; }
      .small { font-size: 12px; }
      button.link { background: none; border: none; color: var(--watch); padding: 0 0 0 6px; cursor: pointer; text-decoration: underline; }
    `,
  ],
})
export class DevicesPage implements OnInit {
  private api = inject(ApiService);
  readonly zones = ZONES;

  units = signal<RegisteredUnit[]>([]);
  error = signal<string | null>(null);
  notice = signal<string | null>(null);
  saving = signal(false);
  editing = signal<string | null>(null);

  form = blankForm();
  edit = { setpoint_c: 0, max_safe_c: 0, min_safe_c: null as number | null };

  ngOnInit() {
    this.load();
  }

  fmt(ts: string) {
    return fmtTs(ts);
  }

  load() {
    this.api.registeredUnits().subscribe({
      next: (u) => this.units.set(u),
      error: () => this.error.set('Could not load registered units.'),
    });
  }

  deviceId(): string {
    const site = this.form.site_id.trim().toUpperCase();
    const unit = this.form.unit.trim().toUpperCase();
    return site && unit ? `${site}-${unit}-${this.form.zone}` : '';
  }

  zoneChanged(code: string) {
    const z = ZONES.find((x) => x.code === code);
    if (!z) return;
    this.form.setpoint_c = z.setpoint;
    this.form.max_safe_c = z.max;
    this.form.min_safe_c = z.min;
  }

  register() {
    const device_id = this.deviceId();
    if (!device_id || !this.form.unit_name || !this.form.site_name) {
      this.error.set('Site code, unit number, unit name and premises name are all required.');
      return;
    }
    const req: RegisterUnitRequest = {
      device_id,
      unit_name: this.form.unit_name,
      site_id: `${this.form.site_id.trim().toUpperCase()}-${this.form.unit.trim().toUpperCase()}`,
      site_name: this.form.site_name,
      setpoint_c: Number(this.form.setpoint_c),
      max_safe_c: Number(this.form.max_safe_c),
      min_safe_c: this.form.min_safe_c === null || (this.form.min_safe_c as unknown) === '' ? null : Number(this.form.min_safe_c),
      categories: this.form.categories.split(',').map((c) => c.trim()).filter(Boolean),
      warmup_min: Number(this.form.warmup_min),
    };
    this.saving.set(true);
    this.error.set(null);
    this.api.registerUnit(req).subscribe({
      next: (u) => {
        this.saving.set(false);
        this.notice.set(`${u.device_id} registered. It appears on Real-Time Monitoring and goes live with its first reading.`);
        this.form = blankForm();
        this.load();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(this.message(err, 'Could not register the probe.'));
      },
    });
  }

  startEdit(u: RegisteredUnit) {
    this.edit = { setpoint_c: u.setpoint_c, max_safe_c: u.max_safe_c, min_safe_c: u.min_safe_c };
    this.editing.set(u.device_id);
  }

  saveLimits(u: RegisteredUnit) {
    const min = this.edit.min_safe_c;
    const clear = min === null || (min as unknown) === '';
    this.api
      .updateUnit(u.device_id, {
        setpoint_c: Number(this.edit.setpoint_c),
        max_safe_c: Number(this.edit.max_safe_c),
        ...(clear ? { clear_min_safe: true } : { min_safe_c: Number(min) }),
      })
      .subscribe({
        next: () => {
          this.editing.set(null);
          this.notice.set(`Limits for ${u.device_id} updated. The change is recorded in the audit log.`);
          this.load();
        },
        error: (err) => this.error.set(this.message(err, 'Could not update the limits.')),
      });
  }

  toggleMonitored(u: RegisteredUnit) {
    this.api.updateUnit(u.device_id, { monitored: !u.monitored }).subscribe({
      next: () => this.load(),
      error: (err) => this.error.set(this.message(err, 'Could not change monitoring.')),
    });
  }

  /** FastAPI returns a string detail for HTTP errors and a list for validation errors. */
  private message(err: any, fallback: string): string {
    const d = err?.error?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && d.length) return d.map((e: any) => String(e.msg).replace(/^Value error, /, '')).join('; ');
    return fallback;
  }
}
