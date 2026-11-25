import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService, AppUser, AuditEntry } from '../shared/api.service';
import { AuthService, ROLES, ROLE_LABELS, Role } from '../shared/auth.service';
import { fmtTs } from '../shared/format';

@Component({
  selector: 'app-users',
  imports: [FormsModule],
  template: `
    <div class="page-head">
      <div>
        <h1>Users</h1>
        <p class="subtle">Manage who can sign in and what they can do.</p>
      </div>
      @if (tab() === 'accounts') {
        <button class="primary" (click)="adding.set(!adding())">{{ adding() ? 'Cancel' : 'Add user' }}</button>
      }
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <div class="tabs" role="tablist">
      <button role="tab" [class.on]="tab() === 'accounts'" (click)="tab.set('accounts')">Accounts</button>
      <button role="tab" [class.on]="tab() === 'activity'" (click)="tab.set('activity')">Activity log</button>
    </div>

    @if (tab() === 'accounts') {
      @if (adding()) {
        <section class="card" style="margin-bottom: 16px">
          <h2>New user</h2>
          <form class="add" (ngSubmit)="create()">
            <input placeholder="Full name" [(ngModel)]="form.full_name" name="full_name" required />
            <input placeholder="Email" type="email" [(ngModel)]="form.email" name="email" required />
            <select [(ngModel)]="form.role" name="role">
              @for (r of roles; track r) { <option [value]="r">{{ roleLabel(r) }}</option> }
            </select>
            <input placeholder="Temporary password" type="text" [(ngModel)]="form.password" name="password" required />
            <button type="submit" class="primary" [disabled]="creating()">{{ creating() ? 'Creating…' : 'Create' }}</button>
          </form>
        </section>
      }

      <section class="card">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Role</th><th>Status</th><th></th></tr></thead>
            <tbody>
              @for (u of users(); track u.id) {
                <tr>
                  <td>
                    <div class="person">
                      <span class="avatar">{{ initials(u.full_name) }}</span>
                      <div><strong>{{ u.full_name }}</strong><div class="subtle small">{{ u.email }}</div></div>
                    </div>
                  </td>
                  <td>
                    <select (change)="changeRole(u, $any($event.target).value)" [disabled]="u.id === me()?.id" aria-label="Role">
                      @for (r of roles; track r) { <option [value]="r" [selected]="r === u.role">{{ roleLabel(r) }}</option> }
                    </select>
                  </td>
                  <td><span class="pill" [class]="u.is_active ? 'ok' : 'neutral'">{{ u.is_active ? 'Active' : 'Disabled' }}</span></td>
                  <td class="num">
                    @if (u.id === me()?.id) { <span class="subtle small">You</span> }
                    @else { <button (click)="toggleActive(u)">{{ u.is_active ? 'Disable' : 'Enable' }}</button> }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>
    }

    @if (tab() === 'activity') {
      <section class="card">
        <div class="row" style="margin-bottom: 12px">
          <label class="row small subtle" style="gap: 6px"><input type="checkbox" [checked]="hideLogins()" (change)="hideLogins.set(!hideLogins()); page.set(0)" /> Hide sign-ins</label>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead>
            <tbody>
              @for (a of pageRows(); track a.id) {
                <tr>
                  <td class="subtle">{{ fmt(a.occurred_at) }}</td>
                  <td>{{ a.actor || '—' }}</td>
                  <td><span class="pill" [class]="actionClass(a.action)">{{ actionText(a.action) }}</span></td>
                  <td class="subtle">{{ [a.target, a.detail].filter(isText).join(' · ') }}</td>
                </tr>
              } @empty {
                <tr><td colspan="4" class="empty">No activity recorded yet.</td></tr>
              }
            </tbody>
          </table>
        </div>
        @if (filteredAudit().length > pageSize) {
          <div class="table-foot">
            <span>{{ page() * pageSize + 1 }}–{{ min((page() + 1) * pageSize, filteredAudit().length) }} of {{ filteredAudit().length }}</span>
            <span class="row">
              <button (click)="page.set(page() - 1)" [disabled]="page() === 0">Previous</button>
              <button (click)="page.set(page() + 1)" [disabled]="(page() + 1) * pageSize >= filteredAudit().length">Next</button>
            </span>
          </div>
        }
      </section>
    }
  `,
  styles: [`
    .add { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
    .person { display: flex; align-items: center; gap: 10px; }
    .avatar { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 650; background: var(--accent-soft); color: var(--accent); flex: none; }
  `],
})
export class UsersPage implements OnInit {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  readonly roles = ROLES;

  users = signal<AppUser[]>([]);
  audit = signal<AuditEntry[]>([]);
  error = signal<string | null>(null);
  creating = signal(false);
  me = this.auth.user;

  form = { full_name: '', email: '', role: 'staff' as Role, password: '' };
  tab = signal<'accounts' | 'activity'>('accounts');
  adding = signal(false);
  hideLogins = signal(true);
  page = signal(0);
  readonly pageSize = 15;
  filteredAudit = computed(() => this.audit().filter((a) => !this.hideLogins() || a.action !== 'login_succeeded'));
  pageRows = computed(() => this.filteredAudit().slice(this.page() * this.pageSize, (this.page() + 1) * this.pageSize));

  min(a: number, b: number) {
    return Math.min(a, b);
  }

  isText(v: string | null): v is string {
    return !!v;
  }

  initials(name: string) {
    return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  }

  actionText(action: string) {
    const t: Record<string, string> = {
      login_succeeded: 'Signed in',
      login_failed: 'Failed sign-in',
      login_locked: 'Account locked',
      user_created: 'User added',
      role_changed: 'Role changed',
      account_disabled: 'User disabled',
      account_enabled: 'User enabled',
      alert_acknowledged: 'Alert acknowledged',
      demo_run: 'Fault test run',
      unit_registered: 'Probe registered',
      unit_limits_changed: 'Limits changed',
      unit_monitoring_paused: 'Monitoring paused',
      unit_monitoring_resumed: 'Monitoring resumed',
    };
    return t[action] ?? action.replace(/_/g, ' ');
  }

  ngOnInit() {
    this.load();
  }

  fmt(ts: string) {
    return fmtTs(ts);
  }

  actionClass(action: string) {
    if (action === 'login_failed' || action === 'account_disabled') return 'warning';
    if (action === 'role_changed' || action === 'user_created') return 'watch';
    return 'ok';
  }

  load() {
    this.api.auditLog().subscribe({ next: (a) => this.audit.set(a), error: () => {} });
    this.api.users().subscribe({
      next: (u) => this.users.set(u),
      error: () => this.error.set('Could not load users.'),
    });
  }

  roleLabel(r: Role) {
    return ROLE_LABELS[r];
  }

  changeRole(u: AppUser, role: Role) {
    this.api.updateUser(u.id, { role }).subscribe({ next: () => this.load(), error: () => this.error.set('Could not update role.') });
  }

  toggleActive(u: AppUser) {
    this.api.updateUser(u.id, { is_active: !u.is_active }).subscribe({ next: () => this.load(), error: () => this.error.set('Could not update status.') });
  }

  create() {
    if (!this.form.full_name || !this.form.email || !this.form.password) return;
    this.creating.set(true);
    this.api.createUser(this.form).subscribe({
      next: () => {
        this.creating.set(false);
        this.adding.set(false);
        this.form = { full_name: '', email: '', role: 'staff', password: '' };
        this.load();
      },
      error: (err) => {
        this.creating.set(false);
        this.error.set(err?.status === 409 ? 'A user with that email already exists.' : 'Could not create the account.');
      },
    });
  }
}
