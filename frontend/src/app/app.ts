import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService, ROLE_LABELS } from './shared/auth.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    @if (auth.isLoggedIn()) {
      <div class="shell">
        <aside class="sidebar">
          <div class="brand">
            <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
              <rect width="32" height="32" rx="8" fill="#0f766e" />
              <path d="M14 8.5a2 2 0 0 1 4 0v8.7a4.2 4.2 0 1 1-4 0z" fill="none" stroke="#fff" stroke-width="1.7" stroke-linejoin="round" />
              <circle cx="16" cy="21.4" r="2" fill="#fff" />
              <path d="M21 9.5h3M21 13h2M21 16.5h3" stroke="#fff" stroke-width="1.5" stroke-linecap="round" opacity="0.7" />
            </svg>
            <div>
              <div class="brand-name">ColdSense-DQ</div>
              <div class="brand-sub">Cold-chain data quality</div>
            </div>
          </div>

          <nav aria-label="Main">
            <div class="nav-group">Operations</div>
            <a routerLink="/monitoring" routerLinkActive="active">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12h4l2.5-6 4 12 2.5-6H21" /></svg>
              Monitoring
            </a>
            <a routerLink="/alerts" routerLinkActive="active">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></svg>
              Alerts
            </a>

            <div class="nav-group">Analysis</div>
            <a routerLink="/quality" routerLinkActive="active">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5.5c0 4.4-3 7.7-7 9.5-4-1.8-7-5.1-7-9.5V6z" /><path d="M9 12l2 2 4-4" /></svg>
              Data Quality
            </a>
            <a routerLink="/trends" routerLinkActive="active">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 3.5v17h17" /><path d="M7.5 15l3.5-4.5 3 3L19 7" /></svg>
              Trends
            </a>
            <a routerLink="/impact" routerLinkActive="active">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 20V12M12 20V5M19 20v-5" /></svg>
              Business Impact
            </a>

            @if (auth.hasRole('quality_supervisor')) {
              <div class="nav-group">Administration</div>
              <a routerLink="/reports" routerLinkActive="active">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 3.5h7l4.5 4.5V20.5H7z" /><path d="M14 3.5V8h4.5M10 13h5.5M10 16.5h5.5" /></svg>
                Reports
              </a>
            }
            @if (auth.hasRole('admin')) {
              <a routerLink="/admin/users" routerLinkActive="active">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="8.5" r="3" /><path d="M3.5 19.5c0-3 2.5-5.3 5.5-5.3s5.5 2.3 5.5 5.3" /><path d="M16 5.8a3 3 0 0 1 0 5.4M18.2 14.6c1.7.7 2.8 2.4 2.8 4.9" /></svg>
                Users
              </a>
              <a routerLink="/admin/devices" routerLinkActive="active">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="7" y="3.5" width="10" height="17" rx="2" /><path d="M12 7.5v6" /><circle cx="12" cy="16" r="1.6" /></svg>
                Devices
              </a>
            }
          </nav>

          <div class="spacer"></div>

          <details class="about-data">
            <summary>About this data</summary>
            <p>
              Development data: real refrigerator telemetry (Bangkok, tropical climate) and real perishable retail sales,
              standing in for confidential Sri Lankan SME data. All times are UTC.
            </p>
          </details>

          @if (auth.user(); as u) {
            <div class="account">
              <span class="avatar" aria-hidden="true">{{ initials(u.full_name) }}</span>
              <div class="who">
                <div class="account-name">{{ u.full_name }}</div>
                <div class="account-role">{{ roleLabel(u.role) }}</div>
              </div>
              <button class="signout" (click)="logout()" title="Sign out" aria-label="Sign out">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" /><path d="M10 16l-4-4 4-4M6 12h10" /></svg>
              </button>
            </div>
          }
        </aside>
        <main class="content">
          <router-outlet />
        </main>
      </div>
    } @else {
      <router-outlet />
    }
  `,
  styles: [`
    .about-data { padding: 0 8px; color: var(--rail-faint); font-size: 12px; }
    .about-data summary { cursor: pointer; list-style: none; font-weight: 500; padding: 4px 0; transition: color 140ms var(--ease); }
    .about-data summary::-webkit-details-marker { display: none; }
    .about-data summary:hover { color: var(--rail-text); }
    .about-data p { margin: 4px 0 2px; line-height: 1.55; }

    .account { border-top: 1px solid rgba(255, 255, 255, 0.09); padding: 12px 6px 2px; display: flex; align-items: center; gap: 10px; }
    .avatar { width: 32px; height: 32px; border-radius: 8px; flex: none; display: grid; place-items: center; font-size: 12px; font-weight: 650; letter-spacing: 0.02em; color: #d8f3ef; background: rgba(95, 211, 198, 0.16); }
    .who { min-width: 0; flex: 1; }
    .account-name { color: #fff; font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .account-role { color: var(--rail-faint); font-size: 12px; }
    .signout { width: 32px; min-height: 32px; padding: 0; display: grid; place-items: center; background: transparent; border-color: transparent; color: var(--rail-faint); }
    .signout svg { width: 18px; height: 18px; }
    .signout:hover:not(:disabled) { background: var(--rail-hover); border-color: transparent; color: #fff; }
    .signout:focus-visible { outline-color: #5fd3c6; }
  `],
})
export class App {
  auth = inject(AuthService);
  private router = inject(Router);

  roleLabel(r: keyof typeof ROLE_LABELS) {
    return ROLE_LABELS[r];
  }

  initials(name: string) {
    return name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0].toUpperCase())
      .join('');
  }

  logout() {
    this.auth.logout();
  }
}
