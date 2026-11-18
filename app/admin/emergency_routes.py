
"""
MEDIVERA - Emergency & Ambulance Management
Super Admin-only operational module.

Uses the existing MySQL tables:
    ambulances
    emergency_cases
    ambulance_dispatches
    patients
    doctors
    staff
    users

Does not modify the existing database schema.
"""

import logging
import re
from datetime import datetime

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.admin import admin_bp
from app.auth.decorators import (
    get_current_user,
    get_user_roles,
    login_required,
)
from app.extensions import mysql
from app.activity_logger import log_activity


logger = logging.getLogger(__name__)


# ============================================================
# AUTHORIZATION
# ============================================================

def _emergency_current_user():
    """Return the authenticated user or None."""

    return get_current_user()


def _require_emergency_admin():
    """
    Emergency and ambulance management are restricted to
    the Super Admin.

    Functional administrators cannot access these endpoints
    merely because they have another administrative role.
    """

    user = _emergency_current_user()

    if not user:
        abort(403)

    user_id = user.get("user_id")

    if not user_id:
        abort(403)

    roles = get_user_roles(user_id)

    if "super_admin" not in roles:
        abort(403)

    return user


# ============================================================
# DATABASE HELPERS
# ============================================================

def emergency_fetch_all(query, params=()):
    cursor = None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    finally:
        if cursor is not None:
            cursor.close()


def emergency_fetch_one(query, params=()):
    cursor = None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()

    finally:
        if cursor is not None:
            cursor.close()


def _emergency_nav_items():
    """Navigation shared by all emergency module pages."""

    return [
        {
    "label": "Emergency Staffing",
    "endpoint": "admin.emergency_staffing",
    "icon": "users",
        },
        {
            "label": "System Overview",
            "icon": "layout-dashboard",
            "endpoint": "admin.super_dashboard",
        },
        {
            "label": "Administrators",
            "icon": "users",
            "endpoint": "admin.administrators",
        },
        {
            "label": "Roles & Permissions",
            "icon": "shield-check",
            "endpoint": "admin.roles_permissions",
        },
        {
            "label": "Approvals",
            "icon": "clipboard-check",
            "endpoint": "admin.approvals",
        },
        {
            "label": "Reports",
            "icon": "chart-no-axes-combined",
            "endpoint": "admin.reports",
        },
        {
            "label": "Activity Logs",
            "icon": "history",
            "endpoint": "admin.activity_logs",
        },
        {
            "label": "System Settings",
            "icon": "settings",
            "endpoint": "admin.system_settings",
        },
        {
            "label": "Emergency Dashboard",
            "icon": "emergency",
            "endpoint": "admin.emergency_dashboard",
        },
        {
            "label": "Emergency Cases",
            "icon": "alert-circle",
            "endpoint": "admin.emergency_cases",
        },
        {
            "label": "Ambulance Management",
            "icon": "ambulance",
            "endpoint": "admin.ambulances",
        },
        {
            "label": "Ambulance Dispatches",
            "icon": "truck",
            "endpoint": "admin.ambulance_dispatches",
        },
    ]



def _render_emergency(template, **context):
    """Render a page with the standard Super Admin layout."""

    user = _require_emergency_admin()
    context.pop("user", None)

    return render_template(
        template,
        user=user,
        nav_items=_emergency_nav_items(),
        is_super_admin=True,
        **context,
    )



def _record_emergency_activity(action, description, user):
    """Record an emergency-module activity without breaking the operation."""

    try:
        log_activity(
            action=action,
            module="Emergency Management",
            description=description,
            user_id=user.get("user_id"),
        )
    except Exception:
        current_app.logger.exception(
            "Emergency activity logging failed."
        )


def _clean(value, maximum=255):
    return str(value or "").strip()[:maximum]


# ============================================================
# EMERGENCY DASHBOARD
# ============================================================

@admin_bp.get("/emergency")
@login_required
def emergency_dashboard():

    _require_emergency_admin()

    metrics = emergency_fetch_one(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM emergency_cases
            ) AS total_cases,

            (
                SELECT COUNT(*)
                FROM emergency_cases
                WHERE severity = 'Critical'
                  AND case_status NOT IN (
                      'Resolved',
                      'Cancelled'
                  )
            ) AS critical_cases,

            (
                SELECT COUNT(*)
                FROM emergency_cases
                WHERE case_status IN (
                    'Reported',
                    'Triaged',
                    'In Progress'
                )
            ) AS active_cases,

            (
                SELECT COUNT(*)
                FROM ambulances
            ) AS total_ambulances,

            (
                SELECT COUNT(*)
                FROM ambulances
                WHERE ambulance_status = 'available'
            ) AS available_ambulances,

            (
                SELECT COUNT(*)
                FROM ambulances
                WHERE ambulance_status = 'dispatched'
            ) AS dispatched_ambulances,

            (
                SELECT COUNT(*)
                FROM ambulances
                WHERE ambulance_status IN (
                    'maintenance',
                    'out_of_service'
                )
            ) AS unavailable_ambulances,

            (
                SELECT COUNT(*)
                FROM ambulance_dispatches
                WHERE dispatch_status NOT IN (
                    'Completed',
                    'Cancelled'
                )
            ) AS active_dispatches
        """
    )

    recent_cases = emergency_fetch_all(
        """
        SELECT
            ec.emergency_case_id,
            ec.case_number,
            ec.emergency_type,
            ec.severity,
            ec.case_status,
            ec.emergency_location,
            ec.reported_at,

            p.first_name,
            p.last_name

        FROM emergency_cases ec

        LEFT JOIN patients p
            ON p.patient_id = ec.patient_id

        ORDER BY ec.reported_at DESC
        LIMIT 8
        """
    )

    recent_dispatches = emergency_fetch_all(
        """
        SELECT
            ad.dispatch_id,
            ad.dispatch_status,
            ad.dispatch_time,

            a.ambulance_code,
            a.registration_number,

            ec.case_number

        FROM ambulance_dispatches ad

        INNER JOIN ambulances a
            ON a.ambulance_id = ad.ambulance_id

        INNER JOIN emergency_cases ec
            ON ec.emergency_case_id = ad.emergency_case_id

        ORDER BY ad.created_at DESC
        LIMIT 8
        """
    )

    return _render_emergency(
        "admin/emergency_dashboard.html",
        metrics=metrics,
        recent_cases=recent_cases,
        recent_dispatches=recent_dispatches,
        breadcrumb="Emergency Management",
    )


# ============================================================
# EMERGENCY CASES
# ============================================================

@admin_bp.get("/emergency/cases")
@login_required
def emergency_cases():

    _require_emergency_admin()

    search = _clean(request.args.get("search"), 100)
    status_filter = _clean(request.args.get("status"), 30)
    severity_filter = _clean(request.args.get("severity"), 20)

    conditions = []
    params = []

    if search:
        search_pattern = f"%{search}%"

        conditions.append(
            """
            (
                ec.case_number LIKE %s
                OR ec.emergency_type LIKE %s
                OR ec.emergency_location LIKE %s
                OR p.first_name LIKE %s
                OR p.last_name LIKE %s
            )
            """
        )

        params.extend([search_pattern] * 5)

    allowed_statuses = {
        "Reported",
        "Triaged",
        "In Progress",
        "Resolved",
        "Cancelled",
    }

    allowed_severities = {
        "Low",
        "Medium",
        "High",
        "Critical",
    }

    if status_filter in allowed_statuses:
        conditions.append("ec.case_status = %s")
        params.append(status_filter)

    if severity_filter in allowed_severities:
        conditions.append("ec.severity = %s")
        params.append(severity_filter)

    where_sql = (
        "WHERE " + " AND ".join(conditions)
        if conditions else ""
    )

    cases = emergency_fetch_all(
        f"""
        SELECT
            ec.*,

            p.first_name,
            p.last_name,
            p.phone AS patient_phone,

            d.doctor_name,

            s.staff_name AS assigned_staff_name

        FROM emergency_cases ec

        LEFT JOIN patients p
            ON p.patient_id = ec.patient_id

        LEFT JOIN doctors d
            ON d.doctor_id = ec.assigned_doctor_id

        LEFT JOIN staff s
            ON s.staff_id = ec.assigned_staff_id

        {where_sql}

        ORDER BY
            CASE ec.severity
                WHEN 'Critical' THEN 1
                WHEN 'High' THEN 2
                WHEN 'Medium' THEN 3
                ELSE 4
            END,
            ec.reported_at DESC

        LIMIT 300
        """,
        tuple(params),
    )

    patients = emergency_fetch_all(
        """
        SELECT
            patient_id,
            first_name,
            last_name,
            phone
        FROM patients
        ORDER BY first_name, last_name
        LIMIT 1000
        """
    )

    doctors = emergency_fetch_all(
        """
        SELECT
            doctor_id,
            doctor_name,
            specialization
        FROM doctors
        ORDER BY doctor_name
        LIMIT 500
        """
    )

    staff_members = emergency_fetch_all(
        """
        SELECT
            staff_id,
            staff_name,
            designation
        FROM staff
        WHERE employment_status = 'active'
        ORDER BY staff_name
        LIMIT 1000
        """
    )

    return _render_emergency(
        "admin/emergency_cases.html",
        cases=cases,
        patients=patients,
        doctors=doctors,
        staff_members=staff_members,
        search=search,
        status_filter=status_filter,
        severity_filter=severity_filter,
        breadcrumb="Emergency Cases",
    )


@admin_bp.post("/emergency/cases/create")
@login_required
def create_emergency_case():

    user = _require_emergency_admin()

    case_number = _clean(
        request.form.get("case_number"),
        40,
    )

    emergency_type = _clean(
        request.form.get("emergency_type"),
        100,
    )

    severity = _clean(
        request.form.get("severity"),
        20,
    )

    description = _clean(
        request.form.get("case_description"),
        5000,
    )

    location = _clean(
        request.form.get("emergency_location"),
        255,
    )

    contact_person = _clean(
        request.form.get("contact_person"),
        100,
    )

    contact_phone = _clean(
        request.form.get("contact_phone"),
        20,
    )

    allowed_severities = {
        "Low",
        "Medium",
        "High",
        "Critical",
    }

    if not case_number or not emergency_type:
        flash(
            "Case number and emergency type are required.",
            "danger",
        )
        return redirect(url_for("admin.emergency_cases"))

    if severity not in allowed_severities:
        flash("Invalid emergency severity.", "danger")
        return redirect(url_for("admin.emergency_cases"))

    patient_id = request.form.get("patient_id") or None
    doctor_id = request.form.get("assigned_doctor_id") or None
    staff_id = request.form.get("assigned_staff_id") or None

    try:
        patient_id = int(patient_id) if patient_id else None
        doctor_id = int(doctor_id) if doctor_id else None
        staff_id = int(staff_id) if staff_id else None

        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            INSERT INTO emergency_cases (
                case_number,
                patient_id,
                emergency_type,
                severity,
                case_description,
                emergency_location,
                contact_person,
                contact_phone,
                assigned_doctor_id,
                assigned_staff_id,
                reported_by_user_id,
                case_status,
                reported_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, 'Reported', NOW()
            )
            """,
            (
                case_number,
                patient_id,
                emergency_type,
                severity,
                description,
                location,
                contact_person,
                contact_phone,
                doctor_id,
                staff_id,
                user["user_id"],
            ),
        )

        mysql.connection.commit()
        cursor.close()

        _record_emergency_activity(
            "Emergency Case Created",
            f"Created emergency case {case_number}.",
            user,
        )

        flash(
            f"Emergency case {case_number} registered successfully.",
            "success",
        )

    except Exception:
        try:
            mysql.connection.rollback()
        except Exception:
            logger.exception("Emergency case rollback failed.")

        logger.exception("Emergency case creation failed.")

        flash(
            "Unable to create the emergency case. "
            "Check the case number and selected records.",
            "danger",
        )

    return redirect(url_for("admin.emergency_cases"))


@admin_bp.post("/emergency/cases/<int:case_id>/status")
@login_required
def update_emergency_case_status(case_id):

    user = _require_emergency_admin()

    new_status = _clean(
        request.form.get("case_status"),
        30,
    )

    allowed_statuses = {
        "Reported",
        "Triaged",
        "In Progress",
        "Resolved",
        "Cancelled",
    }

    if new_status not in allowed_statuses:
        flash("Invalid emergency case status.", "danger")
        return redirect(url_for("admin.emergency_cases"))

    notes = _clean(
        request.form.get("resolution_notes"),
        5000,
    )

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            UPDATE emergency_cases
            SET
                case_status = %s,
                resolved_at = CASE
                    WHEN %s IN ('Resolved', 'Cancelled')
                    THEN NOW()
                    ELSE NULL
                END,
                resolution_notes = %s
            WHERE emergency_case_id = %s
            """,
            (
                new_status,
                new_status,
                notes or None,
                case_id,
            ),
        )

        if cursor.rowcount == 0:
            mysql.connection.rollback()
            flash("Emergency case not found.", "warning")
            return redirect(url_for("admin.emergency_cases"))

        mysql.connection.commit()

        _record_emergency_activity(
            "Emergency Case Updated",
            f"Emergency case {case_id} changed to {new_status}.",
            user,
        )

        flash("Emergency case status updated.", "success")

    except Exception:
        mysql.connection.rollback()
        logger.exception("Emergency status update failed.")
        flash("Unable to update the emergency case.", "danger")

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.emergency_cases"))


# ============================================================
# AMBULANCE MANAGEMENT
# ============================================================

@admin_bp.get("/emergency/ambulances")
@login_required
def ambulances():

    _require_emergency_admin()

    ambulance_list = emergency_fetch_all(
        """
        SELECT
            a.*,

            (
                SELECT COUNT(*)
                FROM ambulance_dispatches ad
                WHERE ad.ambulance_id = a.ambulance_id
                  AND ad.dispatch_status NOT IN (
                      'Completed',
                      'Cancelled'
                  )
            ) AS active_dispatch_count

        FROM ambulances a

        ORDER BY
            CASE a.ambulance_status
                WHEN 'available' THEN 1
                WHEN 'dispatched' THEN 2
                WHEN 'maintenance' THEN 3
                ELSE 4
            END,
            a.ambulance_code
        """
    )

    return _render_emergency(
        "admin/ambulances.html",
        ambulances=ambulance_list,
        breadcrumb="Ambulance Management",
    )


@admin_bp.post("/emergency/ambulances/create")
@login_required
def create_ambulance():

    user = _require_emergency_admin()

    ambulance_code = _clean(
        request.form.get("ambulance_code"),
        30,
    )

    registration_number = _clean(
        request.form.get("registration_number"),
        30,
    ).upper()

    ambulance_type = _clean(
        request.form.get("ambulance_type"),
        50,
    )

    capacity_raw = request.form.get("capacity", "2")

    equipment_details = _clean(
        request.form.get("equipment_details"),
        5000,
    )

    if not ambulance_code or not registration_number:
        flash(
            "Ambulance code and registration number are required.",
            "danger",
        )
        return redirect(url_for("admin.ambulances"))

    try:
        capacity = int(capacity_raw)

        if capacity < 1 or capacity > 100:
            raise ValueError("Invalid capacity.")

        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            INSERT INTO ambulances (
                ambulance_code,
                registration_number,
                ambulance_type,
                capacity,
                ambulance_status,
                equipment_details
            )
            VALUES (%s, %s, %s, %s, 'available', %s)
            """,
            (
                ambulance_code,
                registration_number,
                ambulance_type or "Basic",
                capacity,
                equipment_details or None,
            ),
        )

        mysql.connection.commit()
        cursor.close()

        _record_emergency_activity(
            "Ambulance Registered",
            f"Registered ambulance {ambulance_code}.",
            user,
        )

        flash("Ambulance registered successfully.", "success")

    except ValueError:
        flash("Please enter a valid ambulance capacity.", "danger")

    except Exception:
        try:
            mysql.connection.rollback()
        except Exception:
            logger.exception("Ambulance rollback failed.")

        logger.exception("Ambulance registration failed.")

        flash(
            "Unable to register ambulance. "
            "Check whether its code or registration number already exists.",
            "danger",
        )

    return redirect(url_for("admin.ambulances"))


@admin_bp.post("/emergency/ambulances/<int:ambulance_id>/status")
@login_required
def update_ambulance_status(ambulance_id):

    user = _require_emergency_admin()

    new_status = _clean(
        request.form.get("ambulance_status"),
        30,
    )

    allowed_statuses = {
        "available",
        "maintenance",
        "out_of_service",
    }

    if new_status not in allowed_statuses:
        flash(
            "Invalid ambulance status. Active dispatches "
            "must be completed or cancelled first.",
            "danger",
        )
        return redirect(url_for("admin.ambulances"))

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            SELECT ambulance_status
            FROM ambulances
            WHERE ambulance_id = %s
            FOR UPDATE
            """,
            (ambulance_id,),
        )

        ambulance = cursor.fetchone()

        if not ambulance:
            mysql.connection.rollback()
            flash("Ambulance not found.", "warning")
            return redirect(url_for("admin.ambulances"))

        cursor.execute(
            """
            SELECT COUNT(*) AS active_count
            FROM ambulance_dispatches
            WHERE ambulance_id = %s
              AND dispatch_status NOT IN (
                  'Completed',
                  'Cancelled'
              )
            """,
            (ambulance_id,),
        )

        active = cursor.fetchone()

        if active["active_count"] > 0:
            mysql.connection.rollback()
            flash(
                "This ambulance has an active dispatch. "
                "Complete or cancel it before changing availability.",
                "warning",
            )
            return redirect(url_for("admin.ambulances"))

        cursor.execute(
            """
            UPDATE ambulances
            SET ambulance_status = %s
            WHERE ambulance_id = %s
            """,
            (new_status, ambulance_id),
        )

        mysql.connection.commit()

        _record_emergency_activity(
            "Ambulance Status Updated",
            f"Ambulance {ambulance_id} status changed to {new_status}.",
            user,
        )

        flash("Ambulance status updated.", "success")

    except Exception:
        mysql.connection.rollback()
        logger.exception("Ambulance status update failed.")
        flash("Unable to update ambulance status.", "danger")

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.ambulances"))


# ============================================================
# AMBULANCE DISPATCH MANAGEMENT
# ============================================================


@admin_bp.get("/emergency/dispatches")
@login_required
def ambulance_dispatches():

    _require_emergency_admin()

    # --------------------------------------------------------
    # SEARCH AND STATUS FILTERS
    # --------------------------------------------------------

    search = request.args.get("search", "").strip()[:100]
    status_filter = request.args.get("status", "").strip()

    allowed_statuses = {
        "Requested",
        "Dispatched",
        "Arrived",
        "Transporting",
        "Completed",
        "Cancelled",
    }

    if status_filter not in allowed_statuses:
        status_filter = ""

    conditions = []
    params = []

    if status_filter:
        conditions.append("ad.dispatch_status = %s")
        params.append(status_filter)

    if search:
        conditions.append("""
            (
                CAST(ad.dispatch_id AS CHAR) LIKE %s
                OR ec.case_number LIKE %s
                OR a.ambulance_code LIKE %s
                OR a.registration_number LIKE %s
                OR s.staff_name LIKE %s
                OR ad.pickup_location LIKE %s
                OR ad.destination LIKE %s
            )
        """)

        search_value = f"%{search}%"
        params.extend([search_value] * 7)

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions
        else ""
    )

    # --------------------------------------------------------
    # DISPATCH RECORDS
    # --------------------------------------------------------

    dispatches = emergency_fetch_all(
        f"""
        SELECT
            ad.*,

            a.ambulance_code,
            a.registration_number,
            a.ambulance_type,

            ec.case_number,
            ec.emergency_type,
            ec.severity,

            s.staff_name AS driver_name

        FROM ambulance_dispatches ad

        INNER JOIN ambulances a
            ON a.ambulance_id = ad.ambulance_id

        INNER JOIN emergency_cases ec
            ON ec.emergency_case_id = ad.emergency_case_id

        LEFT JOIN staff s
            ON s.staff_id = ad.driver_staff_id

        {where_clause}

        ORDER BY ad.created_at DESC
        LIMIT 500
        """,
        tuple(params),
    )

    # --------------------------------------------------------
    # AVAILABLE AMBULANCES
    # --------------------------------------------------------

    available_ambulances = emergency_fetch_all(
        """
        SELECT
            ambulance_id,
            ambulance_code,
            registration_number,
            ambulance_type

        FROM ambulances

        WHERE ambulance_status = 'available'

        ORDER BY ambulance_code
        """
    )

    # --------------------------------------------------------
    # ACTIVE EMERGENCY CASES
    # --------------------------------------------------------

    active_cases = emergency_fetch_all(
        """
        SELECT
            emergency_case_id,
            case_number,
            emergency_type,
            severity,
            emergency_location

        FROM emergency_cases

        WHERE case_status NOT IN ('Resolved', 'Cancelled')

        ORDER BY reported_at DESC
        """
    )

    # --------------------------------------------------------
    # ACTIVE STAFF / DRIVERS
    # --------------------------------------------------------

    drivers = emergency_fetch_all(
        """
        SELECT
            staff_id,
            staff_name,
            designation

        FROM staff

        WHERE employment_status = 'active'

        ORDER BY staff_name
        """
    )

    # --------------------------------------------------------
    # RENDER DISPATCH DASHBOARD
    # --------------------------------------------------------

    return _render_emergency(
        "admin/ambulance_dispatches.html",

        dispatches=dispatches,

        available_ambulances=available_ambulances,
        active_cases=active_cases,
        drivers=drivers,

        search=search,
        status_filter=status_filter,

        breadcrumb="Ambulance Dispatches",
    )


@admin_bp.post("/emergency/dispatches/create")
@login_required
def create_ambulance_dispatch():

    user = _require_emergency_admin()

    try:
        case_id = int(request.form.get("emergency_case_id", ""))
        ambulance_id = int(request.form.get("ambulance_id", ""))

        driver_raw = request.form.get("driver_staff_id")
        driver_id = int(driver_raw) if driver_raw else None

    except (TypeError, ValueError):
        flash("Please select a valid case and ambulance.", "danger")
        return redirect(url_for("admin.ambulance_dispatches"))

    pickup_location = _clean(
        request.form.get("pickup_location"),
        255,
    )

    destination = _clean(
        request.form.get("destination"),
        255,
    )

    notes = _clean(
        request.form.get("dispatch_notes"),
        5000,
    )

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        # Lock the ambulance to prevent simultaneous assignments.
        cursor.execute(
            """
            SELECT
                ambulance_id,
                ambulance_status
            FROM ambulances
            WHERE ambulance_id = %s
            FOR UPDATE
            """,
            (ambulance_id,),
        )

        ambulance = cursor.fetchone()

        if not ambulance:
            mysql.connection.rollback()
            flash("Selected ambulance does not exist.", "danger")
            return redirect(url_for("admin.ambulance_dispatches"))

        if ambulance["ambulance_status"] != "available":
            mysql.connection.rollback()
            flash("Selected ambulance is not available.", "warning")
            return redirect(url_for("admin.ambulance_dispatches"))

        cursor.execute(
            """
            SELECT
                emergency_case_id,
                case_status
            FROM emergency_cases
            WHERE emergency_case_id = %s
            FOR UPDATE
            """,
            (case_id,),
        )

        emergency_case = cursor.fetchone()

        if not emergency_case:
            mysql.connection.rollback()
            flash("Selected emergency case does not exist.", "danger")
            return redirect(url_for("admin.ambulance_dispatches"))

        if emergency_case["case_status"] in ("Resolved", "Cancelled"):
            mysql.connection.rollback()
            flash(
                "A resolved or cancelled case cannot receive a dispatch.",
                "warning",
            )
            return redirect(url_for("admin.ambulance_dispatches"))

        if driver_id is not None:
            cursor.execute(
                """
                SELECT staff_id
                FROM staff
                WHERE staff_id = %s
                  AND employment_status = 'active'
                FOR UPDATE
                """,
                (driver_id,),
            )

            if not cursor.fetchone():
                mysql.connection.rollback()
                flash("Selected driver is not an active staff member.", "warning")
                return redirect(url_for("admin.ambulance_dispatches"))

        cursor.execute(
            """
            INSERT INTO ambulance_dispatches (
                emergency_case_id,
                ambulance_id,
                driver_staff_id,
                dispatched_by_user_id,
                dispatch_status,
                pickup_location,
                destination,
                dispatch_time,
                dispatch_notes
            )
            VALUES (
                %s, %s, %s, %s, 'Dispatched',
                %s, %s, NOW(), %s
            )
            """,
            (
                case_id,
                ambulance_id,
                driver_id,
                user["user_id"],
                pickup_location or None,
                destination or None,
                notes or None,
            ),
        )

        cursor.execute(
            """
            UPDATE ambulances
            SET ambulance_status = 'dispatched'
            WHERE ambulance_id = %s
            """,
            (ambulance_id,),
        )

        cursor.execute(
            """
            UPDATE emergency_cases
            SET case_status = 'In Progress'
            WHERE emergency_case_id = %s
              AND case_status IN ('Reported', 'Triaged')
            """,
            (case_id,),
        )

        mysql.connection.commit()

        _record_emergency_activity(
            "Ambulance Dispatched",
            (
                f"Ambulance {ambulance_id} dispatched "
                f"for emergency case {case_id}."
            ),
            user,
        )

        flash("Ambulance dispatched successfully.", "success")

    except Exception:
        try:
            mysql.connection.rollback()
        except Exception:
            logger.exception("Dispatch rollback failed.")

        logger.exception("Ambulance dispatch failed.")

        flash(
            "Unable to dispatch ambulance. "
            "Please verify the selected case, ambulance and driver.",
            "danger",
        )

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.ambulance_dispatches"))


@admin_bp.post("/emergency/dispatches/<int:dispatch_id>/status")
@login_required
def update_dispatch_status(dispatch_id):

    user = _require_emergency_admin()

    new_status = _clean(
        request.form.get("dispatch_status"),
        30,
    )

    allowed_statuses = {
        "Arrived",
        "Transporting",
        "Completed",
        "Cancelled",
    }

    if new_status not in allowed_statuses:
        flash("Invalid dispatch status.", "danger")
        return redirect(url_for("admin.ambulance_dispatches"))

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            SELECT
                ad.dispatch_id,
                ad.ambulance_id,
                ad.emergency_case_id,
                ad.dispatch_status

            FROM ambulance_dispatches ad

            WHERE ad.dispatch_id = %s

            FOR UPDATE
            """,
            (dispatch_id,),
        )

        dispatch = cursor.fetchone()

        if not dispatch:
            mysql.connection.rollback()
            flash("Dispatch record not found.", "warning")
            return redirect(url_for("admin.ambulance_dispatches"))

        if dispatch["dispatch_status"] in ("Completed", "Cancelled"):
            mysql.connection.rollback()
            flash("This dispatch has already been closed.", "warning")
            return redirect(url_for("admin.ambulance_dispatches"))

        timestamp_column = {
            "Arrived": "arrival_time",
            "Transporting": "pickup_time",
            "Completed": "completed_at",
        }.get(new_status)

        # Column names come only from this fixed internal mapping.
        timestamp_sql = (
            f"{timestamp_column} = NOW(),"
            if timestamp_column else ""
        )

        cursor.execute(
            f"""
            UPDATE ambulance_dispatches
            SET
                dispatch_status = %s,
                {timestamp_sql}
                updated_at = CURRENT_TIMESTAMP
            WHERE dispatch_id = %s
            """,
            (new_status, dispatch_id),
        )

        if new_status in ("Completed", "Cancelled"):
            cursor.execute(
                """
                UPDATE ambulances
                SET ambulance_status = 'available'
                WHERE ambulance_id = %s
                """,
                (dispatch["ambulance_id"],),
            )

        mysql.connection.commit()

        _record_emergency_activity(
            "Ambulance Dispatch Updated",
            f"Dispatch {dispatch_id} changed to {new_status}.",
            user,
        )

        flash("Dispatch status updated successfully.", "success")

    except Exception:
        try:
            mysql.connection.rollback()
        except Exception:
            logger.exception("Dispatch status rollback failed.")

        logger.exception("Dispatch status update failed.")

        flash("Unable to update dispatch status.", "danger")

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.ambulance_dispatches"))

# ============================================================
# EMERGENCY WORKFORCE & 24/7 SHIFT MANAGEMENT
# Uses existing shifts, staff_schedules and doctor_schedules.
# No database schema changes.
# ============================================================

def _staffing_admin_user():
    """Return the authenticated Super Admin for staffing operations."""
    user = get_current_user()

    if not user:
        abort(403)

    user_id = user.get("user_id")
    if not user_id or "super_admin" not in get_user_roles(user_id):
        abort(403)

    return user


def _staffing_parse_date(value):
    """Validate an HTML date input."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _staffing_parse_time(value):
    """Validate an HTML time input."""
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        return None


@admin_bp.get("/emergency/staffing")
@login_required
def emergency_staffing():
    """24/7 emergency workforce dashboard."""

    user = _staffing_admin_user()

    metrics = emergency_fetch_one(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM doctors
                WHERE availability_status = 'available'
            ) AS available_doctors,

            (
                SELECT COUNT(*)
                FROM doctors
            ) AS total_doctors,

            (
                SELECT COUNT(*)
                FROM staff
                WHERE employment_status = 'active'
            ) AS active_staff,

            (
                SELECT COUNT(*)
                FROM staff
                WHERE employment_status = 'active'
                  AND LOWER(designation) LIKE '%%nurse%%'
            ) AS active_nurses,

            (
                SELECT COUNT(*)
                FROM staff_schedules
                WHERE duty_date = CURDATE()
                  AND status = 'Scheduled'
            ) AS today_staff_schedules,

            (
                SELECT COUNT(*)
                FROM doctor_schedules
                WHERE schedule_status = 'Approved'
                  AND (
                      (
                          schedule_type = 'Date Specific'
                          AND specific_date = CURDATE()
                      )
                      OR
                      (
                          schedule_type = 'Weekly'
                          AND day_of_week =
                              MOD(DAYOFWEEK(CURDATE()) + 5, 7)
                          AND (
                              effective_from IS NULL
                              OR effective_from <= CURDATE()
                          )
                          AND (
                              effective_until IS NULL
                              OR effective_until >= CURDATE()
                          )
                      )
                  )
            ) AS approved_doctor_schedules
        """
    ) or {}

    shifts = emergency_fetch_all(
        """
        SELECT
            sh.shift_id,
            sh.shift_name,
            sh.start_time,
            sh.end_time,

            COUNT(DISTINCT ss.schedule_id) AS assigned_staff,

            COUNT(
                DISTINCT CASE
                    WHEN LOWER(st.designation) LIKE '%%nurse%%'
                    THEN ss.schedule_id
                END
            ) AS assigned_nurses,

            COUNT(
                DISTINCT CASE
                    WHEN LOWER(st.designation) LIKE '%%doctor%%'
                    THEN ss.schedule_id
                END
            ) AS assigned_doctors

        FROM shifts sh

        LEFT JOIN staff_schedules ss
            ON ss.shift_id = sh.shift_id
           AND ss.duty_date = CURDATE()
           AND ss.status = 'Scheduled'

        LEFT JOIN staff st
            ON st.staff_id = ss.staff_id

        WHERE sh.is_active = 1

        GROUP BY
            sh.shift_id,
            sh.shift_name,
            sh.start_time,
            sh.end_time

        ORDER BY sh.start_time
        """
    )

    staff = emergency_fetch_all(
        """
        SELECT
            staff_id,
            staff_name,
            designation,
            employment_status
        FROM staff
        WHERE employment_status = 'active'
        ORDER BY designation, staff_name
        """
    )

    doctors = emergency_fetch_all(
        """
        SELECT
            doctor_id,
            doctor_name,
            specialization,
            availability_status
        FROM doctors
        ORDER BY doctor_name
        """
    )

    shift_options = emergency_fetch_all(
        """
        SELECT
            shift_id,
            shift_name,
            start_time,
            end_time
        FROM shifts
        WHERE is_active = 1
        ORDER BY start_time
        """
    )

    today_assignments = emergency_fetch_all(
        """
        SELECT
            ss.schedule_id,
            ss.duty_date,
            ss.status,
            ss.notes,
            st.staff_name,
            st.designation,
            sh.shift_name,
            sh.start_time,
            sh.end_time

        FROM staff_schedules ss

        INNER JOIN staff st
            ON st.staff_id = ss.staff_id

        INNER JOIN shifts sh
            ON sh.shift_id = ss.shift_id

        WHERE ss.duty_date = CURDATE()

        ORDER BY sh.start_time, st.staff_name
        """
    )

    doctor_schedules = emergency_fetch_all(
        """
        SELECT
            ds.schedule_id,
            ds.schedule_type,
            ds.day_of_week,
            ds.specific_date,
            ds.start_time,
            ds.end_time,
            ds.effective_from,
            ds.effective_until,
            ds.schedule_status,
            d.doctor_name,
            d.specialization

        FROM doctor_schedules ds

        INNER JOIN doctors d
            ON d.doctor_id = ds.doctor_id

        ORDER BY
            CASE ds.schedule_status
                WHEN 'Pending' THEN 1
                WHEN 'Approved' THEN 2
                WHEN 'Rejected' THEN 3
                ELSE 4
            END,
            ds.created_at DESC

        LIMIT 100
        """
    )

    return _render_emergency(
        "admin/emergency_staffing.html",
        user=user,
        metrics=metrics,
        shifts=shifts,
        staff=staff,
        doctors=doctors,
        shift_options=shift_options,
        today_assignments=today_assignments,
        doctor_schedules=doctor_schedules,
        breadcrumb="Emergency Staffing",
    )


@admin_bp.post("/emergency/staffing/staff-schedules/create")
@login_required
def create_emergency_staff_schedule():
    """Assign an active staff member to an existing shift."""

    user = _staffing_admin_user()

    staff_id = request.form.get("staff_id", type=int)
    shift_id = request.form.get("shift_id", type=int)
    duty_date = _staffing_parse_date(
        request.form.get("duty_date")
    )
    notes = _clean(request.form.get("notes"), 255)

    if not staff_id or not shift_id or not duty_date:
        flash("Please provide a valid staff member, shift and date.", "danger")
        return redirect(url_for("admin.emergency_staffing"))

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            SELECT staff_id, staff_name
            FROM staff
            WHERE staff_id = %s
              AND employment_status = 'active'
            """,
            (staff_id,),
        )
        selected_staff = cursor.fetchone()

        if not selected_staff:
            flash("The selected staff member is not active.", "danger")
            return redirect(url_for("admin.emergency_staffing"))

        cursor.execute(
            """
            SELECT shift_id
            FROM shifts
            WHERE shift_id = %s
              AND is_active = 1
            """,
            (shift_id,),
        )

        if not cursor.fetchone():
            flash("The selected shift is not available.", "danger")
            return redirect(url_for("admin.emergency_staffing"))

        cursor.execute(
            """
            SELECT schedule_id
            FROM staff_schedules
            WHERE staff_id = %s
              AND shift_id = %s
              AND duty_date = %s
            """,
            (staff_id, shift_id, duty_date),
        )

        if cursor.fetchone():
            flash(
                "This staff member already has an assignment "
                "for that shift and date.",
                "warning",
            )
            return redirect(url_for("admin.emergency_staffing"))

        cursor.execute(
            """
            INSERT INTO staff_schedules (
                staff_id,
                shift_id,
                duty_date,
                status,
                assigned_by,
                notes
            )
            VALUES (%s, %s, %s, 'Scheduled', %s, %s)
            """,
            (
                staff_id,
                shift_id,
                duty_date,
                user["user_id"],
                notes or None,
            ),
        )

        mysql.connection.commit()

        _record_emergency_activity(
            "Emergency Staff Scheduled",
            (
                f"Staff member {selected_staff['staff_name']} "
                f"was assigned to shift {shift_id} "
                f"on {duty_date}."
            ),
            user,
        )

        flash("Staff shift assignment created successfully.", "success")

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Emergency staff scheduling failed."
        )
        flash(
            "Unable to create the staff assignment. "
            "Please check the selected records.",
            "danger",
        )

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.emergency_staffing"))


@admin_bp.post("/emergency/staffing/doctor-schedules/create")
@login_required
def create_emergency_doctor_schedule():
    """Create a doctor schedule using the existing approval workflow."""

    user = _staffing_admin_user()

    doctor_id = request.form.get("doctor_id", type=int)
    schedule_type = _clean(
        request.form.get("schedule_type"), 30
    )

    start_time = _staffing_parse_time(
        request.form.get("start_time")
    )
    end_time = _staffing_parse_time(
        request.form.get("end_time")
    )

    if not doctor_id or schedule_type not in (
        "Weekly",
        "Date Specific",
    ):
        flash("Invalid doctor or schedule type.", "danger")
        return redirect(url_for("admin.emergency_staffing"))

    if not start_time or not end_time or start_time == end_time:
        flash("Please provide valid, different start and end times.", "danger")
        return redirect(url_for("admin.emergency_staffing"))

    day_of_week = None
    specific_date = None
    effective_from = None
    effective_until = None

    if schedule_type == "Weekly":
        day_of_week = request.form.get("day_of_week", type=int)

        if day_of_week is None or day_of_week not in range(7):
            flash("Select a valid weekday.", "danger")
            return redirect(url_for("admin.emergency_staffing"))

        effective_from = _staffing_parse_date(
            request.form.get("effective_from")
        )
        effective_until = _staffing_parse_date(
            request.form.get("effective_until")
        )

        if effective_from and effective_until:
            if effective_until < effective_from:
                flash(
                    "The effective end date cannot be before "
                    "the start date.",
                    "danger",
                )
                return redirect(url_for("admin.emergency_staffing"))

    else:
        specific_date = _staffing_parse_date(
            request.form.get("specific_date")
        )

        if not specific_date:
            flash("Select a valid schedule date.", "danger")
            return redirect(url_for("admin.emergency_staffing"))

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            SELECT doctor_id, doctor_name
            FROM doctors
            WHERE doctor_id = %s
            """,
            (doctor_id,),
        )
        doctor = cursor.fetchone()

        if not doctor:
            flash("Selected doctor was not found.", "danger")
            return redirect(url_for("admin.emergency_staffing"))

        cursor.execute(
            """
            INSERT INTO doctor_schedules (
                doctor_id,
                schedule_type,
                day_of_week,
                specific_date,
                start_time,
                end_time,
                effective_from,
                effective_until,
                schedule_status,
                created_by
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                'Pending', %s
            )
            """,
            (
                doctor_id,
                schedule_type,
                day_of_week,
                specific_date,
                start_time,
                end_time,
                effective_from,
                effective_until,
                user["user_id"],
            ),
        )

        mysql.connection.commit()

        _record_emergency_activity(
            "Doctor Schedule Submitted",
            (
                f"A {schedule_type.lower()} schedule was submitted "
                f"for {doctor['doctor_name']}. "
                "Approval is pending."
            ),
            user,
        )

        flash(
            "Doctor schedule submitted. It is pending approval.",
            "success",
        )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Doctor schedule creation failed."
        )
        flash("Unable to create the doctor schedule.", "danger")

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.emergency_staffing"))


@admin_bp.post(
    "/emergency/staffing/doctor-schedules/<int:schedule_id>/status"
)
@login_required
def update_emergency_doctor_schedule_status(schedule_id):
    """Approve or reject a pending doctor schedule."""

    user = _staffing_admin_user()

    new_status = _clean(
        request.form.get("schedule_status"), 20
    )

    if new_status not in ("Approved", "Rejected"):
        flash("Invalid doctor schedule status.", "danger")
        return redirect(url_for("admin.emergency_staffing"))

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            SELECT schedule_id, schedule_status
            FROM doctor_schedules
            WHERE schedule_id = %s
            FOR UPDATE
            """,
            (schedule_id,),
        )

        schedule = cursor.fetchone()

        if not schedule:
            mysql.connection.rollback()
            flash("Doctor schedule not found.", "warning")
            return redirect(url_for("admin.emergency_staffing"))

        if schedule["schedule_status"] != "Pending":
            mysql.connection.rollback()
            flash(
                "Only pending doctor schedules can be reviewed.",
                "warning",
            )
            return redirect(url_for("admin.emergency_staffing"))

        cursor.execute(
            """
            UPDATE doctor_schedules
            SET
                schedule_status = %s,
                approved_by = %s,
                approved_at = CURRENT_TIMESTAMP
            WHERE schedule_id = %s
            """,
            (
                new_status,
                user["user_id"],
                schedule_id,
            ),
        )

        mysql.connection.commit()

        _record_emergency_activity(
            "Doctor Schedule Reviewed",
            (
                f"Doctor schedule {schedule_id} "
                f"was {new_status.lower()}."
            ),
            user,
        )

        flash(
            f"Doctor schedule {new_status.lower()} successfully.",
            "success",
        )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Doctor schedule review failed."
        )
        flash("Unable to update doctor schedule.", "danger")

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.emergency_staffing"))

