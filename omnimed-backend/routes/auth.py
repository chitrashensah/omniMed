# JWT auth — verifies the Supabase access token on protected routes.
# No rate limiting here (planned separately); this only authenticates.

from functools import wraps
from flask import request, jsonify, g
from services import supabase_service


def _extract_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def require_auth(fn):
    """Reject requests without a valid Supabase access token. Sets g.user."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return ("", 204)
        user = supabase_service.verify_token(_extract_token())
        if not user:
            return jsonify({
                "error": "UNAUTHORIZED",
                "message": "A valid Supabase access token is required.",
            }), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def current_user() -> dict | None:
    """The authenticated user ({'id','email'}) for the current request, if any."""
    return getattr(g, "user", None)
