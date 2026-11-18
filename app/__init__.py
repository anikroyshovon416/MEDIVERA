
import logging
import os
from datetime import datetime

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    session,
    url_for,
)

from app.config import Config
from app.system_settings import load_persistent_settings
from app.extensions import csrf, mysql
from app.auth import auth_bp
from app.patients import patient_bp
from app.admin import admin_bp
from app.laboratory import laboratory_bp
from app.pharmacy import pharmacy_bp

from app.auth.decorators import (
    get_current_user,
    get_user_roles,
    login_required,
)

from app.admin import routes
from app.admin import emergency_routes
from app.admin import emergency_approval_routes

def create_app(config_class=Config):
    """Create the MEDIVERA Flask application."""

    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )

    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
        static_folder=os.path.join(project_root, "static"),
        static_url_path="/static",
    )

    app.config.from_object(config_class)

    # Production must use a separately provisioned cloud database.
    if app.config.get("IS_PRODUCTION"):
        required_database_settings = (
            "MYSQL_HOST",
            "MYSQL_USER",
            "MYSQL_PASSWORD",
            "MYSQL_DB",
        )
        missing_database_settings = [
            name
            for name in required_database_settings
            if not os.getenv(name, "").strip()
        ]

        if missing_database_settings:
            raise RuntimeError(
                "Production database configuration is incomplete. "
                "Set the following environment variables: "
                + ", ".join(missing_database_settings)
            )

        database_host = str(
            app.config.get("MYSQL_HOST", "")
        ).strip().lower()

        if database_host in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError(
                "Production MYSQL_HOST must point to the separate "
                "cloud database, not a local loopback address."
            )

        # Load Super Admin-managed settings from the private instance folder.
    load_persistent_settings(app)

    logging.basicConfig(level=logging.INFO)
    app.logger.setLevel(logging.INFO)

    # Configure mandatory TiDB Cloud TLS before MySQL initialization.
    if app.config.get("IS_PRODUCTION"):
        ca_path = os.getenv(
            "MYSQL_SSL_CA",
            "/etc/secrets/isrgrootx1.pem"
        ).strip()

        if not os.path.isfile(ca_path):
            raise RuntimeError(
                "TiDB TLS certificate is missing at: " + ca_path
            )

        app.config["MYSQL_SSL_CA"] = ca_path
        app.config["MYSQL_CUSTOM_OPTIONS"] = {
            "ssl": {"ca": ca_path},
            "ssl_mode": "VERIFY_IDENTITY",
        }
    # Initialize extensions.
    mysql.init_app(app)
    csrf.init_app(app)

    # Register application blueprints.
    app.register_blueprint(auth_bp)
    app.register_blueprint(patient_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(laboratory_bp)
    app.register_blueprint(pharmacy_bp)

    # --------------------------------------------------
    # TEMPLATE CONTEXT
    # --------------------------------------------------

    @app.context_processor
    def inject_shell_context():
        return {
            "current_user": get_current_user(),
            "app_name": app.config.get(
                "APP_NAME",
                "MEDIVERA",
            ),
            "app_tagline": app.config.get(
                "APP_TAGLINE",
                "Intelligent Healthcare. Connected Care. Better Tomorrow.",
            ),
        }

    # --------------------------------------------------
    # SECURITY HEADERS
    # --------------------------------------------------

    @app.after_request
    def add_security_headers(response):

        response.headers.setdefault(
            "X-Content-Type-Options",
            "nosniff",
        )

        response.headers.setdefault(
            "X-Frame-Options",
            "SAMEORIGIN",
        )

        response.headers.setdefault(
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        )

        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )

        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response

    # --------------------------------------------------
    # HOME PAGE
    # --------------------------------------------------

    @app.get("/")
    def home():

        if session.get("user_id"):
            return redirect(url_for("dashboard"))

        return redirect(url_for("auth.login"))

    # --------------------------------------------------
    # HEALTH CHECK
    # --------------------------------------------------

    @app.get("/health")
    def health():

        return jsonify({
            "status": "ok",
            "application": "MEDIVERA",
        })

    # --------------------------------------------------
    # DATABASE HEALTH CHECK
    # --------------------------------------------------

    @app.get("/health/database")
    def database_health():
        cursor = None
        try:
            cursor = mysql.connection.cursor()
            cursor.execute("SELECT 1 AS database_ok")
            row = cursor.fetchone()

            return jsonify({
                "status": "ok" if row else "error",
                "database": "connected" if row else "unavailable",
            }), 200 if row else 503

        except Exception as exc:
            app.logger.exception("Database health check failed.")
            return jsonify({
                "status": "error",
                "database": "unavailable",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "production_config": bool(app.config.get("IS_PRODUCTION")),
                "ssl_mode": (app.config.get("MYSQL_CUSTOM_OPTIONS") or {}).get("ssl_mode"),
                "ssl_options_present": bool((app.config.get("MYSQL_CUSTOM_OPTIONS") or {}).get("ssl")),
                "ca_file_exists": os.path.isfile(app.config.get("MYSQL_SSL_CA", "")),
            }), 503

        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    app.logger.exception("Database cursor cleanup failed.")

    # --------------------------------------------------
    # DASHBOARD DISPATCHER
    # --------------------------------------------------

    @app.get("/dashboard")
    @login_required
    def dashboard():
        """Redirect users to their appropriate dashboard."""

        roles = set(get_user_roles())

        # Super Admin has the system-wide dashboard.
        if "super_admin" in roles:
            return redirect(
                url_for("admin.super_dashboard")
            )

        # Patient-only accounts use the patient portal.
        if roles == {"patient"}:
            return redirect(
                url_for("patient.dashboard")
            )

        # Meal administrators use the existing meal dashboard.
        # Its route independently validates role, assignment and permission.
        if roles.intersection({
            "food_admin",
            "meal_delivery_admin",
        }):
            return redirect(
                url_for("admin.meals_dashboard")
            )

        # These department dashboards are not yet registered.
        # Do not send these administrators to the Super Admin dashboard
        # or expose the general hospital overview.
        if roles.intersection({
            "hr_admin",
            "attendance_admin",
            "patient_services_admin",
            "equipment_admin",
        }):
            return render_template(
                "errors/403.html"
            ), 403

        # Other staff users use the general hospital dashboard.
        cursor = None

        try:

            cursor = mysql.connection.cursor()

            # ------------------------------------------
            # HOSPITAL OVERVIEW METRICS
            # ------------------------------------------

            cursor.execute("""
                SELECT
                    (SELECT COUNT(*)
                     FROM patients) AS total_patients,

                    (SELECT COUNT(*)
                     FROM doctors) AS total_doctors,

                    (SELECT COUNT(*)
                     FROM appointments) AS total_appointments,

                    (SELECT COUNT(*)
                     FROM appointments
                     WHERE status = 'pending_approval')
                     AS pending_appointments,

                    (SELECT COUNT(*)
                     FROM appointments
                     WHERE appointment_date >= CURDATE()
                     AND status IN (
                         'scheduled',
                         'confirmed',
                         'pending_approval'
                     )) AS upcoming_appointments,

                    (SELECT COUNT(*)
                     FROM admissions
                     WHERE admission_status = 'admitted')
                     AS active_admissions,

                    (SELECT COUNT(*)
                     FROM beds
                     WHERE bed_status = 'available')
                     AS available_beds,

                    (SELECT COUNT(*)
                     FROM billing
                     WHERE payment_status = 'pending')
                     AS pending_bills,

                    (SELECT COALESCE(SUM(payment_amount), 0)
                     FROM payments
                     WHERE payment_status = 'successful')
                     AS collected_revenue,

                    (SELECT COUNT(*)
                     FROM inventory
                     WHERE stock_status IN (
                         'low_stock',
                         'out_of_stock'
                     )
                     OR (
                         reorder_level IS NOT NULL
                         AND quantity <= reorder_level
                     )) AS inventory_alerts
            """)

            metrics = cursor.fetchone()

            # ------------------------------------------
            # RECENT APPOINTMENTS
            # ------------------------------------------

            cursor.execute("""
                SELECT
                    a.appointment_id,
                    a.appointment_date,

                    DATE_FORMAT(
                        a.appointment_time,
                        '%h:%i %p'
                    ) AS appointment_time_display,

                    a.status,

                    CONCAT(
                        p.first_name,
                        ' ',
                        p.last_name
                    ) AS patient_name,

                    d.doctor_name

                FROM appointments AS a

                INNER JOIN patients AS p
                    ON p.patient_id = a.patient_id

                INNER JOIN doctors AS d
                    ON d.doctor_id = a.doctor_id

                ORDER BY
                    a.appointment_date DESC,
                    a.appointment_time DESC,
                    a.appointment_id DESC

                LIMIT 5
            """)

            recent_appointments = cursor.fetchall()

        finally:

            if cursor is not None:
                cursor.close()

        return render_template(
            "dashboard.html",

            dashboard_date=datetime.now().strftime(
                "%A, %d %B %Y"
            ),

            metrics=metrics,
            recent_appointments=recent_appointments,
        )

    # --------------------------------------------------
    # ERROR HANDLERS
    # --------------------------------------------------

    @app.errorhandler(403)
    def forbidden(error):

        return render_template(
            "errors/403.html"
        ), 403

    @app.errorhandler(404)
    def not_found(error):

        return render_template(
            "errors/404.html"
        ), 404

    @app.errorhandler(500)
    def internal_error(error):

        app.logger.exception(
            "Unhandled application error."
        )

        return render_template(
            "errors/500.html"
        ), 500

    return app



