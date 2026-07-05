import os
from flask import Blueprint, request, jsonify, g
from routes.auth import require_auth
from services import supabase_service

admin_bp = Blueprint("admin", __name__)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "chitrashenshah@gmail.com")


def _require_admin():
    user = getattr(g, "user", None)
    if not user or user.get("email") != ADMIN_EMAIL:
        return jsonify({"error": "FORBIDDEN"}), 403
    return None


@admin_bp.route("/admin/granted-users", methods=["GET"])
@require_auth
def get_granted_users():
    err = _require_admin()
    if err: return err
    return jsonify({"users": supabase_service.get_granted_users()}), 200


@admin_bp.route("/admin/granted-users", methods=["POST"])
@require_auth
def grant_user():
    err = _require_admin()
    if err: return err
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "").strip()
    email   = data.get("email", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    row = supabase_service.grant_user_access(user_id, email)
    return jsonify({"user": row}), 201


@admin_bp.route("/admin/granted-users/<user_id>", methods=["DELETE"])
@require_auth
def revoke_user(user_id):
    err = _require_admin()
    if err: return err
    supabase_service.revoke_user_access(user_id)
    # Clear from in-memory cache too
    try:
        from routes.chat import _quota_cache
        _quota_cache.pop(user_id, None)
    except Exception:
        pass
    return jsonify({"status": "revoked"}), 200
