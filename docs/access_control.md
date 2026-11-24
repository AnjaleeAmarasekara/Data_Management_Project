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

**Brute-force protection.** After 5 failed sign-ins for one email within 15 minutes,
further attempts are refused with HTTP 429 without the password even being checked. The
count is read from the audit log, so it survives an API restart and needs no extra table,
and a successful sign-in resets it. An unknown email is checked against a dummy bcrypt
hash, so it takes as long as a wrong password and the response time does not reveal which
emails have accounts.

**Secrets fail closed.** `JWT_SECRET` and `DEVICE_API_KEY` used to fall back to hardcoded
values when unset. Those values were committed to the repository, so a deployment that
forgot to set them would sign tokens with a public string - enough to forge an
administrator token. The API now refuses to start if either is missing, shorter than 32
characters, or still a template placeholder (`backend/app/settings.py`).

The device key had a second problem: the value in `.env.example` was a real-looking key,
committed in the first commit, and the live `.env` was using that same value. It has been
rotated and the template now holds a placeholder. Rotation, not history rewriting, is the
remedy: the old key is still in git history but no longer opens anything.

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
- The lockout is per email, not per source address, so it slows a targeted guess but not a
  spray of one password across many accounts.
- Device authentication is one shared key for the whole fleet. A real deployment would
  issue a key per device, so one compromised gateway could be revoked alone.
