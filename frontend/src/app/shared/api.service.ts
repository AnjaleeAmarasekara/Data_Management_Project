import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';

import { Role } from './auth.service';

const BASE = 'http://localhost:8000/api';

export type Label = 'High' | 'Medium' | 'Low';
export interface Flags { missing: boolean; outlier: boolean; drift: boolean; noise: boolean; integrity: boolean }
export interface Trust { value: number; label: Label; flags: Flags }
export interface Score { trust: number; label: Label; flags: Flags }
export interface Forecast {
  status: string;
  slope_c_per_min?: number;
  current_c?: number;
  predicted_c?: number;
  horizon_min?: number;
  minutes_to_limit?: number | null;
  fit_r2?: number;
}
export interface UnitState {
  device_id: string;
  unit_name: string;
  setpoint_c: number;
  max_safe_c: number;
  categories: string[];
  status: 'ok' | 'warning' | 'critical' | 'no_data';
  latest?: { ts: string; temp_c: number; humidity: number };
  trust?: Trust;
  forecast?: Forecast;
}
export interface Point { ts: string; temp_c: number; humidity?: number }
export interface FlagPoint { ts: string; temp_c: number; reason: string }
export interface Readings {
  device_id: string;
  unit_name?: string;
  max_safe_c?: number;
  setpoint_c?: number;
  points: Point[];
  flags: FlagPoint[];
}

export interface UnitQuality {
  device_id: string;
  unit_name: string;
  readings: number;
  windows: number;
  labels: Record<Label, number>;
  avg_trust: number;
  windows_flagged: Record<keyof Flags, number>;
  flagged_readings: Record<string, number>;
  excluded_from_analytics: number;
}
export interface TimelineItem { window_start: string; trust: number; label: Label; flags: Flags }

export interface Alert {
  id: number;
  device_id: string;
  unit_name: string;
  kind: 'excursion' | 'isolation_forest';
  severity: 'critical' | 'warning' | 'watch';
  start_ts: string;
  end_ts: string;
  minutes: number;
  peak_temp_c: number;
  avg_trust: number | null;
  message: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  acknowledgement_note: string | null;
}

export interface ImpactRow {
  device_id: string;
  unit_name: string;
  relative_day: number;
  categories: string;
  sales_exposed: number;
  excursion_minutes: number;
  degree_minutes: number;
  peak_temp_c: number;
  spoilage_probability: number;
  revenue_at_risk: number;
  avg_trust: number;
}
export interface Impact {
  total_revenue_at_risk: number;
  total_sales_exposed: number;
  by_unit: { unit_name: string; revenue_at_risk: number; sales_exposed: number; excursion_minutes: number }[];
  rows: ImpactRow[];
}

export type FaultName =
  | 'dropout'
  | 'spike'
  | 'drift'
  | 'noise'
  | 'duplicate_timestamp'
  | 'calibration_bias_subtle'
  | 'calibration_bias_extreme'
  | 'replay_attack'
  | 'data_injection';

export interface InjectResult {
  fault: FaultName;
  description: string;
  unit_name: string;
  max_safe_c: number;
  detected: boolean;
  note: string | null;
  before: { score: Score; points: Point[] };
  after: { score: Score; points: Point[]; flagged: Record<string, string[]> };
}

export interface ReportUnit { device_id: string; unit_name: string; windows: number; avg_trust: number | null; high: number; medium: number; low: number }
export interface ReportAlert { device_id: string; unit_name: string; kind: string; severity: string; start_ts: string; minutes: number; peak_temp_c: number; avg_trust: number | null }
export interface ReportImpact { device_id: string; unit_name: string; relative_day: number; categories: string; sales_exposed: number; excursion_minutes: number; spoilage_probability: number; revenue_at_risk: number; avg_trust: number }
export interface Report {
  range: { start: string; end: string };
  units: ReportUnit[];
  alerts: ReportAlert[];
  impact: ReportImpact[];
  total_revenue_at_risk: number;
}

export interface SecurityEvent { id: number; event_type: 'auth_failed' | 'unknown_device'; device_id: string | null; detail: string; occurred_at: string }
export interface SecurityEvents { total: number; last_24h_by_type: Record<string, number>; events: SecurityEvent[] }

export interface AppUser { id: number; email: string; full_name: string; role: Role; is_active: boolean }
export interface CreateUserRequest { email: string; full_name: string; role: Role; password: string }

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  units() {
    return this.http.get<UnitState[]>(`${BASE}/units`);
  }
  readings(deviceId: string, hours: number) {
    return this.http.get<Readings>(`${BASE}/units/${encodeURIComponent(deviceId)}/readings`, { params: { hours } });
  }
  qualitySummary() {
    return this.http.get<{ units: UnitQuality[] }>(`${BASE}/quality/summary`);
  }
  qualityTimeline(deviceId: string) {
    return this.http.get<TimelineItem[]>(`${BASE}/quality/timeline`, { params: { device_id: deviceId, limit: 500 } });
  }
  alerts(limit = 200) {
    return this.http.get<Alert[]>(`${BASE}/alerts`, { params: { limit } });
  }
  acknowledgeAlert(id: number, note: string) {
    return this.http.post<{ acknowledged: boolean }>(`${BASE}/alerts/${id}/acknowledge`, { note });
  }
  impact() {
    return this.http.get<Impact>(`${BASE}/impact`);
  }
  inject(deviceId: string, fault: FaultName) {
    return this.http.post<InjectResult>(`${BASE}/demo/inject`, { device_id: deviceId, fault });
  }

  reportSummary(start: string, end: string, deviceIds: string[]) {
    return this.http.get<Report>(`${BASE}/reports/summary`, { params: this.reportParams(start, end, deviceIds) });
  }
  reportPdf(start: string, end: string, deviceIds: string[]) {
    return this.http.get(`${BASE}/reports/pdf`, { params: this.reportParams(start, end, deviceIds), responseType: 'blob' });
  }
  private reportParams(start: string, end: string, deviceIds: string[]): HttpParams {
    let params = new HttpParams().set('start', start).set('end', end);
    for (const id of deviceIds) params = params.append('device_id', id);
    return params;
  }

  securityEvents(limit = 50) {
    return this.http.get<SecurityEvents>(`${BASE}/security/events`, { params: { limit } });
  }

  users() {
    return this.http.get<AppUser[]>(`${BASE}/admin/users`);
  }
  createUser(req: CreateUserRequest) {
    return this.http.post<AppUser>(`${BASE}/admin/users`, req);
  }
  updateUser(id: number, patch: { role?: Role; is_active?: boolean }) {
    return this.http.patch<AppUser>(`${BASE}/admin/users/${id}`, patch);
  }
}
