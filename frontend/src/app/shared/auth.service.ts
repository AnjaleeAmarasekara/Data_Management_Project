import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { tap } from 'rxjs';

const BASE = 'http://localhost:8000/api';
const STORAGE_KEY = 'coldsense.auth';

// Lowest to highest -- must match backend/app/auth.py ROLES exactly.
export const ROLES = ['staff', 'quality_supervisor', 'operations_manager', 'admin'] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Record<Role, string> = {
  staff: 'Staff (Viewer)',
  quality_supervisor: 'Quality Supervisor',
  operations_manager: 'Operations Manager',
  admin: 'Administrator',
};

export interface AuthUser {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  role_level: number;
}

interface StoredAuth {
  token: string;
  user: AuthUser;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);
  private router = inject(Router);

  private state = signal<StoredAuth | null>(this.readStorage());

  user = computed(() => this.state()?.user ?? null);
  token = computed(() => this.state()?.token ?? null);
  isLoggedIn = computed(() => this.state() !== null);

  login(email: string, password: string) {
    return this.http.post<StoredAuth>(`${BASE}/auth/login`, { email, password }).pipe(tap((v) => this.setState(v)));
  }

  logout(): void {
    this.setState(null);
    this.router.navigateByUrl('/login');
  }

  hasRole(min: Role): boolean {
    const u = this.user();
    return !!u && u.role_level >= ROLES.indexOf(min);
  }

  private setState(v: StoredAuth | null): void {
    this.state.set(v);
    try {
      if (v) localStorage.setItem(STORAGE_KEY, JSON.stringify(v));
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* private browsing / storage blocked -- session still works for this tab */
    }
  }

  private readStorage(): StoredAuth | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? (JSON.parse(raw) as StoredAuth) : null;
    } catch {
      return null;
    }
  }
}
