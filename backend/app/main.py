import math

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.auth import require_role
from backend.app.routers import admin, alerts, auth, demo, ingest, quality, reports, security, trends, units

app = FastAPI(title="ColdSense-DQ API", version="0.1.0")


def _printable(value):
    """JSON has no NaN or infinity, so a rejected reading of NaN cannot be echoed back as-is."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _printable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_printable(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """FastAPI echoes the offending input inside a 422. When that input was NaN - exactly
    what the ingest validator rejects - encoding the error itself failed, turning a correct
    rejection into a 500. Make the echoed input printable instead."""
    errors = [{**e, "input": _printable(e.get("input")), "ctx": None} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# auth.router is public (login). ingest.router is machine-to-machine (device API key, a
# separate trust domain from human dashboard users) so it's not gated by require_role
# either. Everything else requires at least an authenticated, active account (role_level
# >= staff, the lowest rung); demo/reports/admin raise that bar further, either at the
# router level (admin) or per-endpoint (alerts.acknowledge).
app.include_router(auth.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")

for router in (units.router, quality.router, alerts.router, trends.router):
    app.include_router(router, prefix="/api", dependencies=[Depends(require_role("staff"))])

app.include_router(demo.router, prefix="/api", dependencies=[Depends(require_role("quality_supervisor"))])
app.include_router(reports.router, prefix="/api", dependencies=[Depends(require_role("quality_supervisor"))])
app.include_router(security.router, prefix="/api", dependencies=[Depends(require_role("quality_supervisor"))])
app.include_router(admin.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
