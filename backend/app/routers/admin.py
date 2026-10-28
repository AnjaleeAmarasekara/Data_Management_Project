"""User management -- Admin only. Demonstrates the top of the role hierarchy."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from backend.app.auth import ROLES, User, hash_password, require_role
from backend.app.db import connect

router = APIRouter(tags=["admin"], dependencies=[Depends(require_role("admin"))])


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool


class CreateUserRequest(BaseModel):
    email: str
    full_name: str
    role: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email address")
        return v


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


@router.get("/admin/users", response_model=list[UserOut])
def list_users():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email, full_name, role, is_active FROM app_user ORDER BY role_level DESC, email")
        return [UserOut(id=r[0], email=r[1], full_name=r[2], role=r[3], is_active=r[4]) for r in cur.fetchall()]


@router.post("/admin/users", response_model=UserOut, status_code=201)
def create_user(req: CreateUserRequest, admin: Annotated[User, Depends(require_role("admin"))]):
    if req.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM app_user WHERE email = %s", (req.email.lower(),))
        if cur.fetchone():
            raise HTTPException(409, "A user with that email already exists")
        cur.execute(
            """
            INSERT INTO app_user (email, password_hash, full_name, role, role_level)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (req.email.lower(), hash_password(req.password), req.full_name, req.role, ROLES.index(req.role)),
        )
        user_id = cur.fetchone()[0]
        conn.commit()
    return UserOut(id=user_id, email=req.email.lower(), full_name=req.full_name, role=req.role, is_active=True)


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, req: UpdateUserRequest, admin: Annotated[User, Depends(require_role("admin"))]):
    if user_id == admin.id and req.is_active is False:
        raise HTTPException(400, "You cannot deactivate your own account")
    if req.role is not None and req.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")

    with connect() as conn, conn.cursor() as cur:
        if req.role is not None:
            cur.execute("UPDATE app_user SET role = %s, role_level = %s WHERE id = %s", (req.role, ROLES.index(req.role), user_id))
        if req.is_active is not None:
            cur.execute("UPDATE app_user SET is_active = %s WHERE id = %s", (req.is_active, user_id))
        conn.commit()
        cur.execute("SELECT id, email, full_name, role, is_active FROM app_user WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such user")
    return UserOut(id=row[0], email=row[1], full_name=row[2], role=row[3], is_active=row[4])
