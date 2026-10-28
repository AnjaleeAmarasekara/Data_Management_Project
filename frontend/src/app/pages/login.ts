import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../shared/auth.service';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <div class="login-wrap">
      <form class="card login-card" (ngSubmit)="submit()">
        <div class="brand">
          <span class="brand-mark" aria-hidden="true">&#10052;</span>
          <div>
            <div class="brand-name">ColdSense-DQ</div>
            <div class="subtle">Sign in to the cold-chain dashboard</div>
          </div>
        </div>

        <label>
          Email
          <input type="email" name="email" [(ngModel)]="email" required autocomplete="username" autofocus />
        </label>
        <label>
          Password
          <input type="password" name="password" [(ngModel)]="password" required autocomplete="current-password" />
        </label>

        @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

        <button type="submit" class="primary" [disabled]="loading()">{{ loading() ? 'Signing in...' : 'Sign in' }}</button>

        <p class="subtle demo-note">
          Demo accounts (password <code>ColdSense#2025</code>): admin&#64;coldsense.lk,
          ops.manager&#64;coldsense.lk, supervisor&#64;coldsense.lk, staff&#64;coldsense.lk
        </p>
      </form>
    </div>
  `,
  styles: [`
    .login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg); padding: 16px; }
    .login-card { width: 100%; max-width: 380px; display: flex; flex-direction: column; gap: 14px; }
    .brand { display: flex; gap: 10px; align-items: center; margin-bottom: 6px; }
    .brand-mark { font-size: 30px; color: var(--accent); }
    .brand-name { font-weight: 700; font-size: 18px; }
    label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
    input { font: inherit; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; }
    input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
    .demo-note { margin: 4px 0 0; font-size: 12px; line-height: 1.5; }
    .demo-note code { background: #eceff4; padding: 1px 5px; border-radius: 4px; }
  `],
})
export class LoginPage {
  private auth = inject(AuthService);
  private router = inject(Router);

  email = '';
  password = '';
  loading = signal(false);
  error = signal<string | null>(null);

  submit() {
    if (!this.email || !this.password) return;
    this.loading.set(true);
    this.error.set(null);
    this.auth.login(this.email, this.password).subscribe({
      next: () => this.router.navigateByUrl('/monitoring'),
      error: (err) => {
        this.loading.set(false);
        this.error.set(err?.status === 401 ? 'Incorrect email or password.' : 'Cannot reach the API at localhost:8000. Start the backend (see README).');
      },
    });
  }
}
