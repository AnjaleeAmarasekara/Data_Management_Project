"""Audit trail: who did what, when. Called from the endpoints that change state or
represent a notable human action (not from read-only GETs, which would drown the signal).
"""


def log_action(conn, actor, action: str, target: str | None = None, detail: str | None = None) -> None:
    """actor is a backend.app.auth.User, or an email string for pre-auth events such as a
    failed login where no authenticated user exists yet."""
    actor_id = getattr(actor, "id", None)
    actor_email = getattr(actor, "email", actor if isinstance(actor, str) else None)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (actor_id, actor_email, action, target, detail) VALUES (%s, %s, %s, %s, %s)",
            (actor_id, actor_email, action, target, detail),
        )
    conn.commit()
