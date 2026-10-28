"""Seed one demo account per role. Run once after the database is created.

Credentials are for the review demo only -- see docs/demo_script.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.auth import ROLES, hash_password  # noqa: E402
from backend.app.db import connect  # noqa: E402

DEMO_PASSWORD = "ColdSense#2025"

DEMO_USERS = [
    ("admin@coldsense.lk", "Nadeesha Perera", "admin"),
    ("ops.manager@coldsense.lk", "Ruwan Fernando", "operations_manager"),
    ("supervisor@coldsense.lk", "Ishara Silva", "quality_supervisor"),
    ("staff@coldsense.lk", "Kasun Jayawardena", "staff"),
]


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        for email, name, role in DEMO_USERS:
            cur.execute(
                """
                INSERT INTO app_user (email, password_hash, full_name, role, role_level)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash, full_name = EXCLUDED.full_name,
                        role = EXCLUDED.role, role_level = EXCLUDED.role_level, is_active = TRUE
                """,
                (email, hash_password(DEMO_PASSWORD), name, role, ROLES.index(role)),
            )
        conn.commit()
    print(f"Seeded {len(DEMO_USERS)} demo users. Password for all: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
