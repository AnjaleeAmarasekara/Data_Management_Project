"""User and unit management -- Admin only. Demonstrates the top of the role hierarchy."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from analytics.registry import MONITORABLE_ZONES
from backend.app.audit import log_action
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
        log_action(conn, admin, "user_created", target=req.email.lower(), detail=f"role={req.role}")
    return UserOut(id=user_id, email=req.email.lower(), full_name=req.full_name, role=req.role, is_active=True)


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, req: UpdateUserRequest, admin: Annotated[User, Depends(require_role("admin"))]):
    if user_id == admin.id and req.is_active is False:
        raise HTTPException(400, "You cannot deactivate your own account")
    if req.role is not None and req.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT email, role, is_active FROM app_user WHERE id = %s", (user_id,))
        before = cur.fetchone()
        if not before:
            raise HTTPException(404, "No such user")

        if req.role is not None:
            cur.execute("UPDATE app_user SET role = %s, role_level = %s WHERE id = %s", (req.role, ROLES.index(req.role), user_id))
        if req.is_active is not None:
            cur.execute("UPDATE app_user SET is_active = %s WHERE id = %s", (req.is_active, user_id))
        conn.commit()

        if req.role is not None and req.role != before[1]:
            log_action(conn, admin, "role_changed", target=before[0], detail=f"{before[1]} -> {req.role}")
        if req.is_active is not None and req.is_active != before[2]:
            log_action(conn, admin, "account_enabled" if req.is_active else "account_disabled", target=before[0])

        cur.execute("SELECT id, email, full_name, role, is_active FROM app_user WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return UserOut(id=row[0], email=row[1], full_name=row[2], role=row[3], is_active=row[4])


# ---------------------------------------------------------------- unit registry
#
# Registering a unit is what lets a new physical probe push readings at all: ingestion
# refuses any device_id that is not in sensor_unit and logs the attempt as an intrusion.
# Registration is therefore an admin action and is audit-logged, like account changes.

# Upper-case site and unit parts ending in a cold-storage zone code, e.g. LIVE-001-MS. The
# zone suffix is not decoration: the quality layer chooses its noise, drift and replay
# thresholds from it (dq_engine/config.py), so a freezer registered under a chiller code
# would be judged by the wrong physics.
DEVICE_ID_RE = re.compile(r"^[A-Z0-9]{2,12}(?:-[A-Z0-9]{1,12}){0,2}-(" + "|".join(MONITORABLE_ZONES) + r")$")


class UnitLimits(BaseModel):
    setpoint_c: float = Field(ge=-40, le=20)
    max_safe_c: float = Field(ge=-35, le=25)
    min_safe_c: float | None = Field(default=None, ge=-45, le=15)

    @model_validator(mode="after")
    def _band_contains_setpoint(self):
        if self.max_safe_c <= self.setpoint_c:
            raise ValueError("max_safe_c must be above setpoint_c")
        if self.min_safe_c is not None and self.min_safe_c >= self.setpoint_c:
            raise ValueError("min_safe_c must be below setpoint_c")
        return self


class RegisterUnitRequest(UnitLimits):
    device_id: str
    unit_name: str = Field(min_length=2, max_length=60)
    site_id: str = Field(min_length=2, max_length=24)
    site_name: str = Field(min_length=2, max_length=80)
    categories: list[str] = Field(default_factory=list, max_length=10)
    warmup_min: int = Field(default=30, ge=0, le=240)

    @field_validator("device_id")
    @classmethod
    def _valid_device_id(cls, v: str) -> str:
        v = v.strip().upper()
        if not DEVICE_ID_RE.match(v):
            raise ValueError(f"device_id must look like SITE-UNIT-ZONE and end in one of {sorted(MONITORABLE_ZONES)}, e.g. LIVE-001-MS")
        return v


class UpdateUnitRequest(BaseModel):
    unit_name: str | None = Field(default=None, min_length=2, max_length=60)
    setpoint_c: float | None = Field(default=None, ge=-40, le=20)
    max_safe_c: float | None = Field(default=None, ge=-35, le=25)
    min_safe_c: float | None = Field(default=None, ge=-45, le=15)
    clear_min_safe: bool = False  # min_safe_c=None cannot mean "remove the lower bound" in a partial update
    categories: list[str] | None = Field(default=None, max_length=10)
    warmup_min: int | None = Field(default=None, ge=0, le=240)
    monitored: bool | None = None


UNIT_COLUMNS = "device_id, unit_name, unit_type, site_id, site_name, zone, setpoint_c, max_safe_c, min_safe_c, categories, clock, warmup_min, monitored, registered_at"


def _unit_out(r) -> dict:
    return {
        "device_id": r[0], "unit_name": r[1], "unit_type": r[2], "site_id": r[3], "site_name": r[4], "zone": r[5],
        "setpoint_c": r[6], "max_safe_c": r[7], "min_safe_c": r[8], "categories": list(r[9] or []),
        "clock": r[10], "warmup_min": r[11], "monitored": r[12], "registered_at": r[13].isoformat(),
    }


def _limits_text(r: dict) -> str:
    low = f"{r['min_safe_c']} to " if r["min_safe_c"] is not None else "up to "
    return f"setpoint {r['setpoint_c']} C, band {low}{r['max_safe_c']} C, warm-up {r['warmup_min']} min"


@router.get("/admin/units")
def list_registered_units():
    """Units registered through this API (clock = 'wall'), plus any demo unit whose limits
    were overridden here. The 856 historical channels that are scored but not monitored
    are left out - they are the scalability data set, not units anyone manages."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {UNIT_COLUMNS} FROM sensor_unit WHERE clock = 'wall' OR monitored ORDER BY registered_at, device_id")
        units = [_unit_out(r) for r in cur.fetchall()]
        cur.execute(
            """
            SELECT device_id, count(*), min(ts), max(ts) FROM sensor_reading
            WHERE device_id = ANY(%s) GROUP BY device_id
            """,
            ([u["device_id"] for u in units],),
        )
        seen = {r[0]: r[1:] for r in cur.fetchall()}
    for u in units:
        n, first, last = seen.get(u["device_id"], (0, None, None))
        u["readings"] = n
        u["first_reading"] = first.isoformat() if first else None
        u["last_reading"] = last.isoformat() if last else None
    return units


@router.post("/admin/units", status_code=201)
def register_unit(req: RegisterUnitRequest, admin: Annotated[User, Depends(require_role("admin"))]):
    zone = req.device_id.rsplit("-", 1)[1]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM sensor_unit WHERE device_id = %s", (req.device_id,))
        if cur.fetchone():
            raise HTTPException(409, f"{req.device_id} is already registered")
        cur.execute(
            f"""
            INSERT INTO sensor_unit (device_id, unit_name, unit_type, branch_name, site_id, site_name, zone,
                                     setpoint_c, max_safe_c, min_safe_c, categories, clock, warmup_min, monitored)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'wall', %s, TRUE)
            RETURNING {UNIT_COLUMNS}
            """,
            (
                req.device_id, req.unit_name, MONITORABLE_ZONES[zone], req.site_name, req.site_id.upper(), req.site_name, zone,
                req.setpoint_c, req.max_safe_c, req.min_safe_c, [c.strip() for c in req.categories if c.strip()], req.warmup_min,
            ),
        )
        unit = _unit_out(cur.fetchone())
        conn.commit()
        log_action(conn, admin, "unit_registered", target=req.device_id, detail=_limits_text(unit))
    return unit


@router.patch("/admin/units/{device_id}")
def update_unit(device_id: str, req: UpdateUnitRequest, admin: Annotated[User, Depends(require_role("admin"))]):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {UNIT_COLUMNS} FROM sensor_unit WHERE device_id = %s AND (clock = 'wall' OR monitored)", (device_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such registered unit")
        before = _unit_out(row)

        after = dict(before)
        for field in ("unit_name", "setpoint_c", "max_safe_c", "min_safe_c", "warmup_min", "monitored"):
            value = getattr(req, field)
            if value is not None:
                after[field] = value
        if req.clear_min_safe:
            after["min_safe_c"] = None
        if req.categories is not None:
            after["categories"] = [c.strip() for c in req.categories if c.strip()]
        # validate the band as it will be stored, not just the fields that were sent
        try:
            UnitLimits(setpoint_c=after["setpoint_c"], max_safe_c=after["max_safe_c"], min_safe_c=after["min_safe_c"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        cur.execute(
            f"""
            UPDATE sensor_unit SET unit_name = %s, setpoint_c = %s, max_safe_c = %s, min_safe_c = %s,
                   categories = %s, warmup_min = %s, monitored = %s
            WHERE device_id = %s RETURNING {UNIT_COLUMNS}
            """,
            (after["unit_name"], after["setpoint_c"], after["max_safe_c"], after["min_safe_c"],
             after["categories"], after["warmup_min"], after["monitored"], device_id),
        )
        unit = _unit_out(cur.fetchone())
        conn.commit()
        if _limits_text(before) != _limits_text(unit):
            # Limits decide what counts as an excursion. Changing them changes every alert
            # that follows, so who changed them and from what is part of the record.
            log_action(conn, admin, "unit_limits_changed", target=device_id, detail=f"{_limits_text(before)} -> {_limits_text(unit)}")
        if before["monitored"] != unit["monitored"]:
            log_action(conn, admin, "unit_monitoring_resumed" if unit["monitored"] else "unit_monitoring_paused", target=device_id)
    return unit


@router.get("/admin/audit")
def list_audit(limit: int = 100):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, actor_email, action, target, detail, occurred_at FROM audit_log ORDER BY occurred_at DESC LIMIT %s",
            (min(max(limit, 1), 1000),),
        )
        return [
            {"id": r[0], "actor": r[1], "action": r[2], "target": r[3], "detail": r[4], "occurred_at": r[5].isoformat()}
            for r in cur.fetchall()
        ]
