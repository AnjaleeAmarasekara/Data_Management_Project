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
  // which end of the safe band the forecast says is threatened
  direction?: 'warm' | 'cold' | null;
}
export interface UnitState {
  device_id: string;
  unit_name: string;
  setpoint_c: number;
  max_safe_c: number;
  min_safe_c: number | null;
  categories: string[];
  status: 'ok' | 'warning' | 'critical' | 'no_data' | 'warming_up';
  // 'data': a replayed historical record; 'wall': a live probe judged against the real clock
  clock?: 'data' | 'wall';
  // which end of the safe band was breached, if either
  breach?: 'too_warm' | 'too_cold' | 'silent' | 'premises_silent' | null;
  connectivity?: {
    last_seen: string | null;
    silent_for_min: number | null;
    channel_silent: boolean;
    premises_silent: boolean;
    premises_silent_for_min: number | null;
    clock?: 'data' | 'wall';
  };
  // present while a newly fitted probe is still settling; limits are not enforced until it ends
  warmup?: { ends_at: string; remaining_min: number; warmup_min: number } | null;
  latest?: { ts: string; temp_c: number; humidity: number | null };
  trust?: Trust;
  forecast?: Forecast;
}
export interface Point { ts: string; temp_c: number; humidity?: number | null }
export interface FlagPoint { ts: string; temp_c: number; reason: string }
export interface Readings {
  device_id: string;
  unit_name?: string;
  max_safe_c?: number;
  min_safe_c?: number | null;
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
  kind: 'excursion' | 'cold_excursion' | 'isolation_forest' | 'sensor_silent';
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
  // 'product' when a probe inside the goods supplied the temperature, 'air' otherwise
  temp_basis: 'product' | 'air';
  cold_minutes: number;
  cold_degree_minutes: number;
  low_temp_c: number | null;
}

export interface NetworkSite {
  site_id: string;
  site_name: string;
  units: number;
  windows: number;
  avg_trust: number | null;
  low_windows: number;
  integrity_windows: number;
  silent_channels: number;
  premises_silent: boolean;
  last_seen: string | null;
}

export interface NetworkSummary {
  totals: { sites: number; cold_units: number; channels: number; readings: number; windows: number; silent_channels: number; silent_premises: number };
  silence_threshold_min: number;
  sites: NetworkSite[];
}

export interface QualityEvaluation {
  generated_at: string;
  method: string;
  sample: {
    positive_windows_per_fault: number;
    negative_windows: number;
    zones: string[];
    cold_unit_windows_scored: number;
    cold_unit_windows_with_any_flag: number;
    cold_unit_flag_rate: number;
  };
  overall: { tp: number; fp: number; fn: number; tn: number; precision: number; recall: number; f1: number; accuracy: number };
  label_level: { precision: number; recall: number; f1: number; accuracy: number };
  false_alarm_rate: { flagged: number; of: number; rate: number; ci95: [number, number] };
  false_alarm_rate_label_level: { flagged: number; of: number; rate: number; ci95: [number, number] };
  macro_average: { precision: number; recall: number; f1: number };
  per_check: { check: string; catches: string[]; windows: number; precision: number; recall: number; f1: number; accuracy: number }[];
  per_fault: { fault: string; expected_check: string | null; windows: number; recall: number; precision: number; f1: number }[];
}

export interface Impact {
  total_revenue_at_risk: number;
  total_sales_exposed: number;
  by_unit: { unit_name: string; revenue_at_risk: number; sales_exposed: number; excursion_minutes: number }[];
  rows: ImpactRow[];
}

export interface TrendPoint {
  bucket_start: string;
  temp_min: number;
  temp_mean: number;
  temp_max: number;
  humidity_mean: number | null;
  readings: number;
  excluded: number;
  completeness: number;
  minutes_too_warm: number;
  minutes_too_cold: number;
  avg_trust: number | null;
  product_mean: number | null;
  ambient_mean: number | null;
}

export type TrendBucket = 'hour' | '6h' | 'day';

export interface Trends {
  device_id: string;
  unit_name: string;
  bucket: TrendBucket;
  max_safe_c: number;
  min_safe_c: number | null;
  has_product_probe: boolean;
  range: { start: string; end: string };
  points: TrendPoint[];
  totals: { readings: number; excluded: number; minutes_too_warm: number; minutes_too_cold: number; completeness: number | null };
}

export interface TrendsOverview {
  bucket: TrendBucket;
  units: { device_id: string; unit_name: string; points: { bucket_start: string; minutes_too_warm: number; minutes_too_cold: number; temp_mean: number }[] }[];
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
  | 'data_injection'
  | 'sensor_silent';

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

export interface AuditEntry { id: number; actor: string | null; action: string; target: string | null; detail: string | null; occurred_at: string }
export interface SecurityEvent { id: number; event_type: 'auth_failed' | 'unknown_device'; device_id: string | null; detail: string; occurred_at: string }
export interface SecurityEvents { total: number; last_24h_by_type: Record<string, number>; events: SecurityEvent[] }

export interface AppUser { id: number; email: string; full_name: string; role: Role; is_active: boolean }
export interface CreateUserRequest { email: string; full_name: string; role: Role; password: string }
export interface RegisteredUnit {
  device_id: string;
  unit_name: string;
  unit_type: string;
  site_id: string;
  site_name: string;
  zone: string;
  setpoint_c: number;
  max_safe_c: number;
  min_safe_c: number | null;
  categories: string[];
  clock: 'data' | 'wall';
  warmup_min: number;
  monitored: boolean;
  registered_at: string;
  readings: number;
  first_reading: string | null;
  last_reading: string | null;
}
export interface RegisterUnitRequest {
  device_id: string;
  unit_name: string;
  site_id: string;
  site_name: string;
  setpoint_c: number;
  max_safe_c: number;
  min_safe_c: number | null;
  categories: string[];
  warmup_min: number;
}
export interface UpdateUnitRequest {
  unit_name?: string;
  setpoint_c?: number;
  max_safe_c?: number;
  min_safe_c?: number;
  clear_min_safe?: boolean;
  categories?: string[];
  warmup_min?: number;
  monitored?: boolean;
}

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

  qualityNetwork() {
    return this.http.get<NetworkSummary>(`${BASE}/quality/network`);
  }

  qualityEvaluation() {
    return this.http.get<QualityEvaluation>(`${BASE}/quality/evaluation`);
  }

  trends(deviceId: string, bucket: TrendBucket) {
    return this.http.get<Trends>(`${BASE}/trends`, { params: { device_id: deviceId, bucket } });
  }

  trendsOverview(bucket: TrendBucket) {
    return this.http.get<TrendsOverview>(`${BASE}/trends/overview`, { params: { bucket } });
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

  auditLog(limit = 50) {
    return this.http.get<AuditEntry[]>(`${BASE}/admin/audit`, { params: { limit } });
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

  registeredUnits() {
    return this.http.get<RegisteredUnit[]>(`${BASE}/admin/units`);
  }
  registerUnit(req: RegisterUnitRequest) {
    return this.http.post<RegisteredUnit>(`${BASE}/admin/units`, req);
  }
  updateUnit(deviceId: string, patch: UpdateUnitRequest) {
    return this.http.patch<RegisteredUnit>(`${BASE}/admin/units/${encodeURIComponent(deviceId)}`, patch);
  }
}
