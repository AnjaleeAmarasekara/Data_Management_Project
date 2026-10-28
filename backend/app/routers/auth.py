from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.auth import User, create_token, verify_password
from backend.app.db import connect

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: User


@router.post("/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, full_name, role, role_level, password_hash FROM app_user WHERE email = %s AND is_active",
            (req.email.lower().strip(),),
        )
        row = cur.fetchone()

    if not row or not verify_password(req.password, row[5]):
        raise HTTPException(401, "Incorrect email or password")

    user = User(id=row[0], email=row[1], full_name=row[2], role=row[3], role_level=row[4])
    return LoginResponse(token=create_token(user), user=user)
