import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AppUser, ApiService } from '../shared/api.service';
import { AuthService, ROLES, ROLE_LABELS, Role } from '../shared/auth.service';

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
  `,
  styles: [`input, select { font: inherit; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }`],
})
export class UsersPage implements OnInit {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  readonly roles = ROLES;

  users = signal<AppUser[]>([]);
  error = signal<string | null>(null);
  creating = signal(false);
  me = this.auth.user;

  form = { full_name: '', email: '', role: 'staff' as Role, password: '' };

  ngOnInit() {
    this.load();
  }

  load() {
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
