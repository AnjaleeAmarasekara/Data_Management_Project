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
            <span class="brand-mark" aria-hidden="true">&#10052;</span>
            <div>
              <div class="brand-name">ColdSense-DQ</div>
              <div class="brand-sub">Cold-chain data quality</div>
            </div>
          </div>
          <nav aria-label="Main">
            <a routerLink="/monitoring" routerLinkActive="active">Real-Time Monitoring</a>
            <a routerLink="/quality" routerLinkActive="active">Data Quality</a>
            <a routerLink="/alerts" routerLinkActive="active">Alerts</a>
            <a routerLink="/impact" routerLinkActive="active">Risk &amp; Business Impact</a>
            @if (auth.hasRole('quality_supervisor')) { <a routerLink="/reports" routerLinkActive="active">Reports</a> }
            @if (auth.hasRole('admin')) { <a routerLink="/admin/users" routerLinkActive="active">User Management</a> }
          </nav>
          <div class="spacer"></div>
          @if (auth.user(); as u) {
            <div class="account">
              <div class="account-name">{{ u.full_name }}</div>
              <div class="account-role">{{ roleLabel(u.role) }}</div>
              <button (click)="logout()">Sign out</button>
            </div>
          }
          <p class="sidebar-note">
            Development data: public Kaggle datasets standing in for confidential retail cold-chain data.
            Times are UTC.
          </p>
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
    .sidebar { display: flex; flex-direction: column; }
    .account { border-top: 1px solid #24354f; padding-top: 12px; margin-top: 12px; display: flex; flex-direction: column; gap: 2px; }
    .account-name { color: #fff; font-weight: 600; font-size: 13px; }
    .account-role { color: #93a4bd; font-size: 12px; margin-bottom: 6px; }
    .account button { width: 100%; background: transparent; border-color: #2c3e5c; color: #d5deec; }
    .account button:hover { border-color: #7cc4ff; color: #fff; }
  `],
})
export class App {
  auth = inject(AuthService);
  private router = inject(Router);

  roleLabel(r: keyof typeof ROLE_LABELS) {
    return ROLE_LABELS[r];
  }

  logout() {
    this.auth.logout();
  }
}
