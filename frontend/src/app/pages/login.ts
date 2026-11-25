import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../shared/auth.service';

// Demo accounts are listed on the sign-in page on purpose: this is a review build, and a
// reviewer should not have to hunt through the README to get in. One click fills the form.
const DEMO_PASSWORD = 'ColdSense#2025';
const DEMO_ACCOUNTS = [
  { role: 'Administrator', email: 'admin@coldsense.lk' },
  { role: 'Operations Manager', email: 'ops.manager@coldsense.lk' },
  { role: 'Quality Supervisor', email: 'supervisor@coldsense.lk' },
  { role: 'Staff', email: 'staff@coldsense.lk' },
];

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <div class="login">
      <section class="pitch" aria-hidden="true">
        <div class="pitch-brand">
          <svg viewBox="0 0 32 32" width="34" height="34"><rect width="32" height="32" rx="8" fill="#0f766e" />
            <path d="M14 8.5a2 2 0 0 1 4 0v8.7a4.2 4.2 0 1 1-4 0z" fill="none" stroke="#fff" stroke-width="1.7" stroke-linejoin="round" />
            <circle cx="16" cy="21.4" r="2" fill="#fff" />
            <path d="M21 9.5h3M21 13h2M21 16.5h3" stroke="#fff" stroke-width="1.5" stroke-linecap="round" opacity="0.7" /></svg>
          <span>ColdSense-DQ</span>
        </div>
        <div class="pitch-body">
          <h2>Trust the data before you trust the alarm.</h2>
          <p>Every temperature reading is scored for reliability before anything is raised, forecast or costed.</p>
        </div>
        <ul class="pitch-facts">
          <li><strong>123</strong> premises monitored</li>
          <li><strong>1.26 M</strong> readings checked</li>
          <li><strong>10</strong> fault types tested</li>
        </ul>
      </section>

      <section class="panel">
        <form class="signin" (ngSubmit)="submit()">
          <h1>Sign in</h1>
          <p class="subtle lead">Use your ColdSense account.</p>

          <label>
            <span>Email</span>
            <input type="email" name="email" [(ngModel)]="email" required autocomplete="username" autofocus placeholder="name@company.lk" />
          </label>
          <label>
            <span>Password</span>
            <input type="password" name="password" [(ngModel)]="password" required autocomplete="current-password" />
          </label>

          @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

          <button type="submit" class="primary go" [disabled]="loading()">{{ loading() ? 'Signing in…' : 'Sign in' }}</button>
        </form>

        <details class="demo">
          <summary class="demo-head">Demo accounts <span class="subtle">· password <code>{{ demoPassword }}</code></span></summary>
          <div class="demo-list">
            @for (a of demoAccounts; track a.email) {
              <button type="button" class="demo-row" (click)="fill(a.email)">
                <span class="demo-role">{{ a.role }}</span>
                <span class="subtle demo-email">{{ a.email }}</span>
              </button>
            }
          </div>
        </details>
      </section>
    </div>
  `,
  styles: [`
    .login { min-height: 100dvh; display: grid; grid-template-columns: minmax(340px, 5fr) 6fr; background: var(--surface); }

    .pitch { background: var(--rail); color: var(--rail-text); padding: 40px 48px; display: flex; flex-direction: column; justify-content: space-between; gap: 32px; }
    .pitch-brand { display: flex; align-items: center; gap: 12px; color: #fff; font-weight: 650; font-size: 16px; letter-spacing: -0.01em; }
    .pitch-body { max-width: 30ch; }
    .pitch-body h2 { color: #fff; font-size: 30px; font-weight: 650; line-height: 1.15; letter-spacing: -0.025em; margin: 0 0 14px; }
    .pitch-body p { font-size: 15px; line-height: 1.55; margin: 0; max-width: 34ch; }
    .pitch-facts { list-style: none; margin: 0; padding: 18px 0 0; display: flex; gap: 28px; border-top: 1px solid rgba(255, 255, 255, 0.1); font-size: 13px; color: var(--rail-faint); }
    .pitch-facts strong { display: block; color: #fff; font-size: 19px; font-weight: 650; letter-spacing: -0.02em; }

    .panel { display: flex; flex-direction: column; justify-content: center; gap: 28px; padding: 40px 6vw; max-width: 560px; width: 100%; margin: 0 auto; }
    .signin { display: flex; flex-direction: column; gap: 14px; }
    .signin h1 { margin: 0; font-size: 26px; }
    .lead { margin: -8px 0 6px; }
    label { display: flex; flex-direction: column; gap: 5px; }
    label span { font-size: 13px; font-weight: 500; }
    input { width: 100%; min-height: 40px; }
    .go { min-height: 40px; margin-top: 4px; }

    .demo { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); padding: 14px 14px 8px; }
    .demo-head { font-size: 13px; font-weight: 600; cursor: pointer; padding-bottom: 6px; }
    .demo:not([open]) { padding-bottom: 8px; }
    .demo-list { display: flex; flex-direction: column; }
    .demo-row { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; width: 100%; text-align: left; background: transparent; border: 0; border-radius: 6px; padding: 7px 8px; min-height: 0; }
    .demo-row:hover:not(:disabled) { background: var(--surface); }
    .demo-role { font-weight: 500; }
    .demo-email { font-size: 12.5px; }

    @media (max-width: 860px) {
      .login { grid-template-columns: 1fr; }
      .pitch { display: none; }
      .panel { padding: 28px 20px; }
    }
  `],
})
export class LoginPage {
  private auth = inject(AuthService);
  private router = inject(Router);

  demoAccounts = DEMO_ACCOUNTS;
  demoPassword = DEMO_PASSWORD;

  email = '';
  password = '';
  loading = signal(false);
  error = signal<string | null>(null);

  fill(email: string) {
    this.email = email;
    this.password = DEMO_PASSWORD;
    this.error.set(null);
  }

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
