"""Secrets the API cannot run safely without.

Both the JWT signing key and the device key used to fall back to a hardcoded value when the
environment variable was missing. Those fallbacks were committed to the repository, so any
deployment that forgot to set them signed tokens with a string anyone could read - enough to
forge an administrator token. A missing or placeholder secret is now a startup error: failing
loudly is better than running with a key that is public.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Values that are obviously not real secrets. A key that is short or still contains a
# template marker is treated the same as a missing one.
_PLACEHOLDER_MARKERS = ("change-me", "change_me", "changeme", "dev-only", "your-", "example")
MIN_SECRET_LENGTH = 32


class MissingSecretError(RuntimeError):
    pass


def require_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSecretError(
            f"{name} is not set. Generate one with:\n"
            f'  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            f"and put it in .env (see .env.example)."
        )
    if len(value) < MIN_SECRET_LENGTH or any(m in value.lower() for m in _PLACEHOLDER_MARKERS):
        raise MissingSecretError(
            f"{name} looks like a placeholder or is shorter than {MIN_SECRET_LENGTH} characters. "
            f"Replace it with a generated secret (see .env.example)."
        )
    return value
