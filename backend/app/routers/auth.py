from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.audit import log_action
from backend.app.auth import User, create_token, hash_password, verify_password
from backend.app.db import connect

router = APIRouter(tags=["auth"])

# Failed logins were audited but nothing acted on them, so a password could be guessed at
# whatever rate the network allowed. After MAX_FAILURES consecutive failures for one email
# within LOCKOUT_WINDOW, further attempts are refused without checking the password. The
# count is read from the audit log itself, so it survives an API restart and needs no new
# table, and a successful login resets it.
MAX_FAILURES = 5
LOCKOUT_WINDOW = "15 minutes"

# Checked against when the email is unknown, so an unknown email costs the same bcrypt
# time as a wrong password. Otherwise the faster reply would reveal which emails have
# accounts.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-account")


def _recent_failures(conn, email: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*) FROM audit_log
            WHERE actor_email = %(e)s AND action = 'login_failed'
              AND occurred_at > now() - interval '{LOCKOUT_WINDOW}'
              AND occurred_at > coalesce(
                  (SELECT max(occurred_at) FROM audit_log
                   WHERE actor_email = %(e)s AND action = 'login_succeeded'),
                  '-infinity'::timestamptz)
            """,
            {"e": email},
        )
        return cur.fetchone()[0]


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: User


@router.post("/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    email = req.email.lower().strip()
    with connect() as conn:
        if _recent_failures(conn, email) >= MAX_FAILURES:
            log_action(conn, email, "login_locked", detail=f"{MAX_FAILURES} failures within {LOCKOUT_WINDOW}")
            raise HTTPException(429, f"Too many failed attempts. Try again in {LOCKOUT_WINDOW}.")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, full_name, role, role_level, password_hash FROM app_user WHERE email = %s AND is_active",
                (email,),
            )
            row = cur.fetchone()

        password_ok = verify_password(req.password, row[5] if row else _DUMMY_HASH)
        if not row or not password_ok:
            log_action(conn, email, "login_failed", detail="incorrect email or password, or account disabled")
            raise HTTPException(401, "Incorrect email or password")

        user = User(id=row[0], email=row[1], full_name=row[2], role=row[3], role_level=row[4])
        log_action(conn, user, "login_succeeded")
    return LoginResponse(token=create_token(user), user=user)
