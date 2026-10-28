from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.auth import require_role
from backend.app.routers import admin, alerts, auth, demo, ingest, quality, reports, security, units

app = FastAPI(title="ColdSense-DQ API", version="0.1.0")

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

for router in (units.router, quality.router, alerts.router):
    app.include_router(router, prefix="/api", dependencies=[Depends(require_role("staff"))])

app.include_router(demo.router, prefix="/api", dependencies=[Depends(require_role("quality_supervisor"))])
app.include_router(reports.router, prefix="/api", dependencies=[Depends(require_role("quality_supervisor"))])
app.include_router(security.router, prefix="/api", dependencies=[Depends(require_role("quality_supervisor"))])
app.include_router(admin.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
