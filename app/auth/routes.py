
from urllib.parse import urljoin, urlparse

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from app.activity_logger import log_activity
from app.extensions import mysql
from app.auth import auth_bp
from app.auth.decorators import get_current_user, get_user_roles, login_required
from app.patients.registration import (
    RegistrationError,
    create_patient_account,
)


def fetch_user_by_username(username):
    """Find an account using its username or registered email."""
    cursor = None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """
            SELECT
                user_id,
                username,
                password_hash,
                email,
                is_active,
                patient_id,
                profile_photo
            FROM users
            WHERE username = %s OR email = %s
            LIMIT 1
            """,
            (username, username),
        )
        return cursor.fetchone()

    finally:
        if cursor is not None:
            cursor.close()


def user_has_assigned_role(user_id):
    """Verify that the account has an assigned RBAC role."""
    cursor = None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM user_roles ur
            INNER JOIN roles r ON r.role_id = ur.role_id
            WHERE ur.user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        return cursor.fetchone() is not None

    finally:
        if cursor is not None:
            cursor.close()


def is_safe_redirect(target):
    """Allow redirects only to the current application host."""
    if not target:
        return False

    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))

    return (
        redirect_url.scheme in ("http", "https")
        and redirect_url.netloc == host_url.netloc
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate an active MEDIVERA account."""

    if session.get("user_id"):
        existing_user = get_current_user()

        if existing_user:
            return redirect(url_for("dashboard"))

        session.clear()

    if request.method == "GET":
        return render_template(
            "auth/login.html",
            next=request.args.get("next", ""),
        )

    login_identifier = (
        request.form.get("username") or ""
    ).strip()

    password = request.form.get("password") or ""
    next_url = (request.form.get("next") or "").strip()

    if not login_identifier or not password:
        flash(
            "Please enter your username or email and password.",
            "error",
        )
        return render_template(
            "auth/login.html",
            next=next_url,
        ), 400

    try:
        user = fetch_user_by_username(login_identifier)

    except Exception:
        current_app.logger.exception("Database error during login.")
        flash(
            "The login service is temporarily unavailable. Please try again.",
            "error",
        )
        return render_template(
            "auth/login.html",
            next=next_url,
        ), 503

    if not user:
        flash("Invalid username/email or password.", "error")
        return render_template(
            "auth/login.html",
            next=next_url,
        ), 401

    if not user.get("is_active"):
        flash("This account is inactive. Please contact the administrator.", "error")
        return render_template(
            "auth/login.html",
            next=next_url,
        ), 401

    try:
        password_valid = check_password_hash(
            user["password_hash"],
            password,
        )
    except (TypeError, ValueError):
        current_app.logger.warning(
            "Invalid password hash for user_id=%s.",
            user["user_id"],
        )
        password_valid = False

    if not password_valid:
        current_app.logger.warning(
            "Invalid login credentials for user_id=%s.",
            user["user_id"],
        )
        flash("Invalid username/email or password.", "error")
        return render_template(
            "auth/login.html",
            next=next_url,
        ), 401

    try:
        if not user_has_assigned_role(user["user_id"]):
            current_app.logger.warning(
                "Login blocked: user_id=%s has no RBAC role.",
                user["user_id"],
            )
            flash(
                "Your account does not have an assigned access role. "
                "Please contact the administrator.",
                "error",
            )
            return render_template(
                "auth/login.html",
                next=next_url,
            ), 403

    except Exception:
        current_app.logger.exception(
            "Role verification failed during login."
        )
        flash(
            "The login service is temporarily unavailable. Please try again.",
            "error",
        )
        return render_template(
            "auth/login.html",
            next=next_url,
        ), 503

    session.clear()
    session["user_id"] = user["user_id"]
    session["username"] = user["username"]
    session.permanent = True

    try:
        log_activity(
            action="User Login",
            module="Authentication",
            description=(
                f"User '{user['username']}' logged in successfully."
            ),
            user_id=user["user_id"],
        )
    except Exception:
        current_app.logger.exception(
            "Could not record login activity for user_id=%s.",
            user["user_id"],
        )

    current_app.logger.info(
        "User authenticated successfully: user_id=%s",
        user["user_id"],
    )

    flash("Welcome back!", "success")

    if next_url and is_safe_redirect(next_url):
        return redirect(next_url)

    return redirect(url_for("dashboard"))


@auth_bp.get("/profile")
@login_required
def profile():
    """Display the signed-in user's own account information."""
    user = get_current_user()

    if not user:
        return redirect(url_for("auth.login"))

    roles = get_user_roles(user["user_id"])

    return render_template(
        "auth/profile.html",
        current_user=user,
        roles=roles,
    )


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """End the current authenticated session."""
    user_id = session.get("user_id")
    username = session.get("username", "Unknown")

    if user_id:
        try:
            log_activity(
                action="User Logout",
                module="Authentication",
                description=(
                    f"User '{username}' logged out successfully."
                ),
                user_id=user_id,
            )
        except Exception:
            current_app.logger.exception(
                "Could not record logout activity."
            )

        current_app.logger.info(
            "User logged out: user_id=%s",
            user_id,
        )

    session.clear()
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Register a patient account without email verification."""
    if request.method == "POST":
        try:
            account = create_patient_account(
                request.form,
                request.files,
            )

            current_app.logger.info(
                "Patient account created: user_id=%s",
                account["user_id"],
            )

            flash(
                "Your MEDIVERA account has been created successfully. "
                "You can now sign in using your username or email address.",
                "success",
            )
            return redirect(url_for("auth.login"))

        except RegistrationError as error:
            flash(str(error), "error")
            return render_template(
                "auth/register.html"
            ), 400

    return render_template("auth/register.html")
