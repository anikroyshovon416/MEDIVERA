
from functools import wraps

from flask import (
    abort,
    current_app,
    g,
    redirect,
    request,
    session,
    url_for,
)

from app.extensions import mysql


def _fetch_one(query, params=()):
    cursor = None
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()


def _fetch_all(query, params=()):
    cursor = None
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()


def get_current_user():
    """Return the currently authenticated, active user."""
    if hasattr(g, "_current_user"):
        return g._current_user

    user_id = session.get("user_id")

    if not user_id:
        g._current_user = None
        return None

    try:
        user = _fetch_one(
            """
            SELECT
                user_id,
                username,
                email,
                is_active,
                patient_id,
                profile_photo
            FROM users
            WHERE user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )
    except Exception:
        current_app.logger.exception(
            "Unable to load the authenticated user."
        )
        abort(503, description="Authentication service unavailable.")

    if not user or not user.get("is_active"):
        session.clear()
        g._current_user = None
        return None

    g._current_user = user
    return user


def get_user_roles(user_id=None):
    """Return authoritative role names from user_roles and roles."""
    if user_id is None:
        user = get_current_user()
        if not user:
            return []
        user_id = user["user_id"]

    cache_key = f"_user_roles_{user_id}"

    if hasattr(g, cache_key):
        return getattr(g, cache_key)

    try:
        rows = _fetch_all(
            """
            SELECT DISTINCT r.role_name
            FROM user_roles ur
            INNER JOIN roles r ON r.role_id = ur.role_id
            WHERE ur.user_id = %s
            ORDER BY r.role_name
            """,
            (user_id,),
        )

        roles = [
            row["role_name"]
            for row in rows
            if row.get("role_name")
        ]

        setattr(g, cache_key, roles)
        return roles

    except Exception:
        current_app.logger.exception(
            "Unable to load user roles for user_id=%s.",
            user_id,
        )
        abort(503, description="Authorization service unavailable.")


def has_role(*required_roles):
    user = get_current_user()

    if not user:
        return False

    return bool(
        set(get_user_roles(user["user_id"])).intersection(required_roles)
    )


def has_permission(permission_key):
    user = get_current_user()

    if not user:
        return False

    if has_role("super_admin"):
        return True

    try:
        result = _fetch_one(
            """
            SELECT 1 AS permitted
            FROM user_roles ur
            INNER JOIN role_permissions rp
                ON rp.role_id = ur.role_id
            INNER JOIN permissions p
                ON p.permission_id = rp.permission_id
            WHERE ur.user_id = %s
              AND p.permission_key = %s
            LIMIT 1
            """,
            (user["user_id"], permission_key),
        )
        return result is not None

    except Exception:
        current_app.logger.exception(
            "Permission lookup failed for user_id=%s, permission=%s.",
            user["user_id"],
            permission_key,
        )
        abort(503, description="Authorization service unavailable.")


def has_admin_assignment(*responsibilities):
    user = get_current_user()

    if not user:
        return False

    if has_role("super_admin"):
        return True

    if not responsibilities:
        return False

    placeholders = ", ".join(["%s"] * len(responsibilities))

    query = f"""
        SELECT 1 AS assigned
        FROM admin_assignments
        WHERE admin_user_id = %s
          AND is_active = 1
          AND responsibility IN ({placeholders})
        LIMIT 1
    """

    try:
        result = _fetch_one(
            query,
            (user["user_id"], *responsibilities),
        )
        return result is not None

    except Exception:
        current_app.logger.exception(
            "Admin assignment lookup failed for user_id=%s.",
            user["user_id"],
        )
        abort(503, description="Authorization service unavailable.")


def login_required(view):
    """Restrict a route to authenticated, active users."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = get_current_user()

        if not user:
            session.clear()
            return redirect(
                url_for("auth.login", next=request.full_path)
            )

        return view(*args, **kwargs)

    return wrapped_view


def role_required(*required_roles):
    if not required_roles:
        raise ValueError("role_required requires at least one role.")

    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()

            if not user:
                return redirect(
                    url_for("auth.login", next=request.full_path)
                )

            if not has_role(*required_roles):
                abort(403)

            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def permission_required(*permission_keys):
    if not permission_keys:
        raise ValueError(
            "permission_required requires at least one permission."
        )

    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()

            if not user:
                return redirect(
                    url_for("auth.login", next=request.full_path)
                )

            for permission_key in permission_keys:
                if not has_permission(permission_key):
                    abort(403)

            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def admin_assignment_required(*responsibilities):
    if not responsibilities:
        raise ValueError(
            "admin_assignment_required requires at least one responsibility."
        )

    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()

            if not user:
                return redirect(
                    url_for("auth.login", next=request.full_path)
                )

            if not has_admin_assignment(*responsibilities):
                abort(403)

            return view(*args, **kwargs)

        return wrapped_view

    return decorator
