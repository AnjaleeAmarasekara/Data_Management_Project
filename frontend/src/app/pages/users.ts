import { Component, OnInit, inject, signal } from '@angular/core';
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
        <h1>User Management</h1>
        <p class="subtle">Create accounts and assign roles. Administrator only.</p>
      </div>
    </div>

    @if (error(); as e) { <div class="notice error" role="alert">{{ e }}</div> }

    <section class="card">
      <h2>Accounts</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
            @for (u of users(); track u.id) {
              <tr>
                <td>{{ u.full_name }}</td>
                <td>{{ u.email }}</td>
                <td>
                  <select (change)="changeRole(u, $any($event.target).value)" [disabled]="u.id === me()?.id">
                    @for (r of roles; track r) { <option [value]="r" [selected]="r === u.role">{{ roleLabel(r) }}</option> }
                  </select>
                </td>
                <td><span class="pill" [class]="u.is_active ? 'ok' : 'neutral'">{{ u.is_active ? 'Active' : 'Disabled' }}</span></td>
                <td>
                  <button (click)="toggleActive(u)" [disabled]="u.id === me()?.id">{{ u.is_active ? 'Disable' : 'Enable' }}</button>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    </section>

    <section class="card" style="margin-top: 14px">
      <h2>Add account</h2>
      <form class="row" (ngSubmit)="create()">
        <input placeholder="Full name" [(ngModel)]="form.full_name" name="full_name" required />
        <input placeholder="Email" type="email" [(ngModel)]="form.email" name="email" required />
        <select [(ngModel)]="form.role" name="role">
          @for (r of roles; track r) { <option [value]="r">{{ roleLabel(r) }}</option> }
        </select>
        <input placeholder="Temporary password" type="text" [(ngModel)]="form.password" name="password" required />
        <button type="submit" class="primary" [disabled]="creating()">{{ creating() ? 'Creating...' : 'Create account' }}</button>
      </form>
    </section>

    <section class="card" style="margin-top: 14px">
      <h2>Audit log</h2>
      <p class="subtle">Who did what: logins (including failed attempts), account and role changes, alert acknowledgements and fault-injection demo runs. Most recent first.</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>When (UTC)</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
          <tbody>
            @for (a of audit(); track a.id) {
              <tr>
                <td>{{ fmt(a.occurred_at) }}</td>
                <td>{{ a.actor || '—' }}</td>
                <td><span class="pill" [class]="actionClass(a.action)">{{ a.action }}</span></td>
                <td>{{ a.target || '—' }}</td>
                <td class="subtle">{{ a.detail || '' }}</td>
              </tr>
            } @empty {
              <tr><td colspan="5" class="subtle">No activity recorded yet.</td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>
  `,
  styles: [`input, select { font: inherit; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }`],
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
