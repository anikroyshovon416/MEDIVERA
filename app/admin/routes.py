from flask import (
    abort,
    current_app,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
import secrets

from werkzeug.security import generate_password_hash
from app.auth.email_service import send_account_credentials
from datetime import datetime
from datetime import date, time, timedelta
import os
import re
import uuid
from app.admin import admin_bp
from app.extensions import mysql
from app.auth.decorators import (
    login_required,
    get_current_user,
    get_user_roles,
)
from app.system_settings import (
    get_current_settings,
    validate_settings,
    save_persistent_settings,
)

from app.activity_logger import log_activity
from werkzeug.utils import secure_filename


ADMIN_ROLE_NAMES = {
    "hr_admin",
    "attendance_admin",
    "patient_services_admin",
    "equipment_admin",
    "food_admin",
    "meal_delivery_admin",
}

ROLE_TITLES = {
    "hr_admin": "Human Resources Admin",
    "attendance_admin": "Attendance Admin",
    "patient_services_admin": "Patient Services Admin",
    "equipment_admin": "Equipment Admin",
    "food_admin": "Food Admin",
    "meal_delivery_admin": "Meal Delivery Admin",
}

RESPONSIBILITY_MODULES = {
    "Patient Services": [
        ("Patients", "users", "patients.view"),
        ("Admissions", "clipboard-list", "admissions.view"),
        ("Admission Approvals", "check-circle", "admissions.approve"),
        ("Rooms", "bed", "rooms.view"),
        ("Room Management", "building", "rooms.manage"),
        ("Doctor Assignments", "user-check", "doctors.assign"),
        ("Approval Requests", "inbox", "approvals.view"),
    ],

    "Appointment Management": [
        ("Appointments", "calendar", "appointments.view"),
        ("Manage Appointments", "calendar-check", "appointments.manage"),
        ("Approve Appointments", "check-circle", "appointments.approve"),
        ("Reject Appointments", "x-circle", "appointments.reject"),
        ("Doctor Schedules", "calendar-clock", "doctors.schedule.manage"),
        ("Schedule Approvals", "calendar-check", "doctors.schedule.approve"),
    ],

    "Equipment Management": [
        ("Equipment", "monitor", "equipment.view"),
        ("Manage Equipment", "settings", "equipment.manage"),
        ("Equipment Requests", "clipboard-check", "equipment.approve"),
        ("Inventory", "package", "inventory.view"),
        ("Manage Inventory", "boxes", "inventory.manage"),
    ],

    "Human Resources": [
        ("Staff", "users", "staff.view"),
        ("Manage Staff", "user-cog", "staff.manage"),
        ("Staff Registration", "user-plus", "staff.approve_registration"),
        ("Leave Applications", "calendar-days", "leaves.view"),
        ("Approve Leaves", "check-circle", "leaves.approve"),
    ],

    "Attendance Management": [
        ("Attendance", "clock", "attendance.view"),
        ("Manage Attendance", "calendar-check", "attendance.manage"),
        ("Schedules", "calendar", "schedules.view"),
        ("Manage Schedules", "calendar-clock", "schedules.manage"),
    ],

    "Food & Meal Management": [
        ("Meal Schedule", "calendar", "meals.view"),
        ("Manage Meals", "utensils", "meals.manage"),
        ("Meal Requests", "clipboard-list", "meals.view_requests"),
        ("Approve Meal Requests", "check-circle", "meals.approve"),
        ("Meal Delivery", "truck", "meals.delivery_update"),
    ],

    "Department Management": [
        ("Hospital Services", "building", "services.view"),
        ("Manage Hospital Services", "settings", "services.manage"),
    ],
}


ROLE_RESPONSIBILITIES = {
    "hr_admin": "Human Resources",
    "attendance_admin": "Attendance Management",
    "patient_services_admin": "Patient Services",
    "equipment_admin": "Equipment Management",
    "food_admin": "Food & Meal Management",
    "meal_delivery_admin": "Food & Meal Management",
}


def fetch_all(query, params=()):
    cursor = None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()


def fetch_one(query, params=()):
    cursor = None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()


def get_admin_permissions(user_id):
    rows = fetch_all(
        """
        SELECT DISTINCT p.permission_key
        FROM user_roles ur
        INNER JOIN role_permissions rp
            ON rp.role_id = ur.role_id
        INNER JOIN permissions p
            ON p.permission_id = rp.permission_id
        WHERE ur.user_id = %s
        """,
        (user_id,),
    )

    return {
        row["permission_key"]
        for row in rows
        if row.get("permission_key")
    }


def get_admin_assignments(user_id):
    return fetch_all(
        """
        SELECT
            assignment_id,
            responsibility,
            department_id
        FROM admin_assignments
        WHERE admin_user_id = %s
          AND is_active = 1
        ORDER BY responsibility
        """,
        (user_id,),
    )


@admin_bp.get("/super-dashboard")
@login_required
def super_dashboard():
    user = get_current_user()

    if not user or "super_admin" not in get_user_roles():
        abort(403)

    metrics = fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM patients) AS total_patients,
            (SELECT COUNT(*) FROM doctors) AS total_doctors,
            (SELECT COUNT(*) FROM appointments) AS total_appointments,
            (
                SELECT COUNT(*)
                FROM appointments
                WHERE status = 'pending_approval'
            ) AS pending_appointments,
            (SELECT COUNT(*) FROM admissions) AS total_admissions,
            (SELECT COUNT(*) FROM inventory) AS inventory_items,
            (
                SELECT COUNT(*)
                FROM admin_assignments
                WHERE is_active = 1
            ) AS active_assignments
        """
    )

    
    nav_items = [
        {
            "label": "System Overview",
            "icon": "layout-dashboard",
            "endpoint": "admin.super_dashboard",
        },
        {
            "label": "Patient Management",
            "icon": "users",
            "endpoint": "admin.patients",
        },
        {
            "label": "Doctor Management",
            "icon": "stethoscope",
            "endpoint": "admin.doctors",
        },
        {
            "label": "Staff Management",
            "icon": "users",
            "endpoint": "admin.staff",
        },
        {
            "label": "Meal Management",
            "icon": "utensils",
            "endpoint": "admin.meals_dashboard",
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
                {
            "label": "All Users",
            "icon": "users",
            "endpoint": "admin.user_accounts",
        },
        {
            "label": "Create Account",
            "icon": "user-plus",
            "endpoint": "admin.create_user_account",
        },

    ]
    return render_template(
        "admin/super_dashboard.html",
        user=user,
        metrics=metrics,
        nav_items=nav_items,
        is_super_admin=True
    )

@admin_bp.get("/administrators")
@login_required
def administrators():
    user = get_current_user()

    if not user or "super_admin" not in get_user_roles():
        abort(403)

    administrators_list = fetch_all(
        """
        SELECT
            u.user_id,
            u.username,
            u.email,
            u.is_active,

            (
                SELECT GROUP_CONCAT(
                    DISTINCT r.role_name
                    ORDER BY r.role_name
                    SEPARATOR ', '
                )
                FROM user_roles ur
                INNER JOIN roles r
                    ON r.role_id = ur.role_id
                WHERE ur.user_id = u.user_id
                  AND r.role_name IN (
                      'super_admin',
                      'hr_admin',
                      'attendance_admin',
                      'patient_services_admin',
                      'equipment_admin',
                      'food_admin',
                      'meal_delivery_admin'
                  )
            ) AS assigned_roles,

            (
                SELECT GROUP_CONCAT(
                    DISTINCT aa.responsibility
                    ORDER BY aa.responsibility
                    SEPARATOR ', '
                )
                FROM admin_assignments aa
               WHERE aa.admin_user_id = u.user_id
  AND aa.is_active = 1
            ) AS responsibilities,

            (
                SELECT GROUP_CONCAT(
                    DISTINCT COALESCE(d.department_name, 'Unassigned')
                    ORDER BY d.department_name
                    SEPARATOR ', '
                )
                FROM admin_assignments aa
                LEFT JOIN departments d
                    ON d.department_id = aa.department_id
                WHERE aa.admin_user_id = u.user_id
            ) AS departments,

            (
                SELECT COUNT(*)
                FROM admin_assignments aa
                WHERE aa.admin_user_id = u.user_id
                  AND aa.is_active = 1
            ) AS active_assignments

        FROM users u

        WHERE EXISTS (
            SELECT 1
            FROM user_roles ur
            INNER JOIN roles r
                ON r.role_id = ur.role_id
            WHERE ur.user_id = u.user_id
              AND r.role_name IN (
                  'super_admin',
                  'hr_admin',
                  'attendance_admin',
                  'patient_services_admin',
                  'equipment_admin',
                  'food_admin',
                  'meal_delivery_admin'
              )
        )

        ORDER BY u.user_id DESC
        """
    )

   
    nav_items = [
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


    return render_template(
        "admin/administrators.html",
        user=user,
        administrators=administrators_list,
        nav_items=nav_items,
        is_super_admin=True,
    )

@admin_bp.get("/roles-permissions")
@login_required
def roles_permissions():
    user = get_current_user()

    if not user or "super_admin" not in get_user_roles():
        abort(403)

    roles_list = fetch_all("""
        SELECT
            r.role_id,
            r.role_name,
            r.description,
            r.is_active,
            COUNT(DISTINCT rp.permission_id) AS permission_count
        FROM roles r
        LEFT JOIN role_permissions rp
            ON rp.role_id = r.role_id
        GROUP BY
            r.role_id,
            r.role_name,
            r.description,
            r.is_active
        ORDER BY r.role_id
    """)

    permission_rows = fetch_all("""
        SELECT
            r.role_id,
            p.permission_key,
            p.permission_name
        FROM roles r
        LEFT JOIN role_permissions rp
            ON rp.role_id = r.role_id
        LEFT JOIN permissions p
            ON p.permission_id = rp.permission_id
        ORDER BY r.role_id, p.permission_key
    """)

    permission_map = {}

    for row in permission_rows:
        if row["permission_key"]:
            permission_map.setdefault(row["role_id"], []).append(
                row["permission_key"]
            )

    for role in roles_list:
        role["permissions"] = permission_map.get(role["role_id"], [])

    nav_items = [
        {"label": "System Overview", "icon": "layout-dashboard",
         "endpoint": "admin.super_dashboard"},
        {"label": "Administrators", "icon": "users",
         "endpoint": "admin.administrators"},
        {"label": "Roles & Permissions", "icon": "shield-check",
         "endpoint": "admin.roles_permissions"},
        {"label": "Approvals", "icon": "clipboard-check",
         "endpoint": "admin.super_dashboard"},
        {"label": "Reports", "icon": "chart-no-axes-combined",
         "endpoint": "admin.super_dashboard"},
        {"label": "Activity Logs", "icon": "history",
         "endpoint": "admin.super_dashboard"},
        {"label": "System Settings", "icon": "settings",
         "endpoint": "admin.super_dashboard"},

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

    return render_template(
        "admin/roles_permissions.html",
        user=user,
        roles=roles_list,
        nav_items=nav_items,
        is_super_admin=True,
    )


@admin_bp.route("/approvals", methods=["GET"])
@login_required
def approvals():
    user = get_current_user()

    if not user or "super_admin" not in get_user_roles():
        abort(403)

    # ---------------------------------------------------------
    # 1. Existing departmental approval requests
    # ---------------------------------------------------------
    
    # 1. Existing departmental requests + ambulance requests
    approval_requests = list(fetch_all("""
        SELECT
            ar.request_id,
            ar.requester_user_id,
            ar.request_type,
            ar.request_title,
            ar.request_description,
            ar.responsibility,
            ar.department_id,
            ar.assigned_admin_id,
            ar.status,
            ar.submitted_at,
            ar.reviewed_at,
            ar.admin_remarks,
            ar.reference_table,
            ar.reference_id,
            ec.case_number,
            ec.emergency_type,
            ec.severity,
            ec.case_status,
            ec.emergency_location
        FROM approval_requests ar
        LEFT JOIN emergency_cases ec
            ON ar.reference_table = 'emergency_cases'
            AND ar.reference_id = ec.emergency_case_id
        ORDER BY ar.submitted_at DESC
        LIMIT 100
    """))

    
    requests = []

    # Process departmental requests and ambulance requests.
    for item in approval_requests:
        row = dict(item)

        is_ambulance = (
            row.get("request_type") == "Other"
            and row.get("reference_table") == "emergency_cases"
            and str(row.get("request_title") or "").startswith(
                "Ambulance Request -"
            )
        )

        is_room_bed = (
            row.get("request_type") == "Room/Bed Request"
            and row.get("reference_table") == "beds"
        )

        row["source"] = (
            "ambulance" if is_ambulance
            else "room_bed" if is_room_bed
            else "request"
        )

        row["can_decide"] = (
            (is_ambulance or is_room_bed)
            and row.get("status") in (
                "Pending",
                "Under Review",
                "Changes Requested",
            )
        )

        row["decision_reason"] = row.get("admin_remarks") or ""

        if is_ambulance:
            row["request_type"] = "Ambulance Request"

            emergency_details = (
                f"Emergency Case: {row.get('case_number') or 'N/A'} | "
                f"Type: {row.get('emergency_type') or 'N/A'} | "
                f"Severity: {row.get('severity') or 'N/A'} | "
                f"Case Status: {row.get('case_status') or 'N/A'} | "
                f"Location: {row.get('emergency_location') or 'N/A'}"
            )

            row["request_description"] = (
                f"{row.get('request_description') or ''}\n"
                f"{emergency_details}"
            )

        requests.append(row)


    # ---------------------------------------------------------
    # 2. Appointments: pending, confirmed and rejected
    # ---------------------------------------------------------
    appointments = fetch_all("""
        SELECT
            a.appointment_id,
            a.patient_id,
            a.doctor_id,
            a.appointment_date,
            a.appointment_time,
            a.reason,
            a.status,
            a.created_at,

            p.first_name,
            p.last_name,

            d.doctor_name,

            h.decision,
            h.decision_reason,
            h.action_at,
            h.action_by

        FROM appointments a

        LEFT JOIN patients p
            ON a.patient_id = p.patient_id

        LEFT JOIN doctors d
            ON a.doctor_id = d.doctor_id

        LEFT JOIN appointment_approval_history h
            ON a.appointment_id = h.appointment_id

        WHERE a.status IN (
            'pending_approval',
            'confirmed',
            'rejected'
        )

        ORDER BY a.created_at DESC
        LIMIT 100
    """)

    # ---------------------------------------------------------
    # 3. Convert appointment rows into approval table rows
    # ---------------------------------------------------------
    for appointment in appointments:

        patient_name = (
            f"{appointment.get('first_name') or ''} "
            f"{appointment.get('last_name') or ''}"
        ).strip()

        if not patient_name:
            patient_name = f"Patient #{appointment['patient_id']}"

        doctor_name = (
            appointment.get("doctor_name")
            or f"Doctor #{appointment['doctor_id']}"
        )

        appointment_time = appointment.get("appointment_time")

        if appointment_time is None:
            time_display = "Not specified"

        elif hasattr(appointment_time, "total_seconds"):
            total_seconds = int(appointment_time.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60

            time_display = (
                f"{((hours - 1) % 12) + 1:02d}:{minutes:02d} "
                f"{'AM' if hours < 12 else 'PM'}"
            )

        elif hasattr(appointment_time, "strftime"):
            time_display = appointment_time.strftime("%I:%M %p")

        else:
            time_display = str(appointment_time)

        appointment_date = appointment.get("appointment_date")

        date_display = (
            appointment_date.strftime("%d %b %Y")
            if hasattr(appointment_date, "strftime")
            else str(appointment_date or "Not specified")
        )

        appointment_id = appointment["appointment_id"]
        appointment_status = appointment.get("status") or ""

        if appointment_status == "confirmed":
            display_status = "Approved"
        elif appointment_status == "rejected":
            display_status = "Rejected"
        else:
            display_status = "Pending"

        requests.append({
            "request_id": f"APPT-{appointment_id}",
            "appointment_id": appointment_id,
            "requester_user_id": appointment["patient_id"],
            "request_type": "Appointment",
            "request_title": (
                f"Appointment #{appointment_id} - {patient_name}"
            ),
            "request_description": (
                f"Doctor: {doctor_name} | "
                f"Date: {date_display} | "
                f"Time: {time_display} | "
                f"Reason: {appointment.get('reason') or 'Not specified'}"
            ),
            "responsibility": "Appointment Management",
            "department_id": None,
            "assigned_admin_id": None,
            "status": display_status,
            "submitted_at": appointment.get("created_at"),
            "reviewed_at": appointment.get("action_at"),
            "decision_reason": appointment.get("decision_reason") or "",
            "decision": appointment.get("decision"),
            "source": "appointment",
            "can_decide": appointment_status == "pending_approval",
        })

    # ---------------------------------------------------------
    # 4. Leave applications
    # ---------------------------------------------------------
    leave_applications = fetch_all("""
        SELECT
            leave_id,
            staff_id,
            leave_type,
            start_date,
            end_date,
            reason,
            status,
            applied_at,
            reviewed_by,
            reviewed_at,
            admin_remarks
        FROM leave_applications
        WHERE status IN ('Pending', 'Approved', 'Rejected')
        ORDER BY applied_at DESC
        LIMIT 100
    """)

    for leave in leave_applications:
        start_date = leave.get("start_date")
        end_date = leave.get("end_date")

        start_display = (
            start_date.strftime("%d %b %Y")
            if hasattr(start_date, "strftime")
            else str(start_date or "Not specified")
        )

        end_display = (
            end_date.strftime("%d %b %Y")
            if hasattr(end_date, "strftime")
            else str(end_date or "Not specified")
        )

        leave_id = leave["leave_id"]

        requests.append({
            "request_id": f"LEAVE-{leave_id}",
            "leave_id": leave_id,
            "requester_user_id": leave["staff_id"],
            "request_type": "Staff Leave",
            "request_title": (
                f"{leave.get('leave_type') or 'Leave'} "
                f"Application #{leave_id}"
            ),
            "request_description": (
                f"Staff ID: {leave['staff_id']} | "
                f"Leave: {leave.get('leave_type')} | "
                f"From: {start_display} | "
                f"To: {end_display} | "
                f"Reason: {leave.get('reason') or 'Not specified'}"
            ),
            "responsibility": "Human Resources",
            "department_id": None,
            "assigned_admin_id": None,
            "status": leave.get("status") or "Pending",
            "submitted_at": leave.get("applied_at"),
            "reviewed_at": leave.get("reviewed_at"),
            "decision_reason": leave.get("admin_remarks") or "",
            "source": "leave",
            "can_decide": leave.get("status") == "Pending",
        })

    # ---------------------------------------------------------
    # 4. Sort combined records by submission date
    # ---------------------------------------------------------
    requests.sort(
        key=lambda row: str(row.get("submitted_at") or ""),
        reverse=True
    )

    # ---------------------------------------------------------
    # 5. Calculate summary counts
    #    These counts include all matching records, not just
    #    the currently displayed filter.
    # ---------------------------------------------------------
    summary_rows = fetch_all("""
        SELECT status, COUNT(*) AS total
        FROM approval_requests
        GROUP BY status
    """)

    appointment_summary_rows = fetch_all("""
        SELECT status, COUNT(*) AS total
        FROM appointments
        WHERE status IN (
            'pending_approval',
            'confirmed',
            'rejected'
        )
        GROUP BY status
    """)

    summary = {
        "total": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
    }

    for row in summary_rows:
        status = (row.get("status") or "").lower()
        count = int(row.get("total") or 0)

        summary["total"] += count

        if status in (
            "pending",
            "pending_approval",
            "submitted",
            "under review",
        ):
            summary["pending"] += count

        elif status == "approved":
            summary["approved"] += count

        elif status == "rejected":
            summary["rejected"] += count

    for row in appointment_summary_rows:
        status = (row.get("status") or "").lower()
        count = int(row.get("total") or 0)

        summary["total"] += count

        if status == "pending_approval":
            summary["pending"] += count

        elif status == "confirmed":
            summary["approved"] += count

        elif status == "rejected":
            summary["rejected"] += count

    leave_summary_rows = fetch_all("""
        SELECT status, COUNT(*) AS total
        FROM leave_applications
        WHERE status IN ('Pending', 'Approved', 'Rejected')
        GROUP BY status
    """)

    for row in leave_summary_rows:
        status = (row.get("status") or "").lower()
        count = int(row.get("total") or 0)

        summary["total"] += count

        if status == "pending":
            summary["pending"] += count
        elif status == "approved":
            summary["approved"] += count
        elif status == "rejected":
            summary["rejected"] += count

    # ---------------------------------------------------------
    # 6. Filter table using clickable summary cards
    # ---------------------------------------------------------
    active_filter = request.args.get("status", "all").lower()

    allowed_filters = {
        "all",
        "pending",
        "approved",
        "rejected",
    }

    if active_filter not in allowed_filters:
        active_filter = "all"

    if active_filter != "all":
        requests = [
            row for row in requests
            if (row.get("status") or "").lower() == active_filter
        ]

    requests = requests[:100]


    # Available ambulances for approved requests
    available_ambulances = list(fetch_all("""
        SELECT
            ambulance_id,
            ambulance_code,
            registration_number,
            ambulance_type
        FROM ambulances
        WHERE ambulance_status = 'available'
        ORDER BY ambulance_code
    """))

       # Active staff members designated as drivers
    available_drivers = list(fetch_all("""
        SELECT staff_id, staff_name, designation
        FROM staff
        WHERE employment_status = 'active'
          AND LOWER(designation) LIKE '%%driver%%'
        ORDER BY staff_name
    """))


    # ---------------------------------------------------------
    # 7. Super Admin sidebar
    # ---------------------------------------------------------
    
    nav_items = [
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


    return render_template(
    "admin/approvals.html",
    user=user,
    requests=requests,
    summary=summary,
    nav_items=nav_items,
    is_super_admin=True,
    active_filter=active_filter,
    available_ambulances=available_ambulances,
    available_drivers=available_drivers,
)
    # =============================================================


# =============================================================
# MEDIVERA - PATIENT ROOM/BED ADMISSION APPROVAL
# Super Admin only. Creates the admission only after approval.
# Uses existing approval_requests, approval_history,
# admissions, beds, rooms and users tables.
# =============================================================

@admin_bp.post("/approvals/room-bed/<int:request_id>/decision")
@login_required
def decide_room_bed_request(request_id):

    if not _is_super_admin():
        abort(403)

    user = get_current_user()
    if not user:
        abort(401)

    decision = (request.form.get("decision") or "").strip()
    remarks = (request.form.get("admin_remarks") or "").strip()

    if decision not in ("Approved", "Rejected"):
        flash("Invalid room/bed decision.", "danger")
        return redirect(url_for("admin.approvals"))

    if len(remarks) > 2000:
        flash("Remarks cannot exceed 2000 characters.", "danger")
        return redirect(url_for("admin.approvals"))

    if decision == "Rejected" and not remarks:
        flash("Please provide a reason for rejecting this bed request.", "danger")
        return redirect(url_for("admin.approvals"))

    cursor = None

    try:
        cursor = mysql.connection.cursor()
        mysql.connection.begin()

        # Lock and validate the request to prevent double processing.
        cursor.execute("""
            SELECT
                request_id,
                requester_user_id,
                request_type,
                request_description,
                reference_table,
                reference_id,
                status
            FROM approval_requests
            WHERE request_id = %s
            FOR UPDATE
        """, (request_id,))
        approval = cursor.fetchone()

        if (
            not approval
            or approval["request_type"] != "Room/Bed Request"
            or approval["reference_table"] != "beds"
        ):
            mysql.connection.rollback()
            flash("Room/bed request not found.", "danger")
            return redirect(url_for("admin.approvals"))

        previous_status = approval["status"]

        if previous_status not in (
            "Pending", "Under Review", "Changes Requested"
        ):
            mysql.connection.rollback()
            flash("This bed request has already been processed.", "warning")
            return redirect(url_for("admin.approvals"))

        admission_id = None
        final_remarks = remarks or None

        if decision == "Approved":
            # Resolve the patient from the requester's actual user record.
            cursor.execute("""
                SELECT patient_id
                FROM users
                WHERE user_id = %s
                LIMIT 1
            """, (approval["requester_user_id"],))
            requester = cursor.fetchone()

            if not requester or not requester.get("patient_id"):
                mysql.connection.rollback()
                flash("The requesting account is not linked to a patient.", "danger")
                return redirect(url_for("admin.approvals"))

            patient_id = requester["patient_id"]

            # Lock the selected bed and its room, then recheck availability.
            cursor.execute("""
                SELECT
                    b.bed_id,
                    b.bed_number,
                    b.bed_status,
                    r.room_id,
                    r.room_number,
                    r.room_type,
                    r.daily_charge,
                    r.room_status
                FROM beds b
                INNER JOIN rooms r ON r.room_id = b.room_id
                WHERE b.bed_id = %s
                FOR UPDATE
            """, (approval["reference_id"],))
            bed = cursor.fetchone()

            if (
                not bed
                or bed["bed_status"] != "available"
                or bed["room_status"] == "maintenance"
            ):
                mysql.connection.rollback()
                flash(
                    "This bed is no longer available or its room is under maintenance.",
                    "warning",
                )
                return redirect(url_for("admin.approvals"))

            # A bed must not already have an active admission.
            cursor.execute("""
                SELECT admission_id
                FROM admissions
                WHERE bed_id = %s
                  AND admission_status = 'admitted'
                LIMIT 1
                FOR UPDATE
            """, (bed["bed_id"],))
            active_bed_admission = cursor.fetchone()

            if active_bed_admission:
                mysql.connection.rollback()
                flash("This bed already has an active admission.", "warning")
                return redirect(url_for("admin.approvals"))

            # Prevent a second active admission for the same patient.
            cursor.execute("""
                SELECT admission_id
                FROM admissions
                WHERE patient_id = %s
                  AND admission_status = 'admitted'
                LIMIT 1
                FOR UPDATE
            """, (patient_id,))
            active_patient_admission = cursor.fetchone()

            if active_patient_admission:
                mysql.connection.rollback()
                flash(
                    "This patient already has an active admission. "
                    "Review that admission before allocating another bed.",
                    "warning",
                )
                return redirect(url_for("admin.approvals"))

            description = approval.get("request_description") or ""
            description_lines = description.splitlines()

            def request_value(label):
                prefix = label.lower() + ":"
                for line in description_lines:
                    if line.strip().lower().startswith(prefix):
                        return line.split(":", 1)[1].strip()
                return ""

            reason = request_value("Reason")
            if not reason:
                reason = "Patient accommodation request"
            reason = reason[:255]

            requested_admission_date = request_value("Admission date")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested_admission_date):
                requested_admission_date = None

            # Create the actual admission only after approval.
            cursor.execute("""
                INSERT INTO admissions (
                    patient_id,
                    room_id,
                    bed_id,
                    admission_date,
                    admission_reason,
                    admission_status
                )
                VALUES (
                    %s, %s, %s,
                    COALESCE(
                        STR_TO_DATE(%s, '%%Y-%%m-%%d'),
                        NOW()
                    ),
                    %s,
                    'admitted'
                )
            """, (
                patient_id,
                bed["room_id"],
                bed["bed_id"],
                requested_admission_date,
                reason,
            ))
            admission_id = cursor.lastrowid

            # Mark the individual bed occupied. Keep room-level status
            # unchanged because rooms may contain multiple beds.
            cursor.execute("""
                UPDATE beds
                SET bed_status = 'occupied'
                WHERE bed_id = %s
                  AND bed_status = 'available'
            """, (bed["bed_id"],))

            if cursor.rowcount != 1:
                raise RuntimeError("Bed availability changed during approval.")

            final_remarks = (
                f"{remarks} | " if remarks else ""
            ) + f"Admission #{admission_id} created for patient #{patient_id}."

        # Record the approval/rejection.
        cursor.execute("""
            UPDATE approval_requests
            SET
                status = %s,
                admin_remarks = %s,
                assigned_admin_id = %s,
                reviewed_at = NOW()
            WHERE request_id = %s
        """, (
            decision,
            final_remarks,
            user["user_id"],
            request_id,
        ))

        cursor.execute("""
            INSERT INTO approval_history (
                request_id,
                action_by,
                action,
                previous_status,
                new_status,
                comments
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            request_id,
            user["user_id"],
            decision,
            previous_status,
            decision,
            final_remarks or f"Request {decision.lower()}.",
        ))

        mysql.connection.commit()

        if decision == "Approved":
            flash(
                f"Bed request approved. Admission #{admission_id} created "
                f"and bed {bed['bed_number']} marked occupied.",
                "success",
            )
        else:
            flash(f"Bed request #{request_id} rejected.", "success")

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Room/bed approval failed for request_id=%s", request_id
        )
        flash(
            "Unable to process this bed request. Check the application log.",
            "danger",
        )

    finally:
        if cursor:
            cursor.close()

    return redirect(url_for("admin.approvals"))


# MEDIVERA - APPOINTMENT APPROVAL ENGINE
# Super Admin only
# =============================================================


def _is_super_admin():
    """Verify that the currently authenticated user is a Super Admin."""

    user = get_current_user()

    if not user:
        return False

    user_id = user.get("user_id")

    if not user_id:
        return False

    roles = get_user_roles(user_id)

    return "super_admin" in roles



def _process_appointment_decision(
    appointment_id,
    decision,
    decision_reason=None,
):
    """
    Process an appointment approval or rejection.

    Existing database tables used:
        appointments
        appointment_approval_history
        users
        notifications

    No schema changes are performed.
    """

    if decision not in ("Approved", "Rejected"):
        return False, "Invalid appointment decision."

    if decision == "Rejected":
        decision_reason = (decision_reason or "").strip()

        if not decision_reason:
            return False, "Please provide a rejection reason."

        if len(decision_reason) > 255:
            return False, "The rejection reason cannot exceed 255 characters."

    user = get_current_user()

    if not user:
        return False, "Your session has expired. Please log in again."

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        # Start a transaction before locking the appointment.
        mysql.connection.begin()

        # -----------------------------------------------------
        # 1. Lock the appointment row.
        # -----------------------------------------------------
        cursor.execute("""
            SELECT
                appointment_id,
                patient_id,
                status
            FROM appointments
            WHERE appointment_id = %s
            FOR UPDATE
        """, (appointment_id,))

        appointment = cursor.fetchone()

        if not appointment:
            mysql.connection.rollback()
            return False, "Appointment not found."

        if appointment["status"] != "pending_approval":
            mysql.connection.rollback()
            return False, (
                "This appointment has already been processed "
                "or is no longer awaiting approval."
            )

        # -----------------------------------------------------
        # 2. Check whether a decision already exists.
        # -----------------------------------------------------
        cursor.execute("""
            SELECT approval_id
            FROM appointment_approval_history
            WHERE appointment_id = %s
            LIMIT 1
        """, (appointment_id,))

        existing_decision = cursor.fetchone()

        if existing_decision:
            mysql.connection.rollback()
            return False, "A decision already exists for this appointment."

        # -----------------------------------------------------
        # 3. Update appointment status.
        # -----------------------------------------------------
        new_status = (
            "confirmed"
            if decision == "Approved"
            else "rejected"
        )

        cursor.execute("""
            UPDATE appointments
            SET status = %s
            WHERE appointment_id = %s
              AND status = 'pending_approval'
        """, (new_status, appointment_id))

        if cursor.rowcount != 1:
            mysql.connection.rollback()
            return False, "Appointment status could not be updated."

        # -----------------------------------------------------
        # 4. Save approval history.
        # -----------------------------------------------------
        cursor.execute("""
            INSERT INTO appointment_approval_history (
                appointment_id,
                action_by,
                action_role,
                decision,
                decision_reason
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            appointment_id,
            user["user_id"],
            "super_admin",
            decision,
            decision_reason if decision == "Rejected" else None,
        ))

        # -----------------------------------------------------
        # 5. Find the patient's registered user account.
        # -----------------------------------------------------
        cursor.execute("""
            SELECT user_id
            FROM users
            WHERE patient_id = %s
              AND is_active = 1
            LIMIT 1
        """, (appointment["patient_id"],))

        patient_user = cursor.fetchone()

        # -----------------------------------------------------
        # 6. Create a patient notification.
        # -----------------------------------------------------
        if patient_user:

            if decision == "Approved":
                notification_title = "Appointment Approved"

                notification_message = (
                    f"Your appointment #{appointment_id} "
                    "has been approved and confirmed by MEDIVERA."
                )

            else:
                notification_title = "Appointment Rejected"

                notification_message = (
                    f"Your appointment #{appointment_id} "
                    "has been rejected by MEDIVERA. "
                    f"Reason: {decision_reason}"
                )

            cursor.execute("""
                INSERT INTO notifications (
                    user_id,
                    title,
                    message,
                    notification_type,
                    is_read
                )
                VALUES (%s, %s, %s, 'Appointment', 0)
            """, (
                patient_user["user_id"],
                notification_title,
                notification_message,
            ))

        else:
            current_app.logger.warning(
                "Appointment %s was processed, but no active user "
                "account was found for patient_id %s.",
                appointment_id,
                appointment["patient_id"],
            )

        # -----------------------------------------------------
        # 7. Commit all changes together.
        # -----------------------------------------------------
        mysql.connection.commit()

        return True, (
            f"Appointment #{appointment_id} "
            f"has been {decision.lower()} successfully."
        )

    except Exception:
        mysql.connection.rollback()

        current_app.logger.exception(
            "Appointment decision failed for appointment_id=%s",
            appointment_id,
        )

        return False, (
            "The appointment decision could not be saved. "
            "Please check the application logs."
        )

    finally:
        if cursor:
            cursor.close()


# =============================================================
# APPROVE APPOINTMENT
# POST /admin/approvals/appointments/<id>/approve
# =============================================================

@admin_bp.route(
    "/approvals/appointments/<int:appointment_id>/approve",
    methods=["POST"],
)
@login_required
def approve_appointment(appointment_id):

    if not _is_super_admin():
        abort(403)

    success, message = _process_appointment_decision(
        appointment_id=appointment_id,
        decision="Approved",
    )

    flash(
        message,
        "success" if success else "danger",
    )

    return redirect(url_for("admin.approvals"))


# =============================================================
# REJECT APPOINTMENT
# POST /admin/approvals/appointments/<id>/reject
# =============================================================

@admin_bp.route(
    "/approvals/appointments/<int:appointment_id>/reject",
    methods=["POST"],
)
@login_required
def reject_appointment(appointment_id):

    if not _is_super_admin():
        abort(403)

    reason = request.form.get("decision_reason", "").strip()

    if not reason:
        flash(
            "A rejection reason is required.",
            "danger",
        )
        return redirect(url_for("admin.approvals"))

    if len(reason) > 255:
        flash(
            "The rejection reason cannot exceed 255 characters.",
            "danger",
        )
        return redirect(url_for("admin.approvals"))

    success, message = _process_appointment_decision(
        appointment_id=appointment_id,
        decision="Rejected",
        decision_reason=reason,
    )

    flash(
        message,
        "success" if success else "danger",
    )

    return redirect(url_for("admin.approvals"))

# ============================================================
# MEDIVERA - STAFF LEAVE APPROVAL ENGINE
# ============================================================

def _process_leave_decision(leave_id, decision, remarks=None):

    if decision not in ("Approved", "Rejected"):
        return False, "Invalid leave decision."

    remarks = (remarks or "").strip()

    if decision == "Rejected" and not remarks:
        return False, "Please provide a rejection reason."

    if len(remarks) > 2000:
        return False, "Remarks cannot exceed 2000 characters."

    user = get_current_user()

    if not user:
        return False, "Your session has expired."

    cursor = None

    try:
        cursor = mysql.connection.cursor()
        mysql.connection.begin()

        cursor.execute("""
            SELECT leave_id, status
            FROM leave_applications
            WHERE leave_id = %s
            FOR UPDATE
        """, (leave_id,))

        leave = cursor.fetchone()

        if not leave:
            mysql.connection.rollback()
            return False, "Leave application not found."

        if leave["status"] != "Pending":
            mysql.connection.rollback()
            return False, "This leave application has already been processed."

        cursor.execute("""
            UPDATE leave_applications
            SET
                status = %s,
                reviewed_by = %s,
                reviewed_at = NOW(),
                admin_remarks = %s
            WHERE leave_id = %s
              AND status = 'Pending'
        """, (
            decision,
            user["user_id"],
            remarks or None,
            leave_id,
        ))

        if cursor.rowcount != 1:
            mysql.connection.rollback()
            return False, "Leave application could not be updated."

        mysql.connection.commit()

        return True, (
            f"Leave application #{leave_id} "
            f"has been {decision.lower()}."
        )

    except Exception:
        mysql.connection.rollback()

        current_app.logger.exception(
            "Leave decision failed for leave_id=%s",
            leave_id,
        )

        return False, "Unable to process the leave application."

    finally:
        if cursor:
            cursor.close()


@admin_bp.route(
    "/approvals/leaves/<int:leave_id>/approve",
    methods=["POST"],
)
@login_required
def approve_leave(leave_id):

    if not _is_super_admin():
        abort(403)

    success, message = _process_leave_decision(
        leave_id,
        "Approved",
    )

    flash(message, "success" if success else "danger")

    return redirect(url_for("admin.approvals"))


@admin_bp.route(
    "/approvals/leaves/<int:leave_id>/reject",
    methods=["POST"],
)
@login_required
def reject_leave(leave_id):

    if not _is_super_admin():
        abort(403)

    remarks = request.form.get("decision_reason", "").strip()

    success, message = _process_leave_decision(
        leave_id,
        "Rejected",
        remarks,
    )

    flash(message, "success" if success else "danger")

    return redirect(url_for("admin.approvals"))

# ============================================================
# MEDIVERA SUPER ADMIN
# REPORTS, ACTIVITY LOGS & SYSTEM SETTINGS
# Read-only implementation - preserves existing database
# ============================================================


# ------------------------------------------------------------
# 1. REPORTS & ANALYTICS
# URL: /admin/reports
# Endpoint: admin.reports
# ------------------------------------------------------------



# ------------------------------------------------------------
# 1. REPORTS & ANALYTICS
# URL: /admin/reports
# Endpoint: admin.reports
# ------------------------------------------------------------

@admin_bp.get("/reports")
@login_required
def reports():

    if not _is_super_admin():
        abort(403)

    # --------------------------------------------------------
    # A. OVERALL HOSPITAL STATISTICS
    # --------------------------------------------------------

    report_stats = fetch_one("""
        SELECT
            (SELECT COUNT(*) FROM patients)
                AS total_patients,

            (SELECT COUNT(*) FROM doctors)
                AS total_doctors,

            (SELECT COUNT(*) FROM appointments)
                AS total_appointments,

            (
                SELECT COUNT(*)
                FROM appointments
                WHERE status = 'pending_approval'
            ) AS pending_appointments,

            (
                SELECT COUNT(*)
                FROM appointments
                WHERE status = 'completed'
            ) AS completed_appointments,

            (
                SELECT COALESCE(SUM(total_amount), 0)
                FROM billing
                WHERE payment_status = 'paid'
            ) AS total_revenue
    """) or {}

    # --------------------------------------------------------
    # B. APPOINTMENT STATUS DISTRIBUTION
    # --------------------------------------------------------

    appointment_status = list(fetch_all("""
        SELECT
            status,
            COUNT(*) AS total
        FROM appointments
        GROUP BY status
        ORDER BY total DESC
    """))

    total_status_appointments = sum(
        int(row.get("total") or 0)
        for row in appointment_status
    )

    status_labels = []
    status_values = []

    for row in appointment_status:

        current_status = row.get("status") or "Unknown"
        current_total = int(row.get("total") or 0)

        row["percentage"] = (
            round(
                (current_total / total_status_appointments) * 100,
                1,
            )
            if total_status_appointments > 0
            else 0
        )

        status_labels.append(
            current_status.replace("_", " ").title()
        )

        status_values.append(current_total)

    # --------------------------------------------------------
    # C. MONTHLY APPOINTMENT STATISTICS
    # --------------------------------------------------------

    monthly_appointments = list(fetch_all("""
        SELECT
            YEAR(created_at) AS report_year,
            MONTH(created_at) AS report_month,
            COUNT(*) AS total
        FROM appointments
        WHERE created_at >= DATE_SUB(
            CURDATE(),
            INTERVAL 5 MONTH
        )
        GROUP BY
            YEAR(created_at),
            MONTH(created_at)
        ORDER BY
            report_year,
            report_month
    """))

    month_labels = [
        f"{int(row['report_year'])}-{int(row['report_month']):02d}"
        for row in monthly_appointments
    ]

    appointment_chart_values = [
        int(row.get("total") or 0)
        for row in monthly_appointments
    ]

    # --------------------------------------------------------
    # D. MONTHLY PAID REVENUE
    # --------------------------------------------------------

    monthly_revenue = list(fetch_all("""
        SELECT
            YEAR(payment_date) AS report_year,
            MONTH(payment_date) AS report_month,
            SUM(total_amount) AS revenue
        FROM billing
        WHERE payment_status = 'paid'
          AND payment_date IS NOT NULL
          AND payment_date >= DATE_SUB(
              CURDATE(),
              INTERVAL 5 MONTH
          )
        GROUP BY
            YEAR(payment_date),
            MONTH(payment_date)
        ORDER BY
            report_year,
            report_month
    """))

    revenue_labels = [
        f"{int(row['report_year'])}-{int(row['report_month']):02d}"
        for row in monthly_revenue
    ]

    revenue_chart_values = [
        float(row.get("revenue") or 0)
        for row in monthly_revenue
    ]

    # --------------------------------------------------------
    # E. DEPARTMENT PERFORMANCE
    # --------------------------------------------------------

    department_stats = list(fetch_all("""
        SELECT
            d.department_id,
            d.department_name,

            COUNT(DISTINCT doc.doctor_id)
                AS doctor_count,

            COUNT(DISTINCT a.appointment_id)
                AS appointment_count

        FROM departments d

        LEFT JOIN doctors doc
            ON doc.department_id = d.department_id

        LEFT JOIN appointments a
            ON a.doctor_id = doc.doctor_id

        GROUP BY
            d.department_id,
            d.department_name

        ORDER BY
            appointment_count DESC,
            d.department_name
    """))

    maximum_department_appointments = max(
        (
            int(row.get("appointment_count") or 0)
            for row in department_stats
        ),
        default=0,
    )

    for department in department_stats:

        department["activity_percentage"] = (
            round(
                (
                    int(department.get("appointment_count") or 0)
                    / maximum_department_appointments
                ) * 100,
                1,
            )
            if maximum_department_appointments > 0
            else 0
        )

    # --------------------------------------------------------
    # F. SUPER ADMIN SIDEBAR
    # --------------------------------------------------------

    nav_items = [
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

    # --------------------------------------------------------
    # G. RENDER REPORTS PAGE
    # --------------------------------------------------------

    return render_template(
        "admin/reports.html",

        user=get_current_user(),
        nav_items=nav_items,
        is_super_admin=True,

        report_stats=report_stats,

        appointment_status=appointment_status,
        status_labels=status_labels,
        status_values=status_values,

        month_labels=month_labels,
        appointment_chart_values=appointment_chart_values,

        revenue_labels=revenue_labels,
        revenue_chart_values=revenue_chart_values,

        department_stats=department_stats,
    )

# ------------------------------------------------------------
# 2. ACTIVITY LOGS
# URL: /admin/activity-logs
# Endpoint: admin.activity_logs
# ------------------------------------------------------------

@admin_bp.get("/activity-logs")
@login_required
def activity_logs():
    if not _is_super_admin():
        abort(403)

    search = request.args.get("search", "").strip()
    module = request.args.get("module", "").strip()
    page = request.args.get("page", 1, type=int)

    page = max(page or 1, 1)
    per_page = 20
    offset = (page - 1) * per_page

    conditions = []
    params = []

    if search:
        conditions.append("""
            (
                u.username LIKE %s
                OR al.action LIKE %s
                OR al.module LIKE %s
                OR al.description LIKE %s
                OR al.ip_address LIKE %s
            )
        """)
        search_value = f"%{search}%"
        params.extend([search_value] * 5)

    if module:
        conditions.append("al.module = %s")
        params.append(module)

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions else ""
    )

    count_result = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM activity_logs al
        LEFT JOIN users u
            ON u.user_id = al.user_id
        {where_clause}
        """,
        tuple(params),
    ) or {}

    total_records = int(count_result.get("total") or 0)
    total_pages = max(1, (total_records + per_page - 1) // per_page)

    # Keep requested page within the available range.
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    log_params = list(params)
    log_params.extend([per_page, offset])

    logs = fetch_all(
        f"""
        SELECT
            al.log_id,
            al.user_id,
            COALESCE(u.username, 'System') AS username,
            al.action,
            al.module,
            al.description,
            al.ip_address,
            al.created_at
        FROM activity_logs al
        LEFT JOIN users u
            ON u.user_id = al.user_id
        {where_clause}
        ORDER BY al.created_at DESC, al.log_id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(log_params),
    )

    modules = fetch_all("""
        SELECT DISTINCT module
        FROM activity_logs
        WHERE module IS NOT NULL
          AND module <> ''
        ORDER BY module
    """)

    summary = fetch_one("""
        SELECT
            COUNT(*) AS total_logs,
            COUNT(DISTINCT user_id) AS active_users,
            COUNT(DISTINCT module) AS tracked_modules
        FROM activity_logs
    """) or {}

    nav_items = [
        {"label": "System Overview", "icon": "layout-dashboard",
         "endpoint": "admin.super_dashboard"},
        {"label": "Administrators", "icon": "users",
         "endpoint": "admin.administrators"},
        {"label": "Roles & Permissions", "icon": "shield-check",
         "endpoint": "admin.roles_permissions"},
        {"label": "Approvals", "icon": "clipboard-check",
         "endpoint": "admin.approvals"},
        {"label": "Reports", "icon": "chart-no-axes-combined",
         "endpoint": "admin.reports"},
        {"label": "Activity Logs", "icon": "history",
         "endpoint": "admin.activity_logs"},
        {"label": "System Settings", "icon": "settings",
         "endpoint": "admin.system_settings"},

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

    return render_template(
        "admin/activity_logs.html",
        user=get_current_user(),
        nav_items=nav_items,
        is_super_admin=True,
        logs=logs,
        modules=modules,
        summary=summary,
        search=search,
        selected_module=module,
        page=page,
        total_pages=total_pages,
        total_records=total_records,
    )




# ------------------------------------------------------------
# 3. SYSTEM SETTINGS
# URL: /admin/system-settings
# GET: Display settings and system diagnostics.
# POST: Validate and persist settings.
# Super Admin only.
# ------------------------------------------------------------

def _system_settings_navigation():
    return [
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
    ]


@admin_bp.route("/system-settings", methods=["GET"])
@login_required
def system_settings():

    if not _is_super_admin():
        abort(403)

    config = current_app.config

    database_status = "Unavailable"
    cursor = None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        database_status = "Connected"

    except Exception:
        current_app.logger.exception(
            "System Settings: database connection check failed."
        )

    finally:
        if cursor is not None:
            cursor.close()

    settings = get_current_settings(current_app)

    return render_template(
        "admin/system_settings.html",
        user=get_current_user(),
        nav_items=_system_settings_navigation(),
        is_super_admin=True,
        settings=settings,
        database_status=database_status,
        config_name=config.get("APP_NAME", "MEDIVERA"),
    )


@admin_bp.route("/system-settings/save", methods=["POST"])
@login_required
def update_system_settings():

    if not _is_super_admin():
        abort(403)

    form_values = {
        "APP_NAME": request.form.get("APP_NAME", ""),
        "APP_FULL_NAME": request.form.get("APP_FULL_NAME", ""),
        "APP_TAGLINE": request.form.get("APP_TAGLINE", ""),
        "TIMEZONE": request.form.get("TIMEZONE", ""),

        "HOSPITAL_NAME": request.form.get("HOSPITAL_NAME", ""),
        "HOSPITAL_ADDRESS": request.form.get("HOSPITAL_ADDRESS", ""),
        "HOSPITAL_PHONE": request.form.get("HOSPITAL_PHONE", ""),
        "HOSPITAL_EMAIL": request.form.get("HOSPITAL_EMAIL", ""),

        "MAX_UPLOAD_MB": request.form.get("MAX_UPLOAD_MB", ""),
        "ITEMS_PER_PAGE": request.form.get("ITEMS_PER_PAGE", ""),
        "MAX_ITEMS_PER_PAGE": request.form.get(
            "MAX_ITEMS_PER_PAGE",
            "",
        ),
        "SESSION_DURATION_HOURS": request.form.get(
            "SESSION_DURATION_HOURS",
            "",
        ),
    }

    try:
        # First validate and normalize the submitted values.
        validated_settings = validate_settings(
            form_values,
            current_app.config,
        )

        # Save only validated settings.
        save_persistent_settings(
            current_app,
            validated_settings,
        )

        user = get_current_user()
        user_id = user.get("user_id") if user else None

        log_activity(
            action="System Settings Updated",
            module="System Settings",
            description=(
                "Super Admin updated application, hospital profile, "
                "upload, pagination or session settings."
            ),
            user_id=user_id,
        )

        flash(
            "System settings have been saved successfully.",
            "success",
        )

    except (ValueError, TypeError) as error:

        flash(str(error), "danger")

    except Exception:

        current_app.logger.exception(
            "Unable to save MEDIVERA system settings."
        )

        flash(
            "Settings could not be saved. "
            "Please check the application logs.",
            "danger",
        )

    return redirect(
        url_for("admin.system_settings")
    )

# ============================================================
# MEDIVERA SUPER ADMIN â€” PATIENT MANAGEMENT
# Uses existing patients table. No schema changes.
# ============================================================

PATIENT_BLOOD_GROUPS = (
    "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"
)

PATIENT_GENDERS = (
    "Male", "Female", "Other", "Prefer not to say"
)


def _patient_admin_access():
    """Allow only Super Admin to manage all patient records."""
    user = get_current_user()

    if not user:
        abort(403)

    if "super_admin" not in get_user_roles(user["user_id"]):
        abort(403)

    return user


def _patient_admin_navigation():
    return [
        {
            "label": "System Overview",
            "icon": "layout-dashboard",
            "endpoint": "admin.super_dashboard",
        },
        {
            "label": "Patient Management",
            "icon": "users",
            "endpoint": "admin.patients",
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
            "icon": "alert-circle",
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


def _validate_patient_form(form):
    """Validate patient data against the existing patients schema."""

    data = {
        "first_name": form.get("first_name", "").strip(),
        "last_name": form.get("last_name", "").strip(),
        "date_of_birth": form.get("date_of_birth", "").strip(),
        "gender": form.get("gender", "").strip(),
        "blood_group": form.get("blood_group", "").strip(),
        "phone": form.get("phone", "").strip(),
        "email": form.get("email", "").strip() or None,
        "address": form.get("address", "").strip() or None,
        "emergency_contact_name": (
            form.get("emergency_contact_name", "").strip() or None
        ),
        "emergency_contact_phone": (
            form.get("emergency_contact_phone", "").strip() or None
        ),
    }

    errors = []

    if not data["first_name"] or len(data["first_name"]) > 50:
        errors.append("First name is required and must be at most 50 characters.")

    if not data["last_name"] or len(data["last_name"]) > 50:
        errors.append("Last name is required and must be at most 50 characters.")

    try:
        dob = datetime.strptime(
            data["date_of_birth"], "%Y-%m-%d"
        ).date()

        if dob >= datetime.now().date():
            errors.append("Date of birth must be in the past.")

    except (ValueError, TypeError):
        errors.append("Please provide a valid date of birth.")

    if data["gender"] not in PATIENT_GENDERS:
        errors.append("Please select a valid gender.")

    # The database enum does not include "I don't know".
    # Store an unknown blood group as NULL.
    if data["blood_group"] in ("", "unknown", "I don't know"):
        data["blood_group"] = None
    elif data["blood_group"] not in PATIENT_BLOOD_GROUPS:
        errors.append("Please select a valid blood group.")

    if not data["phone"] or len(data["phone"]) > 15:
        errors.append("A valid phone number is required (maximum 15 characters).")

    if data["email"] and len(data["email"]) > 100:
        errors.append("Email cannot exceed 100 characters.")

    if data["address"] and len(data["address"]) > 255:
        errors.append("Address cannot exceed 255 characters.")

    if data["emergency_contact_name"] and len(data["emergency_contact_name"]) > 100:
        errors.append("Emergency contact name cannot exceed 100 characters.")

    if data["emergency_contact_phone"] and len(data["emergency_contact_phone"]) > 15:
        errors.append("Emergency contact phone cannot exceed 15 characters.")

    return data, errors

def _patient_phone_or_email_exists(data, exclude_patient_id=None):
    query = """
        SELECT patient_id
        FROM patients
        WHERE (
            phone = %s
    """
    params = [data["phone"]]

    if data["email"]:
        query += " OR email = %s"
        params.append(data["email"])

    query += ")"

    if exclude_patient_id is not None:
        query += " AND patient_id <> %s"
        params.append(exclude_patient_id)

    return fetch_one(query, tuple(params))


def _patient_form_context(user, patient=None):
    return {
        "user": user,
        "nav_items": _patient_admin_navigation(),
        "is_super_admin": True,
        "patient": patient,
        "blood_groups": PATIENT_BLOOD_GROUPS,
        "genders": PATIENT_GENDERS,
    }
def _patient_form_context(user, patient=None):
    return {
        "user": user,
        "nav_items": _patient_admin_navigation(),
        "is_super_admin": True,
        "patient": patient,
        "blood_groups": PATIENT_BLOOD_GROUPS,
        "genders": PATIENT_GENDERS,
        "today": datetime.now().date().isoformat(),
    }


# ------------------------------------------------------------
# PATIENT DIRECTORY
# GET /admin/patients
# ------------------------------------------------------------

@admin_bp.get("/patients")
@login_required
def patients():

    user = _patient_admin_access()

    search = request.args.get("search", "").strip()
    page = max(request.args.get("page", 1, type=int) or 1, 1)

    per_page = 15
    offset = (page - 1) * per_page

    conditions = []
    params = []

    if search:
        conditions.append("""
            (
                CAST(patient_id AS CHAR) LIKE %s
                OR first_name LIKE %s
                OR last_name LIKE %s
                OR phone LIKE %s
                OR email LIKE %s
            )
        """)

        value = f"%{search}%"
        params.extend([value] * 5)

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions else ""
    )

    count_row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM patients
        {where_clause}
        """,
        tuple(params),
    ) or {}

    total_records = int(count_row.get("total") or 0)
    total_pages = max(
        1,
        (total_records + per_page - 1) // per_page
    )

    page = min(page, total_pages)
    offset = (page - 1) * per_page

    patient_rows = fetch_all(
        f"""
        SELECT
            p.patient_id,
            p.first_name,
            p.last_name,
            p.date_of_birth,
            p.gender,
            p.blood_group,
            p.phone,
            p.email,
            p.created_at,
            u.user_id AS linked_user_id,
            u.username AS linked_username
        FROM patients p
        LEFT JOIN users u
            ON u.patient_id = p.patient_id
        {where_clause}
        ORDER BY p.created_at DESC, p.patient_id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params) + (per_page, offset),
    )

    summary = fetch_one("""
        SELECT
            COUNT(*) AS total_patients,
            COUNT(DISTINCT gender) AS gender_categories,
            COUNT(DISTINCT blood_group) AS blood_group_categories
        FROM patients
    """) or {}

    return render_template(
        "admin/patients.html",
        user=user,
        nav_items=_patient_admin_navigation(),
        is_super_admin=True,
        patients=patient_rows,
        summary=summary,
        search=search,
        page=page,
        total_pages=total_pages,
        total_records=total_records,
    )


# ------------------------------------------------------------
# ADD PATIENT
# GET /admin/patients/add
# POST /admin/patients/add
# ------------------------------------------------------------

@admin_bp.route(
    "/patients/add",
    methods=["GET", "POST"],
)
@login_required
def add_patient():

    user = _patient_admin_access()

    if request.method == "GET":
        return render_template(
            "admin/patient_form.html",
            **_patient_form_context(user),
            page_heading="Register New Patient",
            form_action=url_for("admin.add_patient"),
        )

    data, errors = _validate_patient_form(request.form)

    if errors:
        for error in errors:
            flash(error, "danger")

        return render_template(
            "admin/patient_form.html",
            **_patient_form_context(user, data),
            page_heading="Register New Patient",
            form_action=url_for("admin.add_patient"),
        ), 400

    cursor = None

    try:
        if _patient_phone_or_email_exists(data):
            flash(
                "A patient with this phone number or email already exists.",
                "danger",
            )

            return render_template(
                "admin/patient_form.html",
                **_patient_form_context(user, data),
                page_heading="Register New Patient",
                form_action=url_for("admin.add_patient"),
            ), 409

        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            INSERT INTO patients (
                first_name,
                last_name,
                date_of_birth,
                gender,
                blood_group,
                phone,
                email,
                address,
                emergency_contact_name,
                emergency_contact_phone
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            (
                data["first_name"],
                data["last_name"],
                data["date_of_birth"],
                data["gender"],
                data["blood_group"],
                data["phone"],
                data["email"],
                data["address"],
                data["emergency_contact_name"],
                data["emergency_contact_phone"],
            ),
        )

        patient_id = cursor.lastrowid
        mysql.connection.commit()

        flash(
            f"Patient registered successfully. Patient ID: {patient_id}",
            "success",
        )

        return redirect(
            url_for(
                "admin.patient_detail",
                patient_id=patient_id,
            )
        )

    except Exception:
        mysql.connection.rollback()

        current_app.logger.exception(
            "Super Admin patient registration failed."
        )

        flash(
            "Patient registration failed. Check the application log.",
            "danger",
        )

        return render_template(
            "admin/patient_form.html",
            **_patient_form_context(user, data),
            page_heading="Register New Patient",
            form_action=url_for("admin.add_patient"),
        ), 500

    finally:
        if cursor:
            cursor.close()


# ------------------------------------------------------------
# PATIENT PROFILE
# GET /admin/patients/<patient_id>
# ------------------------------------------------------------

@admin_bp.get("/patients/<int:patient_id>")
@login_required
def patient_detail(patient_id):

    user = _patient_admin_access()

    patient = fetch_one(
        """
        SELECT
            p.*,
            u.user_id AS linked_user_id,
            u.username AS linked_username,
            u.email AS account_email,
            u.is_active AS account_is_active
        FROM patients p
        LEFT JOIN users u
            ON u.patient_id = p.patient_id
        WHERE p.patient_id = %s
        LIMIT 1
        """,
        (patient_id,),
    )

    if not patient:
        abort(404)

    appointments_list = fetch_all(
        """
        SELECT
            a.appointment_id,
            a.appointment_date,
            a.appointment_time,
            a.reason,
            a.status,
            d.doctor_name
        FROM appointments a
        LEFT JOIN doctors d
            ON d.doctor_id = a.doctor_id
        WHERE a.patient_id = %s
        ORDER BY a.appointment_date DESC,
                 a.appointment_time DESC
        LIMIT 10
        """,
        (patient_id,),
    )

    admissions_list = fetch_all(
        """
        SELECT
            admission_id,
            room_id,
            admission_date,
            discharge_date,
            admission_reason,
            admission_status
        FROM admissions
        WHERE patient_id = %s
        ORDER BY admission_date DESC
        LIMIT 10
        """,
        (patient_id,),
    )

    return render_template(
        "admin/patient_detail.html",
        user=user,
        nav_items=_patient_admin_navigation(),
        is_super_admin=True,
        patient=patient,
        appointments=appointments_list,
        admissions=admissions_list,
    )


# ------------------------------------------------------------
# EDIT PATIENT
# GET /admin/patients/<patient_id>/edit
# POST /admin/patients/<patient_id>/edit
# ------------------------------------------------------------

@admin_bp.route(
    "/patients/<int:patient_id>/edit",
    methods=["GET", "POST"],
)
@login_required
def edit_patient(patient_id):

    user = _patient_admin_access()

    existing = fetch_one(
        """
        SELECT *
        FROM patients
        WHERE patient_id = %s
        LIMIT 1
        """,
        (patient_id,),
    )

    if not existing:
        abort(404)

    if request.method == "GET":
        return render_template(
            "admin/patient_form.html",
            **_patient_form_context(user, existing),
            page_heading="Edit Patient",
            form_action=url_for(
                "admin.edit_patient",
                patient_id=patient_id,
            ),
        )

    data, errors = _validate_patient_form(request.form)

    if errors:
        for error in errors:
            flash(error, "danger")

        data["patient_id"] = patient_id

        return render_template(
            "admin/patient_form.html",
            **_patient_form_context(user, data),
            page_heading="Edit Patient",
            form_action=url_for(
                "admin.edit_patient",
                patient_id=patient_id,
            ),
        ), 400

    cursor = None

    try:
        if _patient_phone_or_email_exists(
            data,
            exclude_patient_id=patient_id,
        ):
            flash(
                "Another patient already uses this phone number or email.",
                "danger",
            )

            return render_template(
                "admin/patient_form.html",
                **_patient_form_context(user, data),
                page_heading="Edit Patient",
                form_action=url_for(
                    "admin.edit_patient",
                    patient_id=patient_id,
                ),
            ), 409

        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            UPDATE patients
            SET
                first_name = %s,
                last_name = %s,
                date_of_birth = %s,
                gender = %s,
                blood_group = %s,
                phone = %s,
                email = %s,
                address = %s,
                emergency_contact_name = %s,
                emergency_contact_phone = %s
            WHERE patient_id = %s
            """,
            (
                data["first_name"],
                data["last_name"],
                data["date_of_birth"],
                data["gender"],
                data["blood_group"],
                data["phone"],
                data["email"],
                data["address"],
                data["emergency_contact_name"],
                data["emergency_contact_phone"],
                patient_id,
            ),
        )

        mysql.connection.commit()

        flash("Patient details updated successfully.", "success")

        return redirect(
            url_for(
                "admin.patient_detail",
                patient_id=patient_id,
            )
        )

    except Exception:
        mysql.connection.rollback()

        current_app.logger.exception(
            "Patient update failed for patient_id=%s",
            patient_id,
        )

        flash(
            "Patient details could not be updated.",
            "danger",
        )

        return render_template(
            "admin/patient_form.html",
            **_patient_form_context(user, data),
            page_heading="Edit Patient",
            form_action=url_for(
                "admin.edit_patient",
                patient_id=patient_id,
            ),
        ), 500

    finally:
        if cursor:
            cursor.close()

# ============================================================
# MEDIVERA - DOCTOR MANAGEMENT
# Super Admin only | Existing database schema
# ============================================================

from decimal import Decimal, InvalidOperation


def _doctor_admin_access():
    user = get_current_user()
    if not user:
        abort(403)

    if "super_admin" not in get_user_roles(user["user_id"]):
        abort(403)

    return user


def _doctor_admin_navigation():
    return [
        {"label": "System Overview", "endpoint": "admin.super_dashboard", "icon": "dashboard"},
        {"label": "Patient Management", "endpoint": "admin.patients", "icon": "patients"},
        {"label": "Doctor Management", "endpoint": "admin.doctors", "icon": "doctors"},
    ]


def _doctor_form_context(user, doctor=None, form_data=None):
    departments = fetch_all(
        "SELECT department_id, department_name "
        "FROM departments ORDER BY department_name"
    )

    return {
        "user": user,
        "doctor": doctor,
        "form_data": form_data or {},
        "departments": departments,
        "nav_items": _doctor_admin_navigation(),
        "is_super_admin": True,
        "availability_options": ("available", "unavailable"),
    }


def _doctor_validate_form(form):
    errors = {}

    name = (form.get("doctor_name") or "").strip()
    specialization = (form.get("specialization") or "").strip() or None
    phone = (form.get("phone") or "").strip() or None
    email = (form.get("email") or "").strip().lower() or None
    department_raw = (form.get("department_id") or "").strip()
    fee_raw = (form.get("consultation_fee") or "").strip()
    experience_raw = (form.get("experience_years") or "0").strip()
    availability = (form.get("availability_status") or "available").strip()

    if not name or len(name) > 100:
        errors["doctor_name"] = "Enter a doctor name (maximum 100 characters)."

    try:
        department_id = int(department_raw)
        if not fetch_one(
            "SELECT department_id FROM departments WHERE department_id = %s",
            (department_id,)
        ):
            errors["department_id"] = "Select a valid department."
    except (TypeError, ValueError):
        department_id = None
        errors["department_id"] = "Select a valid department."

    try:
        fee = Decimal(fee_raw)
        if not fee.is_finite() or fee < 0 or fee > Decimal("99999999.99"):
            raise InvalidOperation
        fee = fee.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        fee = None
        errors["consultation_fee"] = "Enter a valid non-negative consultation fee."

    try:
        experience = int(experience_raw)
        if experience < 0 or experience > 100:
            raise ValueError
    except ValueError:
        experience = 0
        errors["experience_years"] = "Experience must be between 0 and 100 years."

    if phone and (len(phone) > 15 or not phone.replace("+", "").replace("-", "").replace(" ", "").isdigit()):
        errors["phone"] = "Enter a valid phone number (maximum 15 characters)."

    if email and (len(email) > 100 or "@" not in email):
        errors["email"] = "Enter a valid email address."

    if availability not in ("available", "unavailable"):
        errors["availability_status"] = "Select a valid availability status."

    data = {
        "doctor_name": name,
        "department_id": department_id,
        "specialization": specialization,
        "phone": phone,
        "email": email,
        "consultation_fee": fee,
        "experience_years": experience,
        "availability_status": availability,
    }

    return data, errors


def _doctor_duplicate_errors(data, exclude_id=None):
    errors = {}

    for field, label in (("phone", "phone number"), ("email", "email address")):
        value = data.get(field)
        if not value:
            continue

        sql = f"SELECT doctor_id FROM doctors WHERE {field} = %s"
        params = [value]

        if exclude_id is not None:
            sql += " AND doctor_id <> %s"
            params.append(exclude_id)

        if fetch_one(sql, tuple(params)):
            errors[field] = f"That {label} is already assigned to another doctor."

    return errors


@admin_bp.route("/doctors", methods=["GET"])
@login_required
def doctors():
    user = _doctor_admin_access()

    search = (request.args.get("q") or "").strip()
    department_filter = (request.args.get("department") or "").strip()
    availability_filter = (request.args.get("availability") or "").strip()

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    per_page = 10
    conditions = ["1 = 1"]
    params = []

    if search:
        conditions.append(
            "(d.doctor_name LIKE %s OR d.specialization LIKE %s "
            "OR d.phone LIKE %s OR d.email LIKE %s)"
        )
        term = f"%{search}%"
        params.extend([term, term, term, term])

    if department_filter.isdigit():
        conditions.append("d.department_id = %s")
        params.append(int(department_filter))

    if availability_filter in ("available", "unavailable"):
        conditions.append("d.availability_status = %s")
        params.append(availability_filter)

    where_sql = " AND ".join(conditions)

    summary = fetch_one("""
        SELECT
            COUNT(*) AS total_doctors,
            COALESCE(SUM(availability_status = 'available'), 0) AS available_doctors,
            COALESCE(SUM(availability_status = 'unavailable'), 0) AS unavailable_doctors
        FROM doctors
    """) or {}

    count_row = fetch_one(
        f"SELECT COUNT(*) AS total FROM doctors d WHERE {where_sql}",
        tuple(params)
    ) or {}

    total_records = int(count_row.get("total") or 0)
    total_pages = max(1, (total_records + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    doctor_rows = fetch_all(
        f"""
        SELECT d.doctor_id, d.user_id, d.doctor_name,
               d.specialization, d.phone, d.email,
               d.consultation_fee, d.experience_years,
               d.availability_status, d.department_id,
               dep.department_name
        FROM doctors d
        LEFT JOIN departments dep
            ON dep.department_id = d.department_id
        WHERE {where_sql}
        ORDER BY d.doctor_id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [per_page, offset])
    )

    departments = fetch_all(
        "SELECT department_id, department_name "
        "FROM departments ORDER BY department_name"
    )

    return render_template(
        "admin/doctors.html",
        user=user,
        nav_items=_doctor_admin_navigation(),
        is_super_admin=True,
        doctors=doctor_rows,
        summary=summary,
        departments=departments,
        search=search,
        department_filter=department_filter,
        availability_filter=availability_filter,
        page=page,
        total_pages=total_pages,
        total_records=total_records,
    )


@admin_bp.route("/doctors/add", methods=["GET", "POST"])
@login_required
def add_doctor():
    user = _doctor_admin_access()

    if request.method == "POST":
        data, errors = _doctor_validate_form(request.form)
        errors.update(_doctor_duplicate_errors(data))

        if errors:
            for message in errors.values():
                flash(message, "danger")
            return render_template(
                "admin/doctor_form.html",
                **_doctor_form_context(user, form_data=request.form),
                page_heading="Add Doctor",
                form_action=url_for("admin.add_doctor"),
            ), 400

        cursor = None
        try:
            cursor = mysql.connection.cursor()
            cursor.execute("""
                INSERT INTO doctors (
                    department_id, doctor_name, specialization,
                    phone, email, consultation_fee,
                    experience_years, availability_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data["department_id"],
                data["doctor_name"],
                data["specialization"],
                data["phone"],
                data["email"],
                data["consultation_fee"],
                data["experience_years"],
                data["availability_status"],
            ))
            doctor_id = cursor.lastrowid
            mysql.connection.commit()
            flash("Doctor record created successfully.", "success")
            return redirect(url_for("admin.doctor_detail", doctor_id=doctor_id))

        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Unable to create doctor")
            flash("The doctor record could not be saved. Check the details and try again.", "danger")
        finally:
            if cursor:
                cursor.close()

    return render_template(
        "admin/doctor_form.html",
        **_doctor_form_context(user),
        page_heading="Add Doctor",
        form_action=url_for("admin.add_doctor"),
    )


@admin_bp.route("/doctors/<int:doctor_id>", methods=["GET"])
@login_required
def doctor_detail(doctor_id):
    user = _doctor_admin_access()

    doctor = fetch_one("""
        SELECT d.*, dep.department_name,
               u.username AS account_username,
               u.email AS account_email,
               u.is_active AS account_active
        FROM doctors d
        LEFT JOIN departments dep
            ON dep.department_id = d.department_id
        LEFT JOIN users u
            ON u.user_id = d.user_id
        WHERE d.doctor_id = %s
    """, (doctor_id,))

    if not doctor:
        abort(404)

    appointments = fetch_all("""
        SELECT a.appointment_id, a.appointment_date,
               a.appointment_time, a.status,
               p.first_name, p.last_name
        FROM appointments a
        LEFT JOIN patients p ON p.patient_id = a.patient_id
        WHERE a.doctor_id = %s
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        LIMIT 10
    """, (doctor_id,))

    return render_template(
        "admin/doctor_detail.html",
        user=user,
        nav_items=_doctor_admin_navigation(),
        is_super_admin=True,
        doctor=doctor,
        appointments=appointments,
    )


@admin_bp.route("/doctors/<int:doctor_id>/edit", methods=["GET", "POST"])
@login_required
def edit_doctor(doctor_id):
    user = _doctor_admin_access()

    doctor = fetch_one(
        "SELECT * FROM doctors WHERE doctor_id = %s",
        (doctor_id,)
    )

    if not doctor:
        abort(404)

    if request.method == "POST":
        data, errors = _doctor_validate_form(request.form)
        errors.update(_doctor_duplicate_errors(data, exclude_id=doctor_id))

        if errors:
            for message in errors.values():
                flash(message, "danger")
            return render_template(
                "admin/doctor_form.html",
                **_doctor_form_context(user, doctor=doctor, form_data=request.form),
                page_heading="Edit Doctor",
                form_action=url_for("admin.edit_doctor", doctor_id=doctor_id),
            ), 400

        cursor = None
        try:
            cursor = mysql.connection.cursor()
            cursor.execute("""
                UPDATE doctors
                SET department_id = %s,
                    doctor_name = %s,
                    specialization = %s,
                    phone = %s,
                    email = %s,
                    consultation_fee = %s,
                    experience_years = %s,
                    availability_status = %s
                WHERE doctor_id = %s
            """, (
                data["department_id"],
                data["doctor_name"],
                data["specialization"],
                data["phone"],
                data["email"],
                data["consultation_fee"],
                data["experience_years"],
                data["availability_status"],
                doctor_id,
            ))
            mysql.connection.commit()
            flash("Doctor record updated successfully.", "success")
            return redirect(url_for("admin.doctor_detail", doctor_id=doctor_id))

        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Unable to update doctor")
            flash("The doctor record could not be updated.", "danger")
        finally:
            if cursor:
                cursor.close()

    return render_template(
        "admin/doctor_form.html",
        **_doctor_form_context(user, doctor=doctor),
        page_heading="Edit Doctor",
        form_action=url_for("admin.edit_doctor", doctor_id=doctor_id),
    )

# ============================================================
# MEDIVERA - STAFF MANAGEMENT
# Super Admin only | Existing staff and departments tables
# ============================================================

def _staff_admin_access():
    user = get_current_user()
    if not user:
        abort(403)

    if "super_admin" not in get_user_roles(user["user_id"]):
        abort(403)

    return user


def _staff_admin_navigation():
    items = _patient_admin_navigation()

    doctor_item = {
        "label": "Doctor Management",
        "icon": "stethoscope",
        "endpoint": "admin.doctors",
    }
    staff_item = {
        "label": "Staff Management",
        "icon": "users",
        "endpoint": "admin.staff",
    }

    if not any(item.get("endpoint") == "admin.doctors" for item in items):
        patient_index = next(
            (i for i, item in enumerate(items)
             if item.get("endpoint") == "admin.patients"),
            -1
        )
        items.insert(patient_index + 1 if patient_index >= 0 else 1, doctor_item)

    if not any(item.get("endpoint") == "admin.staff" for item in items):
        doctor_index = next(
            (i for i, item in enumerate(items)
             if item.get("endpoint") == "admin.doctors"),
            -1
        )
        items.insert(doctor_index + 1 if doctor_index >= 0 else 2, staff_item)

    return items


def _staff_form_context(user, staff_member=None, form_data=None):
    departments = fetch_all("""
        SELECT department_id, department_name
        FROM departments
        ORDER BY department_name
    """)

    return {
        "user": user,
        "staff_member": staff_member,
        "form_data": form_data or {},
        "departments": departments,
        "nav_items": _staff_admin_navigation(),
        "is_super_admin": True,
        "gender_options": ("male", "female", "other"),
        "employment_options": ("active", "inactive"),
        "today": datetime.today().strftime("%Y-%m-%d"),
    }


def _staff_validate_form(form):
    errors = {}

    staff_name = (form.get("staff_name") or "").strip()
    gender = (form.get("gender") or "").strip().lower() or None
    phone = (form.get("phone") or "").strip() or None
    email = (form.get("email") or "").strip().lower() or None
    designation = (form.get("designation") or "").strip()
    department_raw = (form.get("department_id") or "").strip()
    salary_raw = (form.get("salary") or "").strip()
    joining_date_raw = (form.get("joining_date") or "").strip()
    employment_status = (
        form.get("employment_status") or "active"
    ).strip().lower()

    if not staff_name or len(staff_name) > 100:
        errors["staff_name"] = "Enter a staff name (maximum 100 characters)."

    if gender and gender not in ("male", "female", "other"):
        errors["gender"] = "Select a valid gender."

    if not designation or len(designation) > 100:
        errors["designation"] = "Enter a designation (maximum 100 characters)."

    department_id = None
    if department_raw:
        try:
            department_id = int(department_raw)
            if not fetch_one(
                "SELECT department_id FROM departments WHERE department_id = %s",
                (department_id,)
            ):
                errors["department_id"] = "Select a valid department."
        except (TypeError, ValueError):
            errors["department_id"] = "Select a valid department."

    if phone:
        normalized_phone = (
            phone.replace("+", "")
                 .replace("-", "")
                 .replace(" ", "")
                 .replace("(", "")
                 .replace(")", "")
        )
        if len(phone) > 15 or not normalized_phone.isdigit():
            errors["phone"] = "Enter a valid phone number (maximum 15 characters)."

    if email:
        if len(email) > 100 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            errors["email"] = "Enter a valid email address."

    salary = None
    if salary_raw:
        try:
            salary = Decimal(salary_raw)
            if (
                not salary.is_finite()
                or salary < 0
                or salary > Decimal("99999999.99")
            ):
                raise InvalidOperation
            salary = salary.quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            errors["salary"] = "Enter a valid non-negative salary."

    joining_date = None
    if joining_date_raw:
        try:
            joining_date = datetime.strptime(
                joining_date_raw, "%Y-%m-%d"
            ).date()
        except ValueError:
            errors["joining_date"] = "Enter a valid joining date."

    if employment_status not in ("active", "inactive"):
        errors["employment_status"] = "Select a valid employment status."

    data = {
        "staff_name": staff_name,
        "gender": gender,
        "phone": phone,
        "email": email,
        "designation": designation,
        "department_id": department_id,
        "salary": salary,
        "joining_date": joining_date,
        "employment_status": employment_status,
    }

    return data, errors


def _staff_duplicate_errors(data, exclude_id=None):
    errors = {}

    for field, label in (
        ("phone", "phone number"),
        ("email", "email address"),
    ):
        value = data.get(field)
        if not value:
            continue

        sql = f"SELECT staff_id FROM staff WHERE {field} = %s"
        params = [value]

        if exclude_id is not None:
            sql += " AND staff_id <> %s"
            params.append(exclude_id)

        if fetch_one(sql, tuple(params)):
            errors[field] = (
                f"That {label} is already assigned to another staff member."
            )

    return errors


@admin_bp.route("/staff", methods=["GET"])
@login_required
def staff():
    user = _staff_admin_access()

    search = (request.args.get("q") or "").strip()
    department_filter = (request.args.get("department") or "").strip()
    status_filter = (request.args.get("status") or "").strip().lower()

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    per_page = 10
    conditions = ["1 = 1"]
    params = []

    if search:
        conditions.append("""
            (s.staff_name LIKE %s
             OR s.designation LIKE %s
             OR s.phone LIKE %s
             OR s.email LIKE %s)
        """)
        term = f"%{search}%"
        params.extend([term, term, term, term])

    if department_filter.isdigit():
        conditions.append("s.department_id = %s")
        params.append(int(department_filter))

    if status_filter in ("active", "inactive"):
        conditions.append("s.employment_status = %s")
        params.append(status_filter)

    where_sql = " AND ".join(conditions)

    summary = fetch_one("""
        SELECT
            COUNT(*) AS total_staff,
            COALESCE(SUM(employment_status = 'active'), 0) AS active_staff,
            COALESCE(SUM(employment_status = 'inactive'), 0) AS inactive_staff
        FROM staff
    """) or {}

    count_row = fetch_one(
        f"SELECT COUNT(*) AS total FROM staff s WHERE {where_sql}",
        tuple(params)
    ) or {}

    total_records = int(count_row.get("total") or 0)
    total_pages = max(1, (total_records + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    staff_rows = fetch_all(
        f"""
        SELECT
            s.staff_id,
            s.staff_name,
            s.gender,
            s.phone,
            s.email,
            s.designation,
            s.department_id,
            s.salary,
            s.joining_date,
            s.employment_status,
            s.user_id,
            d.department_name,
            u.username AS account_username
        FROM staff s
        LEFT JOIN departments d
            ON d.department_id = s.department_id
        LEFT JOIN users u
            ON u.user_id = s.user_id
        WHERE {where_sql}
        ORDER BY s.staff_id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [per_page, offset])
    )

    departments = fetch_all("""
        SELECT department_id, department_name
        FROM departments
        ORDER BY department_name
    """)

    return render_template(
        "admin/staff.html",
        user=user,
        nav_items=_staff_admin_navigation(),
        is_super_admin=True,
        staff_members=staff_rows,
        summary=summary,
        departments=departments,
        search=search,
        department_filter=department_filter,
        status_filter=status_filter,
        page=page,
        total_pages=total_pages,
        total_records=total_records,
    )


@admin_bp.route("/staff/add", methods=["GET", "POST"])
@login_required
def add_staff():
    user = _staff_admin_access()

    if request.method == "POST":
        data, errors = _staff_validate_form(request.form)
        errors.update(_staff_duplicate_errors(data))

        if errors:
            for message in errors.values():
                flash(message, "danger")

            return render_template(
                "admin/staff_form.html",
                **_staff_form_context(user, form_data=request.form),
                page_heading="Add Staff Member",
                form_action=url_for("admin.add_staff"),
            ), 400

        cursor = None
        try:
            cursor = mysql.connection.cursor()
            cursor.execute("""
                INSERT INTO staff (
                    staff_name, gender, phone, email,
                    designation, department_id, salary,
                    joining_date, employment_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data["staff_name"],
                data["gender"],
                data["phone"],
                data["email"],
                data["designation"],
                data["department_id"],
                data["salary"],
                data["joining_date"],
                data["employment_status"],
            ))

            staff_id = cursor.lastrowid
            mysql.connection.commit()

            flash("Staff member added successfully.", "success")
            return redirect(
                url_for("admin.staff_detail", staff_id=staff_id)
            )

        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Unable to create staff record")
            flash("The staff record could not be saved. Please check the details.", "danger")
        finally:
            if cursor:
                cursor.close()

    return render_template(
        "admin/staff_form.html",
        **_staff_form_context(user),
        page_heading="Add Staff Member",
        form_action=url_for("admin.add_staff"),
    )


@admin_bp.route("/staff/<int:staff_id>", methods=["GET"])
@login_required
def staff_detail(staff_id):
    user = _staff_admin_access()

    staff_member = fetch_one("""
        SELECT
            s.*,
            d.department_name,
            u.username AS account_username,
            u.email AS account_email,
            u.is_active AS account_active
        FROM staff s
        LEFT JOIN departments d
            ON d.department_id = s.department_id
        LEFT JOIN users u
            ON u.user_id = s.user_id
        WHERE s.staff_id = %s
    """, (staff_id,))

    if not staff_member:
        abort(404)

    return render_template(
        "admin/staff_detail.html",
        user=user,
        nav_items=_staff_admin_navigation(),
        is_super_admin=True,
        staff_member=staff_member,
    )


@admin_bp.route("/staff/<int:staff_id>/edit", methods=["GET", "POST"])
@login_required
def edit_staff(staff_id):
    user = _staff_admin_access()

    staff_member = fetch_one(
        "SELECT * FROM staff WHERE staff_id = %s",
        (staff_id,)
    )

    if not staff_member:
        abort(404)

    if request.method == "POST":
        data, errors = _staff_validate_form(request.form)
        errors.update(_staff_duplicate_errors(data, exclude_id=staff_id))

        if errors:
            for message in errors.values():
                flash(message, "danger")

            return render_template(
                "admin/staff_form.html",
                **_staff_form_context(
                    user,
                    staff_member=staff_member,
                    form_data=request.form,
                ),
                page_heading="Edit Staff Member",
                form_action=url_for("admin.edit_staff", staff_id=staff_id),
            ), 400

        cursor = None
        try:
            cursor = mysql.connection.cursor()
            cursor.execute("""
                UPDATE staff
                SET staff_name = %s,
                    gender = %s,
                    phone = %s,
                    email = %s,
                    designation = %s,
                    department_id = %s,
                    salary = %s,
                    joining_date = %s,
                    employment_status = %s
                WHERE staff_id = %s
            """, (
                data["staff_name"],
                data["gender"],
                data["phone"],
                data["email"],
                data["designation"],
                data["department_id"],
                data["salary"],
                data["joining_date"],
                data["employment_status"],
                staff_id,
            ))

            mysql.connection.commit()
            flash("Staff record updated successfully.", "success")
            return redirect(
                url_for("admin.staff_detail", staff_id=staff_id)
            )

        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Unable to update staff record")
            flash("The staff record could not be updated.", "danger")
        finally:
            if cursor:
                cursor.close()

    return render_template(
        "admin/staff_form.html",
        **_staff_form_context(user, staff_member=staff_member),
        page_heading="Edit Staff Member",
        form_action=url_for("admin.edit_staff", staff_id=staff_id),
    )

# ============================================================
# MEDIVERA - MEAL MANAGEMENT MODULE
# Reuses existing meal_types, meal_menu and meal_requests tables
# ============================================================

def _meal_admin_access(permission=None, delivery_only=False):
    """Validate meal administrator role, assignment and permission."""
    user = get_current_user()
    roles = set(get_user_roles())

    if not user:
        abort(403)

    # Super Admin has complete access.
    if "super_admin" in roles:
        return user

    allowed_roles = (
        {"meal_delivery_admin"}
        if delivery_only
        else {"food_admin", "meal_delivery_admin"}
    )

    if not roles.intersection(allowed_roles):
        abort(403)

    assignments = get_admin_assignments(user["user_id"])
    assigned = any(
        row.get("responsibility") == "Food & Meal Management"
        for row in assignments
    )

    if not assigned:
        abort(403)

    permissions = get_admin_permissions(user["user_id"])

    if permission and permission not in permissions:
        abort(403)

    return user


def _meal_nav_items():
    """Sidebar compatible with templates/admin/base_admin.html."""
    return [
        {
            "label": "Meal Dashboard",
            "icon": "layout-dashboard",
            "endpoint": "admin.meals_dashboard",
        },
        {
            "label": "Meal Schedule",
            "icon": "calendar",
            "endpoint": "admin.meal_schedule",
        },
        {
            "label": "Manage Menu",
            "icon": "utensils",
            "endpoint": "admin.meal_menu_add",
        },
        {
            "label": "Meal Requests",
            "icon": "clipboard-list",
            "endpoint": "admin.meal_requests",
        },
        {
            "label": "Meal Delivery",
            "icon": "truck",
            "endpoint": "admin.meal_delivery",
        },
    ]


@admin_bp.get("/meals")
@login_required
def meals_dashboard():
    user = _meal_admin_access("meals.view")

    today = date.today()

    metrics = fetch_one(
        """
        SELECT
            (SELECT COUNT(*)
             FROM meal_menu
             WHERE menu_date = %s AND is_available = 1) AS today_menu_items,

            (SELECT COUNT(*)
             FROM meal_requests
             WHERE meal_date = %s) AS today_requests,

            (SELECT COUNT(*)
             FROM meal_requests
             WHERE meal_date = %s AND status = 'Requested') AS pending_requests,

            (SELECT COUNT(*)
             FROM meal_requests
             WHERE meal_date = %s AND status = 'Accepted') AS accepted_requests,

            (SELECT COUNT(*)
             FROM meal_requests
             WHERE meal_date = %s AND status = 'Prepared') AS prepared_requests,

            (SELECT COUNT(*)
             FROM meal_requests
             WHERE meal_date = %s AND status = 'Delivered') AS delivered_requests
        """,
        (today, today, today, today, today, today),
    ) or {}

    menu = fetch_all(
        """
        SELECT
            mm.menu_id,
            mm.menu_date,
            mm.item_name,
            mm.item_description,
            mm.dietary_type,
            mm.is_available,
            mt.meal_name,
            mt.serving_start,
            mt.serving_end
        FROM meal_menu mm
        INNER JOIN meal_types mt
            ON mt.meal_type_id = mm.meal_type_id
        WHERE mm.menu_date = %s
        ORDER BY mt.sort_order, mm.item_name
        """,
        (today,),
    )

    recent_requests = fetch_all(
        """
        SELECT
            mr.meal_request_id,
            mr.patient_id,
            mr.meal_date,
            mr.quantity,
            mr.special_instructions,
            mr.status,
            mr.requested_at,
            mm.item_name,
            mt.meal_name
        FROM meal_requests mr
        INNER JOIN meal_menu mm
            ON mm.menu_id = mr.menu_id
        INNER JOIN meal_types mt
            ON mt.meal_type_id = mr.meal_type_id
        ORDER BY mr.requested_at DESC
        LIMIT 10
        """
    )

    meal_types = fetch_all(
        """
        SELECT meal_type_id, meal_name, serving_start, serving_end
        FROM meal_types
        WHERE is_active = 1
        ORDER BY sort_order
        """
    )

    return render_template(
        "admin/meals_dashboard.html",
        can_manage_menu=("super_admin" in set(get_user_roles()) or "food_admin" in set(get_user_roles())),
        user=user,
        metrics=metrics,
        menu=menu,
        recent_requests=recent_requests,
        meal_types=meal_types,
        today=today,
        nav_items=_meal_nav_items(),
        is_super_admin="super_admin" in set(get_user_roles()),
    )




# ==========================================================
# MEDIVERA MEAL IMAGE UPLOADS
# Images are stored under static/uploads/meals.
# The database stores paths relative to the static folder.
# ==========================================================

def _save_medivera_meal_image(upload):
    if not upload or not upload.filename:
        return None

    original_name = secure_filename(upload.filename)
    extension = os.path.splitext(original_name)[1].lower()

    allowed = {".jpg", ".jpeg", ".png", ".webp"}
    if extension not in allowed:
        raise ValueError("Use JPG, JPEG, PNG or WEBP food images.")

    data = upload.read()
    upload.stream.seek(0)

    if not data:
        raise ValueError("The selected image is empty.")

    if len(data) > 5 * 1024 * 1024:
        raise ValueError("Food images must be 5 MB or smaller.")

    signatures = {
        ".jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
        ".png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
        ".webp": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    }

    if not signatures[extension](data):
        raise ValueError("The uploaded file does not match its image extension.")

    upload_dir = os.path.join(current_app.static_folder, "uploads", "meals")
    os.makedirs(upload_dir, exist_ok=True)

    filename = uuid.uuid4().hex + extension
    destination = os.path.join(upload_dir, filename)

    with open(destination, "wb") as image_file:
        image_file.write(data)

    return "uploads/meals/" + filename


def _delete_medivera_meal_image(relative_path):
    if not relative_path:
        return

    normalized = relative_path.replace("\\", "/")
    if not normalized.startswith("uploads/meals/"):
        return

    upload_root = os.path.realpath(
        os.path.join(current_app.static_folder, "uploads", "meals")
    )
    target = os.path.realpath(
        os.path.join(current_app.static_folder, normalized)
    )

    if os.path.commonpath([upload_root, target]) != upload_root:
        return

    if os.path.isfile(target):
        try:
            os.remove(target)
        except OSError:
            current_app.logger.warning(
                "Could not remove old meal image: %s", target
            )


@admin_bp.route("/meals/menu/add", methods=["GET", "POST"])
@login_required
def meal_menu_add():
    user = _meal_admin_access("meals.manage")

    if request.method == "POST":
        meal_type_id = request.form.get("meal_type_id", type=int)
        item_name = request.form.get("item_name", "").strip()
        item_description = request.form.get("item_description", "").strip()
        dietary_type = request.form.get("dietary_type", "Regular")
        menu_date = request.form.get("menu_date", "").strip()
        image_upload = request.files.get("food_image")

        allowed_dietary_types = {
            "Regular",
            "Vegetarian",
            "Vegan",
            "Diabetic Friendly",
            "Low Salt",
            "Other",
        }

        try:
            parsed_date = datetime.strptime(menu_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            parsed_date = None

        if (
            not meal_type_id
            or not item_name
            or len(item_name) > 150
            or len(item_description) > 255
            or parsed_date is None
            or dietary_type not in allowed_dietary_types
        ):
            flash("Please enter valid meal menu details.", "danger")
            return redirect(url_for("admin.meal_menu_add"))

        meal_type = fetch_one(
            """
            SELECT meal_type_id
            FROM meal_types
            WHERE meal_type_id = %s AND is_active = 1
            """,
            (meal_type_id,),
        )

        if not meal_type:
            flash("Please select a valid active meal type.", "danger")
            return redirect(url_for("admin.meal_menu_add"))

        saved_image = None
        cursor = mysql.connection.cursor()
        try:
            if image_upload and image_upload.filename:
                saved_image = _save_medivera_meal_image(image_upload)

            cursor.execute(
                """
                INSERT INTO meal_menu (
                    meal_type_id,
                    menu_date,
                    item_name,
                    item_description,
                    dietary_type,
                    is_available,
                    created_by,
                    image_path
                )
                VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                """,
                (
                    meal_type_id,
                    parsed_date,
                    item_name,
                    item_description or None,
                    dietary_type,
                    user["user_id"],
                    saved_image,
                ),
            )
            mysql.connection.commit()
            flash("Meal item added to the menu successfully.", "success")
        except ValueError as exc:
            mysql.connection.rollback()
            if saved_image:
                _delete_medivera_meal_image(saved_image)
            flash(str(exc), "danger")
        except Exception:
            mysql.connection.rollback()
            if saved_image:
                _delete_medivera_meal_image(saved_image)
            current_app.logger.exception("Unable to add meal menu item")
            flash("The meal item could not be saved.", "danger")
        finally:
            cursor.close()

        return redirect(url_for("admin.meals_dashboard"))

    meal_types = fetch_all(
        """
        SELECT meal_type_id, meal_name
        FROM meal_types
        WHERE is_active = 1
        ORDER BY sort_order
        """
    )

    return render_template(
        "admin/meal_menu_form.html",
        user=user,
        meal_types=meal_types,
        today=date.today(),
        nav_items=_meal_nav_items(),
        is_super_admin="super_admin" in set(get_user_roles()),
    )


@admin_bp.route("/meals/menu/<int:menu_id>/edit", methods=["GET", "POST"])
@login_required
def meal_menu_edit(menu_id):
    user = _meal_admin_access("meals.manage")

    item = fetch_one(
        """
        SELECT menu_id, meal_type_id, menu_date, item_name,
               item_description, dietary_type, is_available, image_path
        FROM meal_menu
        WHERE menu_id = %s
        """,
        (menu_id,),
    )

    if not item:
        abort(404)

    if request.method == "POST":
        item_name = request.form.get("item_name", "").strip()
        description = request.form.get("item_description", "").strip()
        dietary_type = request.form.get("dietary_type", "Regular")
        menu_date = request.form.get("menu_date", "").strip()
        meal_type_id = request.form.get("meal_type_id", type=int)
        is_available = 1 if request.form.get("is_available") == "1" else 0
        image_upload = request.files.get("food_image")
        remove_image = request.form.get("remove_image") == "1"

        allowed_dietary_types = {
            "Regular", "Vegetarian", "Vegan",
            "Diabetic Friendly", "Low Salt", "Other",
        }

        try:
            parsed_date = datetime.strptime(menu_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            parsed_date = None

        if (
            not item_name
            or len(item_name) > 150
            or len(description) > 255
            or not meal_type_id
            or parsed_date is None
            or dietary_type not in allowed_dietary_types
        ):
            flash("Please check the menu details.", "danger")
            return redirect(url_for("admin.meal_menu_edit", menu_id=menu_id))

        saved_image = None
        old_image = item.get("image_path")
        new_image_path = old_image

        cursor = mysql.connection.cursor()
        try:
            if image_upload and image_upload.filename:
                saved_image = _save_medivera_meal_image(image_upload)
                new_image_path = saved_image
            elif remove_image:
                new_image_path = None

            cursor.execute(
                """
                UPDATE meal_menu
                SET meal_type_id = %s,
                    menu_date = %s,
                    item_name = %s,
                    item_description = %s,
                    dietary_type = %s,
                    is_available = %s,
                    image_path = %s
                WHERE menu_id = %s
                """,
                (
                    meal_type_id,
                    parsed_date,
                    item_name,
                    description or None,
                    dietary_type,
                    is_available,
                    new_image_path,
                    menu_id,
                ),
            )
            mysql.connection.commit()

            if old_image and old_image != new_image_path:
                _delete_medivera_meal_image(old_image)

            flash("Meal menu item updated.", "success")
        except ValueError as exc:
            mysql.connection.rollback()
            if saved_image:
                _delete_medivera_meal_image(saved_image)
            flash(str(exc), "danger")
        except Exception:
            mysql.connection.rollback()
            if saved_image:
                _delete_medivera_meal_image(saved_image)
            current_app.logger.exception("Unable to update meal menu item")
            flash("The menu item could not be updated.", "danger")
        finally:
            cursor.close()

        return redirect(url_for("admin.meals_dashboard"))

    meal_types = fetch_all(
        """
        SELECT meal_type_id, meal_name
        FROM meal_types
        WHERE is_active = 1
        ORDER BY sort_order
        """
    )

    return render_template(
        "admin/meal_menu_form.html",
        user=user,
        meal_types=meal_types,
        item=item,
        today=date.today(),
        nav_items=_meal_nav_items(),
        is_super_admin="super_admin" in set(get_user_roles()),
    )


@admin_bp.get("/meals/requests")
@login_required
def meal_requests():
    user = _meal_admin_access("meals.view_requests")

    status_filter = request.args.get("status", "").strip()
    allowed_statuses = {
        "Requested", "Accepted", "Prepared",
        "Delivered", "Rejected", "Cancelled",
    }

    if status_filter not in allowed_statuses:
        status_filter = ""

    query = """
        SELECT
            mr.meal_request_id,
            mr.patient_id,
            mr.meal_date,
            mr.quantity,
            mr.special_instructions,
            mr.status,
            mr.requested_at,
            mm.item_name,
            mt.meal_name
        FROM meal_requests mr
        INNER JOIN meal_menu mm ON mm.menu_id = mr.menu_id
        INNER JOIN meal_types mt ON mt.meal_type_id = mr.meal_type_id
    """

    params = ()

    if status_filter:
        query += " WHERE mr.status = %s"
        params = (status_filter,)

    query += " ORDER BY mr.meal_date DESC, mr.requested_at DESC LIMIT 200"

    rows = fetch_all(query, params)

    return render_template(
        "admin/meal_requests.html",
        can_approve=("super_admin" in set(get_user_roles()) or "food_admin" in set(get_user_roles())),
        can_deliver=("super_admin" in set(get_user_roles()) or "meal_delivery_admin" in set(get_user_roles())),
        user=user,
        requests=rows,
        status_filter=status_filter,
        statuses=sorted(allowed_statuses),
        nav_items=_meal_nav_items(),
        is_super_admin="super_admin" in set(get_user_roles()),
    )


@admin_bp.post("/meals/requests/<int:request_id>/status")
@login_required
def meal_request_update_status(request_id):
    user = get_current_user()
    roles = set(get_user_roles())

    if "super_admin" in roles:
        pass
    elif "meal_delivery_admin" in roles:
        user = _meal_admin_access("meals.delivery_update", delivery_only=True)
    elif "food_admin" in roles:
        user = _meal_admin_access("meals.approve")
    else:
        abort(403)

    new_status = request.form.get("status", "").strip()

    request_row = fetch_one(
        """
        SELECT meal_request_id, status
        FROM meal_requests
        WHERE meal_request_id = %s
        """,
        (request_id,),
    )

    if not request_row:
        abort(404)

    current_status = request_row["status"]

    # Food Admin handles approval/rejection.
    # Delivery Admin handles preparation and delivery.
    if "super_admin" in roles:
        allowed_transitions = {
            "Requested": {"Accepted", "Rejected", "Cancelled"},
            "Accepted": {"Prepared", "Rejected", "Cancelled"},
            "Prepared": {"Delivered"},
        }
    elif "meal_delivery_admin" in roles:
        allowed_transitions = {
            "Accepted": {"Prepared"},
            "Prepared": {"Delivered"},
        }
    else:
        allowed_transitions = {
            "Requested": {"Accepted", "Rejected"},
        }

    if new_status not in allowed_transitions.get(current_status, set()):
        flash("That status change is not permitted.", "danger")
        return redirect(url_for("admin.meal_requests"))

    cursor = mysql.connection.cursor()
    try:
        cursor.execute(
            """
            UPDATE meal_requests
            SET status = %s, handled_by = %s
            WHERE meal_request_id = %s
            """,
            (new_status, user["user_id"], request_id),
        )
        mysql.connection.commit()
        flash("Meal request status updated.", "success")
    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Unable to update meal request")
        flash("Unable to update this meal request.", "danger")
    finally:
        cursor.close()

    return redirect(url_for("admin.meal_requests"))




# ==========================================================
# MEDIVERA - DEDICATED MEAL SCHEDULE
# ==========================================================

@admin_bp.get("/meals/schedule")
@login_required
def meal_schedule():
    user = _meal_admin_access("meals.view")

    selected_date_text = request.args.get(
        "date", date.today().isoformat()
    )

    try:
        selected_date = datetime.strptime(
            selected_date_text, "%Y-%m-%d"
        ).date()
    except ValueError:
        selected_date = date.today()

    schedule = fetch_all(
        """
        SELECT
            mm.menu_id,
            mm.menu_date,
            mm.item_name,
            mm.item_description,
            mm.dietary_type,
            mm.is_available,
            mt.meal_name,
            mt.serving_start,
            mt.serving_end,
            mt.sort_order
        FROM meal_menu mm
        INNER JOIN meal_types mt
            ON mt.meal_type_id = mm.meal_type_id
        WHERE mm.menu_date = %s
        ORDER BY mt.sort_order, mm.item_name
        """,
        (selected_date,)
    )

    meal_types = fetch_all(
        """
        SELECT meal_type_id, meal_name,
               serving_start, serving_end
        FROM meal_types
        WHERE is_active = 1
        ORDER BY sort_order
        """
    )

    roles = set(get_user_roles())

    return render_template(
        "admin/meal_schedule.html",
        user=user,
        schedule=schedule,
        meal_types=meal_types,
        selected_date=selected_date,
        can_manage_menu=(
            "super_admin" in roles or "food_admin" in roles
        ),
        nav_items=_meal_nav_items()
    )


# ==========================================================
# MEDIVERA - DEDICATED MEAL DELIVERY
# ==========================================================

@admin_bp.get("/meals/delivery")
@login_required
def meal_delivery():
    user = _meal_admin_access(
        "meals.delivery_update",
        delivery_only=True
    )

    deliveries = fetch_all(
        """
        SELECT
            mr.meal_request_id,
            mr.patient_id,
            mr.meal_date,
            mr.quantity,
            mr.special_instructions,
            mr.status,
            mr.requested_at,
            mm.item_name,
            mt.meal_name,
            mt.serving_start,
            mt.serving_end
        FROM meal_requests mr
        INNER JOIN meal_menu mm
            ON mm.menu_id = mr.menu_id
        INNER JOIN meal_types mt
            ON mt.meal_type_id = mr.meal_type_id
        WHERE mr.status IN ('Accepted', 'Prepared')
        ORDER BY
            mr.meal_date,
            mt.sort_order,
            mr.requested_at
        """
    )

    roles = set(get_user_roles())

    return render_template(
        "admin/meal_delivery.html",
        user=user,
        deliveries=deliveries,
        can_deliver=(
            "super_admin" in roles
            or "meal_delivery_admin" in roles
        ),
        nav_items=_meal_nav_items()
    )

# ============================================================
# MEDIVERA - SUPER ADMIN ACCOUNT MANAGEMENT
# Uses existing users, roles, user_roles, staff and activity_logs
# No database schema changes.
# ============================================================

def _super_admin_account_nav():
    """Navigation shared by Super Admin account-management pages."""
    return [
        {
            "label": "System Overview",
            "icon": "layout-dashboard",
            "endpoint": "admin.super_dashboard",
        },
        {
            "label": "All Users",
            "icon": "users",
            "endpoint": "admin.user_accounts",
        },
        {
            "label": "Create Account",
            "icon": "user-plus",
            "endpoint": "admin.create_user_account",
        },
        {
            "label": "Administrators",
            "icon": "shield-check",
            "endpoint": "admin.administrators",
        },
        {
            "label": "Roles & Permissions",
            "icon": "key-round",
            "endpoint": "admin.roles_permissions",
        },
    ]


@admin_bp.get("/users")
@login_required
def user_accounts():
    """Display all MEDIVERA user accounts."""
    if not _is_super_admin():
        abort(403)

    user = get_current_user()
    search = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip()

    conditions = []
    params = []

    if search:
        like = f"%{search}%"
        conditions.append(
            "(u.username LIKE %s OR u.email LIKE %s "
            "OR CAST(u.user_id AS CHAR) LIKE %s)"
        )
        params.extend([like, like, like])

    if status == "active":
        conditions.append("u.is_active = 1")
    elif status == "inactive":
        conditions.append("u.is_active = 0")

    where_sql = (
        "WHERE " + " AND ".join(conditions)
        if conditions else ""
    )

    accounts = fetch_all(
        f"""
        SELECT
            u.user_id,
            u.username,
            u.email,
            u.role AS legacy_role,
            u.is_active,
            u.created_at,

            (
                SELECT GROUP_CONCAT(
                    DISTINCT r.role_name
                    ORDER BY r.role_name
                    SEPARATOR ', '
                )
                FROM user_roles ur
                INNER JOIN roles r
                    ON r.role_id = ur.role_id
                WHERE ur.user_id = u.user_id
            ) AS assigned_roles,

            (
                SELECT COUNT(*)
                FROM user_roles ur
                WHERE ur.user_id = u.user_id
            ) AS role_count,

            s.staff_id,
            s.staff_name,
            s.designation

        FROM users u

        LEFT JOIN staff s
            ON s.user_id = u.user_id

        {where_sql}

        ORDER BY u.created_at DESC, u.user_id DESC
        LIMIT 300
        """,
        tuple(params),
    )

    summary = fetch_one(
        """
        SELECT
            COUNT(*) AS total_users,
            SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END)
                AS active_users,
            SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END)
                AS inactive_users
        FROM users
        """
    ) or {}

    return render_template(
        "admin/user_accounts.html",
        user=user,
        accounts=accounts,
        summary=summary,
        search=search,
        status=status,
        nav_items=_super_admin_account_nav(),
        is_super_admin=True,
    )


@admin_bp.route("/users/create", methods=["GET", "POST"])
@login_required
def create_user_account():
    """Create a login account and assign its initial RBAC role."""
    if not _is_super_admin():
        abort(403)

    user = get_current_user()

    roles = fetch_all(
        """
        SELECT role_id, role_name, description
        FROM roles
        WHERE is_active = 1
        ORDER BY role_name
        """
    )

    unlinked_staff = fetch_all(
        """
        SELECT
            staff_id,
            staff_name,
            designation,
            email
        FROM staff
        WHERE user_id IS NULL
          AND employment_status = 'active'
        ORDER BY staff_name
        """
    )

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        role_id = (request.form.get("role_id") or "").strip()
        staff_id = (request.form.get("staff_id") or "").strip()

        errors = []

        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}", username):
            errors.append(
                "Username must be 3–50 characters and contain "
                "only letters, numbers, dots, underscores or hyphens."
            )

        if (
            not email
            or len(email) > 100
            or not re.fullmatch(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email)
        ):
            errors.append("Enter a valid email address.")
        try:
            role_id = int(role_id)
            selected_role = fetch_one(
                """
                SELECT role_id, role_name
                FROM roles
                WHERE role_id = %s
                  AND is_active = 1
                """,
                (role_id,),
            )
        except (TypeError, ValueError):
            selected_role = None

        if not selected_role:
            errors.append("Select a valid active role.")

        if staff_id:
            try:
                staff_id = int(staff_id)
                selected_staff = fetch_one(
                    """
                    SELECT staff_id, staff_name, email, user_id
                    FROM staff
                    WHERE staff_id = %s
                      AND user_id IS NULL
                      AND employment_status = 'active'
                    """,
                    (staff_id,),
                )
                if not selected_staff:
                    errors.append(
                        "The selected staff record is unavailable "
                        "or already linked to an account."
                    )
                elif selected_staff.get("email") and (
                    selected_staff["email"].strip().lower() != email
                ):
                    errors.append(
                        "The account email must match the selected "
                        "staff record's email address."
                    )
            except (TypeError, ValueError):
                errors.append("Invalid staff selection.")

        if not errors:
            duplicate = fetch_one(
                """
                SELECT user_id
                FROM users
                WHERE username = %s OR email = %s
                LIMIT 1
                """,
                (username, email),
            )

            if duplicate:
                errors.append(
                    "That username or email address is already registered."
                )

        if errors:
            for error in errors:
                flash(error, "error")

        else:
            temporary_password = secrets.token_urlsafe(15)
            password_hash = generate_password_hash(
                temporary_password
            )

            role_name = selected_role["role_name"]

            # Keep the legacy enum compatible with the new RBAC role.
            if role_name == "patient":
                legacy_role = "patient"
            elif role_name == "doctor":
                legacy_role = "doctor"
            elif role_name == "receptionist":
                legacy_role = "receptionist"
            elif role_name.endswith("_admin") or role_name == "super_admin":
                legacy_role = "admin"
            else:
                legacy_role = "receptionist"

            cursor = None
            new_user_id = None

            try:
                cursor = mysql.connection.cursor()

                cursor.execute(
                    """
                    INSERT INTO users (
                        username,
                        password_hash,
                        role,
                        email,
                        is_active
                    )
                    VALUES (%s, %s, %s, %s, 1)
                    """,
                    (
                        username,
                        password_hash,
                        legacy_role,
                        email,
                    ),
                )

                new_user_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO user_roles (
                        user_id,
                        role_id,
                        assigned_by
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (
                        new_user_id,
                        role_id,
                        user["user_id"],
                    ),
                )

                if staff_id:
                    cursor.execute(
                        """
                        UPDATE staff
                        SET user_id = %s
                        WHERE staff_id = %s
                          AND user_id IS NULL
                        """,
                        (new_user_id, staff_id),
                    )

                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "The staff record could not be linked."
                        )

                # Send before commit so an SMTP failure can roll back
                # the uncommitted account and role assignment.
                send_account_credentials(
                    email=email,
                    username=username,
                    temporary_password=temporary_password,
                    role_name=role_name,
                )

                mysql.connection.commit()

                log_activity(
                    action="Account Created",
                    module="User Management",
                    description=(
                        f"Created account '{username}' "
                        f"with role '{role_name}'."
                    ),
                    user_id=user["user_id"],
                )

                flash(
                    f"Account '{username}' created. "
                    "Login credentials have been emailed.",
                    "success",
                )

                return redirect(url_for("admin.user_accounts"))

            except Exception:
                try:
                    mysql.connection.rollback()
                except Exception:
                    current_app.logger.exception(
                        "Account creation rollback failed."
                    )

                current_app.logger.exception(
                    "Super Admin account creation failed."
                )

                flash(
                    "Account creation failed. Check the server log "
                    "and SMTP configuration. No successful creation "
                    "has been confirmed.",
                    "error",
                )

            finally:
                if cursor is not None:
                    cursor.close()

    return render_template(
        "admin/create_user_account.html",
        user=user,
        roles=roles,
        staff_records=unlinked_staff,
        nav_items=_super_admin_account_nav(),
        is_super_admin=True,
    )


@admin_bp.post("/users/<int:user_id>/toggle-status")
@login_required
def toggle_user_account_status(user_id):
    """Activate or suspend an account safely."""
    if not _is_super_admin():
        abort(403)

    actor = get_current_user()

    target = fetch_one(
        """
        SELECT
            u.user_id,
            u.username,
            u.is_active,
            (
                SELECT COUNT(*)
                FROM user_roles ur
                INNER JOIN roles r
                    ON r.role_id = ur.role_id
                WHERE ur.user_id = u.user_id
                  AND r.role_name = 'super_admin'
            ) AS is_super_admin
        FROM users u
        WHERE u.user_id = %s
        """,
        (user_id,),
    )

    if not target:
        abort(404)

    if user_id == actor["user_id"] and target["is_active"]:
        flash("You cannot suspend your own account.", "error")
        return redirect(url_for("admin.user_accounts"))

    new_status = 0 if target["is_active"] else 1

    if not new_status and target["is_super_admin"]:
        active_super_admins = fetch_one(
            """
            SELECT COUNT(DISTINCT u.user_id) AS total
            FROM users u
            INNER JOIN user_roles ur
                ON ur.user_id = u.user_id
            INNER JOIN roles r
                ON r.role_id = ur.role_id
            WHERE r.role_name = 'super_admin'
              AND r.is_active = 1
              AND u.is_active = 1
            """
        )

        if (active_super_admins or {}).get("total", 0) <= 1:
            flash(
                "You cannot suspend the last active Super Admin.",
                "error",
            )
            return redirect(url_for("admin.user_accounts"))

    cursor = None

    try:
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            UPDATE users
            SET is_active = %s
            WHERE user_id = %s
            """,
            (new_status, user_id),
        )

        mysql.connection.commit()

        action = "Account Activated" if new_status else "Account Suspended"

        log_activity(
            action=action,
            module="User Management",
            description=(
                f"{action}: account '{target['username']}' "
                f"(user_id={user_id})."
            ),
            user_id=actor["user_id"],
        )

        flash(
            f"Account '{target['username']}' "
            f"{'activated' if new_status else 'suspended'}.",
            "success",
        )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Could not update account status for user_id=%s.",
            user_id,
        )
        flash("Could not update the account status.", "error")

    finally:
        if cursor is not None:
            cursor.close()

    return redirect(url_for("admin.user_accounts"))
