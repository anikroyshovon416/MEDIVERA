
"""
MEDIVERA â€” Emergency Ambulance Request & Approval Workflow

Uses existing tables:
    approval_requests
    approval_history
    emergency_cases
    ambulance_dispatches
    ambulances
    staff

No database schema changes.
"""

import logging

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    request,
    url_for,
)

from app.admin import admin_bp

from app.admin.emergency_routes import (
    _require_emergency_admin,
    _render_emergency,
    emergency_fetch_all,
)

from app.auth.decorators import (
    get_current_user,
    login_required,
)

from app.extensions import mysql

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def _approval_clean(value, maximum=255):
    return (value or "").strip()[:maximum]


def _is_ambulance_request(row):
    """Identify ambulance requests without affecting other approvals."""

    return (
        row
        and row.get("request_type") == "Other"
        and row.get("reference_table") == "emergency_cases"
        and (row.get("request_title") or "").startswith(
            "Ambulance Request -"
        )
    )


def _approval_redirect():
    return redirect(url_for("admin.approvals"))

# ============================================================
# 1. AMBULANCE REQUEST CENTRE
# GET /admin/emergency/ambulance-requests
# ============================================================

@admin_bp.get("/emergency/ambulance-requests")
@login_required
def ambulance_requests():

    _require_emergency_admin()

    requests_list = emergency_fetch_all("""
        SELECT
            ar.request_id,
            ar.requester_user_id,
            ar.request_type,
            ar.request_title,
            ar.request_description,
            ar.responsibility,
            ar.reference_table,
            ar.reference_id,
            ar.status,
            ar.admin_remarks,
            ar.submitted_at,
            ar.reviewed_at,

            u.username AS requester_name,

            ec.case_number,
            ec.emergency_type,
            ec.severity,
            ec.case_status,
            ec.emergency_location

        FROM approval_requests ar

        LEFT JOIN users u
            ON u.user_id = ar.requester_user_id

        LEFT JOIN emergency_cases ec
            ON ec.emergency_case_id = ar.reference_id
           AND ar.reference_table = 'emergency_cases'

        WHERE ar.request_type = 'Other'
          AND ar.reference_table = 'emergency_cases'
          AND ar.request_title LIKE 'Ambulance Request -%%'

        ORDER BY ar.submitted_at DESC

        LIMIT 300
    """)

    active_cases = emergency_fetch_all("""
        SELECT
            emergency_case_id,
            case_number,
            emergency_type,
            severity,
            case_status,
            emergency_location

        FROM emergency_cases

        WHERE case_status NOT IN (
            'Resolved',
            'Cancelled'
        )

        ORDER BY reported_at DESC
    """)
    available_ambulances = emergency_fetch_all("""
        SELECT
            ambulance_id,
            ambulance_code,
            registration_number,
            ambulance_type
        FROM ambulances
        WHERE ambulance_status = 'available'
        ORDER BY ambulance_code
    """)

    available_drivers = emergency_fetch_all("""
    SELECT
        staff_id,
        staff_name
    FROM staff
    WHERE employment_status = 'active'
      AND LOWER(designation) LIKE '%%driver%%'
    ORDER BY staff_name
""")

    summary = {
        "total": len(requests_list),
        "pending": 0,
        "approved": 0,
        "rejected": 0,
        "assigned": 0,
    }

    for item in requests_list:

        status = (item.get("status") or "").lower()

        if status in ("pending", "under review"):
            summary["pending"] += 1

        elif status == "approved":
            summary["approved"] += 1

        elif status == "rejected":
            summary["rejected"] += 1

        # An assignment is represented by an Assigned history entry.
        # The detailed assignment status is also visible in dispatches.

        return _render_emergency(
        "admin/ambulance_requests.html",
        requests_list=requests_list,
        active_cases=active_cases,
        available_ambulances=available_ambulances,
        available_drivers=available_drivers,
        summary=summary,
    )

# ============================================================
# 2. SUBMIT AMBULANCE REQUEST
# POST /admin/emergency/ambulance-requests/create
# ============================================================

@admin_bp.post("/emergency/ambulance-requests/create")
@login_required
def create_ambulance_request():

    _require_emergency_admin()

    user = get_current_user()

    if not user:
        abort(401)

    case_id = request.form.get("emergency_case_id", type=int)

    pickup = _approval_clean(
        request.form.get("pickup_location"),
        255,
    )

    destination = _approval_clean(
        request.form.get("destination"),
        255,
    )

    reason = _approval_clean(
        request.form.get("request_reason"),
        2000,
    )

    if not case_id or not pickup or not destination or not reason:

        flash(
            "Complete all ambulance request fields.",
            "danger",
        )

        return _approval_redirect()

    cursor = None

    try:

        cursor = mysql.connection.cursor()

        mysql.connection.begin()

        # Lock the emergency case.
        cursor.execute("""
            SELECT
                emergency_case_id,
                case_number,
                case_status

            FROM emergency_cases

            WHERE emergency_case_id = %s

            FOR UPDATE
        """, (case_id,))

        case = cursor.fetchone()

        if not case:
            mysql.connection.rollback()

            flash("Emergency case not found.", "danger")

            return _approval_redirect()

        if case["case_status"] in ("Resolved", "Cancelled"):
            mysql.connection.rollback()

            flash(
                "An ambulance cannot be requested for a closed emergency case.",
                "danger",
            )

            return _approval_redirect()

        # Prevent duplicate active ambulance requests.
        cursor.execute("""
            SELECT request_id

            FROM approval_requests

            WHERE request_type = 'Other'
              AND reference_table = 'emergency_cases'
              AND reference_id = %s
              AND request_title LIKE 'Ambulance Request -%%'
              AND status IN (
                  'Pending',
                  'Under Review',
                  'Approved'
              )

            LIMIT 1

            FOR UPDATE
        """, (case_id,))

        existing = cursor.fetchone()

        if existing:
            mysql.connection.rollback()

            flash(
                "An active ambulance request already exists for this case.",
                "warning",
            )

            return _approval_redirect()

        title = (
            f"Ambulance Request - {case['case_number']}"
        )

        description = (
            f"Emergency Case: {case['case_number']}\n"
            f"Pickup Location: {pickup}\n"
            f"Destination: {destination}\n"
            f"Reason: {reason}"
        )

        # Create approval request.
        cursor.execute("""
            INSERT INTO approval_requests (
                requester_user_id,
                request_type,
                request_title,
                request_description,
                responsibility,
                reference_table,
                reference_id,
                status
            )

            VALUES (
                %s,
                'Other',
                %s,
                %s,
                'Patient Services',
                'emergency_cases',
                %s,
                'Pending'
            )
        """, (
            user["user_id"],
            title,
            description,
            case_id,
        ))

        request_id = cursor.lastrowid

        # Record submission history.
        cursor.execute("""
            INSERT INTO approval_history (
                request_id,
                action_by,
                action,
                previous_status,
                new_status,
                comments
            )

            VALUES (
                %s,
                %s,
                'Submitted',
                NULL,
                'Pending',
                %s
            )
        """, (
            request_id,
            user["user_id"],
            "Ambulance request submitted for Super Admin approval.",
        ))

        mysql.connection.commit()

        flash(
            f"Ambulance request #{request_id} submitted successfully.",
            "success",
        )

    except Exception:

        mysql.connection.rollback()

        logger.exception("Ambulance request creation failed.")

        flash(
            "Ambulance request could not be submitted. Check application logs.",
            "danger",
        )

    finally:

        if cursor:
            cursor.close()

    return _approval_redirect()


# ============================================================
# 3. APPROVE / REJECT / REVIEW AMBULANCE REQUEST
# POST /admin/emergency/ambulance-requests/<id>/decision
# ============================================================

@admin_bp.post(
    "/emergency/ambulance-requests/<int:request_id>/decision"
)
@login_required
def decide_ambulance_request(request_id):

    _require_emergency_admin()

    user = get_current_user()

    if not user:
        abort(401)

    decision = _approval_clean(
        request.form.get("decision"),
        50,
    )

    remarks = _approval_clean(
        request.form.get("admin_remarks"),
        2000,
    )

    allowed_decisions = {
        "Under Review",
        "Approved",
        "Rejected",
        "Changes Requested",
    }

    if decision not in allowed_decisions:

        flash("Invalid approval decision.", "danger")

        return _approval_redirect()

    if decision in ("Rejected", "Changes Requested") and not remarks:

        flash(
            "Please provide remarks for this decision.",
            "danger",
        )

        return _approval_redirect()

    cursor = None

    try:

        cursor = mysql.connection.cursor()

        mysql.connection.begin()

        cursor.execute("""
            SELECT
                request_id,
                request_type,
                request_title,
                reference_table,
                reference_id,
                status

            FROM approval_requests

            WHERE request_id = %s

            FOR UPDATE
        """, (request_id,))

        approval = cursor.fetchone()

        if not _is_ambulance_request(approval):

            mysql.connection.rollback()

            flash(
                "Ambulance approval request not found.",
                "danger",
            )

            return _approval_redirect()

        previous_status = approval["status"]

        if previous_status not in (
    "Pending",
    "Under Review",
    "Changes Requested",
):

            mysql.connection.rollback()

            flash(
                "This request has already been processed.",
                "warning",
            )

            return _approval_redirect()

        # Update approval request.
        cursor.execute("""
            UPDATE approval_requests

            SET
                status = %s,
                admin_remarks = %s,
                assigned_admin_id = %s,

                reviewed_at = CASE
                    WHEN %s IN ('Approved', 'Rejected')
                    THEN NOW()
                    ELSE reviewed_at
                END

            WHERE request_id = %s
        """, (
            decision,
            remarks or None,
            user["user_id"],
            decision,
            request_id,
        ))

        # Record decision history.
        cursor.execute("""
            INSERT INTO approval_history (
                request_id,
                action_by,
                action,
                previous_status,
                new_status,
                comments
            )

            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
        """, (
            request_id,
            user["user_id"],
            decision,
            previous_status,
            decision,
            remarks or f"Request marked {decision}.",
        ))

        mysql.connection.commit()

        flash(
            f"Ambulance request #{request_id}: {decision}.",
            "success",
        )

    except Exception:

        mysql.connection.rollback()

        logger.exception(
            "Ambulance request decision failed: %s",
            request_id,
        )

        flash(
            "Approval decision could not be saved.",
            "danger",
        )

    finally:

        if cursor:
            cursor.close()

    return _approval_redirect()


# ============================================================
# 4. ASSIGN APPROVED REQUEST TO AN AMBULANCE
# POST /admin/emergency/ambulance-requests/<id>/assign
# ============================================================

@admin_bp.post(
    "/emergency/ambulance-requests/<int:request_id>/assign"
)
@login_required
def assign_approved_ambulance(request_id):

    _require_emergency_admin()

    user = get_current_user()

    if not user:
        abort(401)

    ambulance_id = request.form.get("ambulance_id", type=int)

    driver_id = request.form.get("driver_staff_id", type=int)

    pickup = _approval_clean(
        request.form.get("pickup_location"),
        255,
    )

    destination = _approval_clean(
        request.form.get("destination"),
        255,
    )

    notes = _approval_clean(
        request.form.get("dispatch_notes"),
        2000,
    )

    if not ambulance_id or not pickup or not destination:

        flash(
            "Select an ambulance and provide pickup and destination.",
            "danger",
        )

        return _approval_redirect()

    cursor = None

    try:

        cursor = mysql.connection.cursor()

        mysql.connection.begin()

        # Lock approval request.
        cursor.execute("""
            SELECT
                request_id,
                request_type,
                request_title,
                reference_table,
                reference_id,
                status

            FROM approval_requests

            WHERE request_id = %s

            FOR UPDATE
        """, (request_id,))

        approval = cursor.fetchone()

        if not _is_ambulance_request(approval):

            mysql.connection.rollback()

            flash("Ambulance request not found.", "danger")

            return _approval_redirect()

        if approval["status"] != "Approved":

            mysql.connection.rollback()

            flash(
                "Only approved ambulance requests can be assigned.",
                "danger",
            )

            return _approval_redirect()

        case_id = approval["reference_id"]

        # Lock emergency case.
        cursor.execute("""
            SELECT
                emergency_case_id,
                case_status

            FROM emergency_cases

            WHERE emergency_case_id = %s

            FOR UPDATE
        """, (case_id,))

        case = cursor.fetchone()

        if not case or case["case_status"] in (
            "Resolved",
            "Cancelled",
        ):

            mysql.connection.rollback()

            flash(
                "The emergency case is no longer eligible for dispatch.",
                "danger",
            )

            return _approval_redirect()

        # Prevent duplicate active dispatches.
        cursor.execute("""
            SELECT dispatch_id

            FROM ambulance_dispatches

            WHERE emergency_case_id = %s

              AND dispatch_status NOT IN (
                  'Completed',
                  'Cancelled'
              )

            LIMIT 1

            FOR UPDATE
        """, (case_id,))

        existing_dispatch = cursor.fetchone()

        if existing_dispatch:

            mysql.connection.rollback()

            flash(
                "An active dispatch already exists for this emergency case.",
                "warning",
            )

            return _approval_redirect()

        # Lock selected ambulance.
        cursor.execute("""
            SELECT
                ambulance_id,
                ambulance_code,
                ambulance_status

            FROM ambulances

            WHERE ambulance_id = %s

            FOR UPDATE
        """, (ambulance_id,))

        ambulance = cursor.fetchone()

        if not ambulance or ambulance["ambulance_status"] != "available":

            mysql.connection.rollback()

            flash(
                "The selected ambulance is no longer available.",
                "danger",
            )

            return _approval_redirect()

        # Validate optional driver.
        if driver_id:

            cursor.execute("""
                SELECT staff_id

                FROM staff

                WHERE staff_id = %s

                LIMIT 1
            """, (driver_id,))

            driver = cursor.fetchone()

            if not driver:

                mysql.connection.rollback()

                flash("Selected driver was not found.", "danger")

                return _approval_redirect()

        # Create actual dispatch only after approval.
        cursor.execute("""
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
                %s,
                %s,
                %s,
                %s,
                'Dispatched',
                %s,
                %s,
                NOW(),
                %s
            )
        """, (
            case_id,
            ambulance_id,
            driver_id or None,
            user["user_id"],
            pickup,
            destination,
            notes or None,
        ))

        dispatch_id = cursor.lastrowid

        # Mark ambulance unavailable.
        cursor.execute("""
            UPDATE ambulances

            SET ambulance_status = 'dispatched'

            WHERE ambulance_id = %s
              AND ambulance_status = 'available'
        """, (ambulance_id,))

        if cursor.rowcount != 1:

            mysql.connection.rollback()

            flash(
                "Ambulance availability changed. Please try again.",
                "warning",
            )

            return _approval_redirect()

        # Update emergency case.
        if case["case_status"] in ("Reported", "Triaged"):

            cursor.execute("""
                UPDATE emergency_cases

                SET case_status = 'In Progress'

                WHERE emergency_case_id = %s
            """, (case_id,))

        # Record assignment in approval history.
        cursor.execute("""
            INSERT INTO approval_history (
                request_id,
                action_by,
                action,
                previous_status,
                new_status,
                comments
            )

            VALUES (
                %s,
                %s,
                'Assigned',
                'Approved',
                'Approved',
                %s
            )
        """, (
            request_id,
            user["user_id"],
            (
                f"Ambulance {ambulance['ambulance_code']} "
                f"assigned. Dispatch ID: {dispatch_id}."
            ),
        ))

        mysql.connection.commit()

        flash(
            (
                f"Ambulance {ambulance['ambulance_code']} "
                f"dispatched successfully. Dispatch #{dispatch_id}."
            ),
            "success",
        )

    except Exception:

        mysql.connection.rollback()

        logger.exception(
            "Ambulance assignment failed: request_id=%s",
            request_id,
        )

        flash(
            "Ambulance assignment failed. Check application logs.",
            "danger",
        )

    finally:

        if cursor:
            cursor.close()

    return _approval_redirect()

