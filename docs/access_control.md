# Authentication and Role Hierarchy

## Why hierarchical, not just two roles

A retail SME's cold-chain team isn't flat: a staff member monitors, a quality supervisor
acts on problems, an operations manager runs the wider operation, and someone needs to
administer accounts. Modeling that as a single ordered hierarchy (`role_level`, an
integer) rather than a per-role permission list means every access check is one
comparison -- "does this role rank at or above the minimum this action needs" -- instead
of maintaining a growing table of who-can-do-what. It also matches how these
organizations actually work: a manager can do everything a supervisor can, not a
different, disjoint set of things.

## The hierarchy

| Level | Role | Can do |
|---|---|---|
| 0 | Staff (Viewer) | View Monitoring, Data Quality, Alerts, Impact. Nothing else. |
| 1 | Quality Supervisor | Everything Staff can, plus: run the fault-injection demo, acknowledge alerts, view/export Reports (JSON and PDF). |
| 2 | Operations Manager | Everything Quality Supervisor can. (No unit-specific extra capabilities are built yet; this level exists so the hierarchy has a real middle tier reviewers can act on if the system grows, e.g. threshold configuration.) |
| 3 | Administrator | Everything Operations Manager can, plus: create accounts, change roles, enable/disable accounts. |

Enforced in `backend/app/auth.py::require_role`, applied per-router or per-endpoint in
`backend/app/main.py` and the individual routers. The frontend mirrors the same checks
(`AuthService.hasRole`) to hide controls a role can't use, and `roleGuard` blocks direct
navigation to `/reports` or `/admin/users` -- but the **enforcement is server-side**; the
frontend checks are only for a better user experience, not the security boundary.

## Login

Email + password, JWT bearer token (`backend/app/auth.py`), 12-hour expiry. Passwords are
bcrypt-hashed (`app_user.password_hash`); nothing is stored in plain text. There is no
self-service registration -- accounts are created by an Administrator via User
Management, matching how access is actually provisioned in a real SME (IT/ops sets up
accounts, staff don't sign themselves up).

## Demo accounts

Seeded by `backend/seed_users.py`, password `ColdSense#2025` for all four:

| Email | Role |
|---|---|
| admin@coldsense.lk | Administrator |
| ops.manager@coldsense.lk | Operations Manager |
| supervisor@coldsense.lk | Quality Supervisor |
| staff@coldsense.lk | Staff (Viewer) |

## Known limitations (worth stating in the report)

- No password reset flow (an Administrator would reset it by editing the account; that
  UI isn't built).
- No audit log beyond alert acknowledgements (who ran the fault-injection demo, who
  changed a role, etc. isn't recorded).
- The JWT secret (`JWT_SECRET` in `.env`) defaults to a dev value if unset -- fine for a
  local demo, not for a real deployment.
