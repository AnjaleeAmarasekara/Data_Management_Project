"""Authentication and hierarchical role-based access control.

Roles form a single ordered hierarchy (role_level), so "can this user do X" is one
">=" comparison against the minimum level X requires, rather than a per-role permission
list. This mirrors a typical retail-SME org chart: an Operations Manager can do
everything a Quality Supervisor can, an Admin can do everything an Operations Manager
can, plus manage accounts.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from backend.app.db import connect

SECRET_KEY = os.environ.get("JWT_SECRET", "dev-only-secret-change-me")
ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 12

# Ordered lowest to highest. role_level in app_user is this list's index.
ROLES = ["staff", "quality_supervisor", "operations_manager", "admin"]
ROLE_LABELS = {
    "staff": "Staff (Viewer)",
    "quality_supervisor": "Quality Supervisor",
    "operations_manager": "Operations Manager",
    "admin": "Administrator",
}

bearer_scheme = HTTPBearer(auto_error=False)


class User(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    role_level: int


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _load_user(user_id: int) -> User | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, full_name, role, role_level FROM app_user WHERE id = %s AND is_active",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return User(id=row[0], email=row[1], full_name=row[2], role=row[3], role_level=row[4])


def get_current_user(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user = _load_user(int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer active")
    return user


def require_role(min_role: str):
    """Dependency factory: require role_level >= ROLES.index(min_role)."""
    min_level = ROLES.index(min_role)

    def _check(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role_level < min_level:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{min_role}' or higher")
        return user

    return _check
