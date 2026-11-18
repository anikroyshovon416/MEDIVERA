
from decimal import Decimal, InvalidOperation

import MySQLdb

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.extensions import mysql
from app.laboratory import laboratory_bp

from app.auth.decorators import (
    login_required,
    role_required,
    get_current_user,
)


# ============================================================
# MEDIVERA LABORATORY MODULE
# Database: smart_healthcare
#
# Existing tables reused:
#   lab_tests
#   lab_test_catalogue
#   doctors
#   patients
#   users
#
# Workflow:
#   Doctor orders
#   Technician enters result
#   Ordering doctor verifies
#   Patient views completed result
# ============================================================


# ------------------------------------------------------------
# DATABASE HELPERS
# ------------------------------------------------------------

def lab_fetch_one(query, params=()):
    cursor = mysql.connection.cursor()

    try:
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def lab_fetch_all(query, params=()):
    cursor = mysql.connection.cursor()

    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def lab_execute(query, params=()):
    cursor = mysql.connection.cursor()

    try:
        cursor.execute(query, params)
        mysql.connection.commit()
        return cursor.lastrowid
    except Exception:
        mysql.connection.rollback()
        raise
    finally:
        cursor.close()


def current_user_id():
    user = get_current_user()

    if not user or not user.get("user_id"):
        abort(403)

    return int(user["user_id"])


def get_current_doctor():
    user_id = current_user_id()

    doctor = lab_fetch_one(
        """
        SELECT
            doctor_id,
            user_id,
            doctor_name,
            department_id,
            specialization,
            email
        FROM doctors
        WHERE user_id = %s
        LIMIT 1
        """,
        (user_id,)
    )

    if not doctor:
        abort(403)

    return doctor


def get_current_patient_id():
    user = get_current_user()

    if not user or not user.get("patient_id"):
        abort(403)

    return int(user["patient_id"])


def validate_test_result(result):
    if not result:
        return False, "Please enter the laboratory test result."

    if len(result) > 10000:
        return False, "The result is too long."

    return True, None


# ============================================================
# 1. LABORATORY TECHNICIAN DASHBOARD
# ============================================================

@laboratory_bp.route("/technician", methods=["GET"])
@login_required
@role_required("lab_technician")
def technician_dashboard():

    statistics = lab_fetch_one(
        """
        SELECT
            COUNT(*) AS total_tests,

            SUM(
                CASE
                    WHEN test_status = 'pending'
                    THEN 1 ELSE 0
                END
            ) AS pending_tests,

            SUM(
                CASE
                    WHEN test_status = 'completed'
                    THEN 1 ELSE 0
                END
            ) AS completed_tests,

            SUM(
                CASE
                    WHEN test_status = 'cancelled'
                    THEN 1 ELSE 0
                END
            ) AS cancelled_tests

        FROM lab_tests
        """
    ) or {}

    pending_tests = lab_fetch_all(
        """
        SELECT
            lt.test_id,
            lt.patient_id,
            lt.doctor_id,
            lt.test_name,
            lt.test_date,
            lt.normal_range,
            lt.test_charge,
            lt.test_status,

            CONCAT(
                p.first_name,
                ' ',
                p.last_name
            ) AS patient_name,

            p.phone AS patient_phone,

            d.doctor_name

        FROM lab_tests lt

        INNER JOIN patients p
            ON p.patient_id = lt.patient_id

        INNER JOIN doctors d
            ON d.doctor_id = lt.doctor_id

        WHERE lt.test_status = 'pending'

        ORDER BY lt.test_date ASC

        LIMIT 20
        """
    )

    return render_template(
        "laboratory/technician_dashboard.html",
        statistics=statistics,
        pending_tests=pending_tests
    )


# ============================================================
# 2. TECHNICIAN: ALL PENDING TESTS
# ============================================================

@laboratory_bp.route("/technician/tests", methods=["GET"])
@login_required
@role_required("lab_technician")
def technician_tests():

    search = request.args.get("search", "").strip()

    query = """
        SELECT
            lt.test_id,
            lt.patient_id,
            lt.doctor_id,
            lt.test_name,
            lt.test_date,
            lt.test_result,
            lt.normal_range,
            lt.test_status,
            lt.test_charge,

            CONCAT(
                p.first_name,
                ' ',
                p.last_name
            ) AS patient_name,

            d.doctor_name

        FROM lab_tests lt

        INNER JOIN patients p
            ON p.patient_id = lt.patient_id

        INNER JOIN doctors d
            ON d.doctor_id = lt.doctor_id

        WHERE lt.test_status = 'pending'
    """

    params = []

    if search:
        query += """
            AND (
                lt.test_name LIKE %s
                OR p.first_name LIKE %s
                OR p.last_name LIKE %s
                OR CAST(lt.test_id AS CHAR) = %s
            )
        """

        term = f"%{search}%"

        params.extend([
            term,
            term,
            term,
            search
        ])

    query += " ORDER BY lt.test_date ASC"

    tests = lab_fetch_all(query, tuple(params))

    return render_template(
        "laboratory/technician_tests.html",
        tests=tests,
        search=search
    )


# ============================================================
# 3. TECHNICIAN: TEST DETAILS
# ============================================================

@laboratory_bp.route(
    "/technician/tests/<int:test_id>",
    methods=["GET"]
)
@login_required
@role_required("lab_technician")
def technician_test_details(test_id):

    test = lab_fetch_one(
        """
        SELECT
            lt.*,

            CONCAT(
                p.first_name,
                ' ',
                p.last_name
            ) AS patient_name,

            p.date_of_birth,
            p.gender,
            p.phone,
            p.email,

            d.doctor_name,
            d.specialization

        FROM lab_tests lt

        INNER JOIN patients p
            ON p.patient_id = lt.patient_id

        INNER JOIN doctors d
            ON d.doctor_id = lt.doctor_id

        WHERE lt.test_id = %s

        LIMIT 1
        """,
        (test_id,)
    )

    if not test:
        abort(404)

    return render_template(
        "laboratory/test_details.html",
        test=test
    )


# ============================================================
# 4. TECHNICIAN: ENTER TEST RESULT
#
# IMPORTANT:
# The technician does NOT mark the test completed.
# The test remains pending until doctor verification.
# ============================================================

@laboratory_bp.route(
    "/technician/tests/<int:test_id>/result",
    methods=["POST"]
)
@login_required
@role_required("lab_technician")
def technician_enter_result(test_id):

    test = lab_fetch_one(
        """
        SELECT
            test_id,
            test_status,
            test_result
        FROM lab_tests
        WHERE test_id = %s
        LIMIT 1
        """,
        (test_id,)
    )

    if not test:
        abort(404)

    if test["test_status"] != "pending":
        flash(
            "Only pending laboratory tests can be updated.",
            "warning"
        )

        return redirect(
            url_for(
                "laboratory.technician_test_details",
                test_id=test_id
            )
        )

    result = request.form.get(
        "test_result",
        ""
    ).strip()

    normal_range = request.form.get(
        "normal_range",
        ""
    ).strip()

    valid, error = validate_test_result(result)

    if not valid:
        flash(error, "danger")

        return redirect(
            url_for(
                "laboratory.technician_test_details",
                test_id=test_id
            )
        )

    if len(normal_range) > 100:
        flash(
            "Reference range must not exceed 100 characters.",
            "danger"
        )

        return redirect(
            url_for(
                "laboratory.technician_test_details",
                test_id=test_id
            )
        )

    try:
        lab_execute(
            """
            UPDATE lab_tests

            SET
                test_result = %s,
                normal_range = %s

            WHERE
                test_id = %s
                AND test_status = 'pending'
            """,
            (
                result,
                normal_range or None,
                test_id
            )
        )

        flash(
            "Laboratory result saved successfully. "
            "It is now awaiting doctor verification.",
            "success"
        )

    except Exception:
        current_app.logger.exception(
            "Laboratory result entry failed"
        )

        flash(
            "Unable to save the laboratory result.",
            "danger"
        )

    return redirect(
        url_for(
            "laboratory.technician_test_details",
            test_id=test_id
        )
    )


# ============================================================
# 5. DOCTOR: LABORATORY CATALOGUE
# ============================================================

@laboratory_bp.route(
    "/doctor/catalogue",
    methods=["GET"]
)
@login_required
@role_required("doctor")
def doctor_lab_catalogue():

    catalogue = lab_fetch_all(
        """
        SELECT
            catalogue_id,
            test_name,
            description,
            normal_range,
            default_charge,
            preparation_instructions

        FROM lab_test_catalogue

        WHERE is_active = 1

        ORDER BY test_name ASC
        """
    )

    return render_template(
        "laboratory/doctor_catalogue.html",
        catalogue=catalogue
    )


# ============================================================
# 6. DOCTOR: ORDER A LABORATORY TEST
# ============================================================

@laboratory_bp.route(
    "/doctor/orders",
    methods=["GET", "POST"]
)
@login_required
@role_required("doctor")
def doctor_lab_orders():

    doctor = get_current_doctor()

    if request.method == "POST":

        patient_id = request.form.get(
            "patient_id",
            type=int
        )

        catalogue_id = request.form.get(
            "catalogue_id",
            type=int
        )

        if not patient_id or not catalogue_id:
            flash(
                "Please select a patient and laboratory test.",
                "danger"
            )

            return redirect(
                url_for("laboratory.doctor_lab_orders")
            )

        patient = lab_fetch_one(
            """
            SELECT patient_id
            FROM patients
            WHERE patient_id = %s
            LIMIT 1
            """,
            (patient_id,)
        )

        if not patient:
            flash(
                "The selected patient does not exist.",
                "danger"
            )

            return redirect(
                url_for("laboratory.doctor_lab_orders")
            )

        catalogue = lab_fetch_one(
            """
            SELECT
                catalogue_id,
                test_name,
                normal_range,
                default_charge

            FROM lab_test_catalogue

            WHERE
                catalogue_id = %s
                AND is_active = 1

            LIMIT 1
            """,
            (catalogue_id,)
        )

        if not catalogue:
            flash(
                "The selected laboratory test is unavailable.",
                "danger"
            )

            return redirect(
                url_for("laboratory.doctor_lab_orders")
            )

        try:
            charge = Decimal(
                str(catalogue["default_charge"])
            )

            if not charge.is_finite() or charge < 0:
                raise InvalidOperation

        except (InvalidOperation, TypeError, ValueError):
            flash(
                "The catalogue contains an invalid test charge.",
                "danger"
            )

            return redirect(
                url_for("laboratory.doctor_lab_orders")
            )

        try:
            test_id = lab_execute(
                """
                INSERT INTO lab_tests (
                    patient_id,
                    doctor_id,
                    test_name,
                    test_date,
                    normal_range,
                    test_status,
                    test_charge
                )

                VALUES (
                    %s,
                    %s,
                    %s,
                    NOW(),
                    %s,
                    'pending',
                    %s
                )
                """,
                (
                    patient_id,
                    doctor["doctor_id"],
                    catalogue["test_name"],
                    catalogue["normal_range"],
                    charge
                )
            )

            flash(
                f"Laboratory test ordered successfully. "
                f"Test ID: {test_id}",
                "success"
            )

        except Exception:
            current_app.logger.exception(
                "Doctor laboratory order creation failed"
            )

            flash(
                "Unable to create the laboratory test order.",
                "danger"
            )

        return redirect(
            url_for("laboratory.doctor_lab_orders")
        )

    patients = lab_fetch_all(
        """
        SELECT
            patient_id,
            first_name,
            last_name,
            phone

        FROM patients

        ORDER BY first_name, last_name
        """
    )

    catalogue = lab_fetch_all(
        """
        SELECT
            catalogue_id,
            test_name,
            default_charge,
            normal_range,
            preparation_instructions

        FROM lab_test_catalogue

        WHERE is_active = 1

        ORDER BY test_name
        """
    )

    orders = lab_fetch_all(
        """
        SELECT
            test_id,
            patient_id,
            test_name,
            test_date,
            test_status,
            test_result,
            normal_range,
            test_charge

        FROM lab_tests

        WHERE doctor_id = %s

        ORDER BY test_date DESC

        LIMIT 100
        """,
        (doctor["doctor_id"],)
    )

    return render_template(
        "laboratory/doctor_orders.html",
        doctor=doctor,
        patients=patients,
        catalogue=catalogue,
        orders=orders
    )


# ============================================================
# 7. DOCTOR: VERIFY LABORATORY RESULT
#
# Only the doctor who ordered the test can verify it.
# ============================================================

@laboratory_bp.route(
    "/doctor/tests/<int:test_id>/verify",
    methods=["POST"]
)
@login_required
@role_required("doctor")
def doctor_verify_lab_result(test_id):

    doctor = get_current_doctor()

    test = lab_fetch_one(
        """
        SELECT
            test_id,
            doctor_id,
            test_status,
            test_result

        FROM lab_tests

        WHERE test_id = %s
        LIMIT 1
        """,
        (test_id,)
    )

    if not test:
        abort(404)

    if int(test["doctor_id"]) != int(doctor["doctor_id"]):
        abort(403)

    if test["test_status"] != "pending":
        flash(
            "This test is no longer awaiting verification.",
            "warning"
        )

        return redirect(
            url_for("laboratory.doctor_lab_orders")
        )

    if not test["test_result"] or not test["test_result"].strip():
        flash(
            "The laboratory technician has not entered a result yet.",
            "warning"
        )

        return redirect(
            url_for("laboratory.doctor_lab_orders")
        )

    try:
        lab_execute(
            """
            UPDATE lab_tests

            SET test_status = 'completed'

            WHERE
                test_id = %s
                AND doctor_id = %s
                AND test_status = 'pending'
                AND test_result IS NOT NULL
                AND TRIM(test_result) <> ''
            """,
            (
                test_id,
                doctor["doctor_id"]
            )
        )

        flash(
            "Laboratory result verified and published successfully.",
            "success"
        )

    except Exception:
        current_app.logger.exception(
            "Laboratory result verification failed"
        )

        flash(
            "Unable to verify the laboratory result.",
            "danger"
        )

    return redirect(
        url_for("laboratory.doctor_lab_orders")
    )


# ============================================================
# 8. DOCTOR: CANCEL TEST
# ============================================================

@laboratory_bp.route(
    "/doctor/tests/<int:test_id>/cancel",
    methods=["POST"]
)
@login_required
@role_required("doctor")
def doctor_cancel_lab_test(test_id):

    doctor = get_current_doctor()

    test = lab_fetch_one(
        """
        SELECT
            test_id,
            doctor_id,
            test_status

        FROM lab_tests

        WHERE test_id = %s
        LIMIT 1
        """,
        (test_id,)
    )

    if not test:
        abort(404)

    if int(test["doctor_id"]) != int(doctor["doctor_id"]):
        abort(403)

    if test["test_status"] != "pending":
        flash(
            "Only pending tests can be cancelled.",
            "warning"
        )

        return redirect(
            url_for("laboratory.doctor_lab_orders")
        )

    try:
        lab_execute(
            """
            UPDATE lab_tests

            SET test_status = 'cancelled'

            WHERE
                test_id = %s
                AND doctor_id = %s
                AND test_status = 'pending'
            """,
            (
                test_id,
                doctor["doctor_id"]
            )
        )

        flash(
            "Laboratory test cancelled successfully.",
            "success"
        )

    except Exception:
        current_app.logger.exception(
            "Laboratory test cancellation failed"
        )

        flash(
            "Unable to cancel this laboratory test.",
            "danger"
        )

    return redirect(
        url_for("laboratory.doctor_lab_orders")
    )


# ============================================================
# 9. PATIENT: OWN LABORATORY TEST HISTORY
# ============================================================

@laboratory_bp.route(
    "/patient/tests",
    methods=["GET"]
)
@login_required
@role_required("patient")
def patient_lab_tests():

    patient_id = get_current_patient_id()

    tests = lab_fetch_all(
        """
        SELECT
            test_id,
            test_name,
            test_date,
            test_status,
            test_charge,
            normal_range,

            CASE
                WHEN test_status = 'completed'
                THEN test_result
                ELSE NULL
            END AS test_result

        FROM lab_tests

        WHERE patient_id = %s

        ORDER BY test_date DESC
        """,
        (patient_id,)
    )

    statistics = lab_fetch_one(
        """
        SELECT
            COUNT(*) AS total_tests,

            SUM(
                CASE
                    WHEN test_status = 'pending'
                    THEN 1 ELSE 0
                END
            ) AS pending_tests,

            SUM(
                CASE
                    WHEN test_status = 'completed'
                    THEN 1 ELSE 0
                END
            ) AS completed_tests

        FROM lab_tests

        WHERE patient_id = %s
        """,
        (patient_id,)
    ) or {}

    return render_template(
        "laboratory/patient_tests.html",
        tests=tests,
        statistics=statistics
    )


# ============================================================
# 10. PATIENT: VIEW COMPLETED TEST REPORT
# ============================================================

@laboratory_bp.route(
    "/patient/tests/<int:test_id>",
    methods=["GET"]
)
@login_required
@role_required("patient")
def patient_lab_test_details(test_id):

    patient_id = get_current_patient_id()

    test = lab_fetch_one(
        """
        SELECT
            lt.test_id,
            lt.test_name,
            lt.test_date,
            lt.test_result,
            lt.normal_range,
            lt.test_status,
            lt.test_charge,

            d.doctor_name,
            d.specialization

        FROM lab_tests lt

        INNER JOIN doctors d
            ON d.doctor_id = lt.doctor_id

        WHERE
            lt.test_id = %s
            AND lt.patient_id = %s
            AND lt.test_status = 'completed'

        LIMIT 1
        """,
        (
            test_id,
            patient_id
        )
    )

    if not test:
        abort(404)

    return render_template(
        "laboratory/patient_test_details.html",
        test=test
    )
from datetime import datetime

@laboratory_bp.route("/patient/book", methods=["GET", "POST"])
@login_required
@role_required("patient")
def patient_book_lab_tests():

    patient_id = get_current_patient_id()

    if request.method == "POST":
        selected_ids = request.form.getlist("catalogue_ids")
        doctor_id = request.form.get("doctor_id", type=int)
        preferred_datetime = request.form.get("test_date", "").strip()

        if not selected_ids:
            flash("Please select at least one laboratory test.", "warning")
            return redirect(url_for("laboratory.patient_book_lab_tests"))

        if not doctor_id or not preferred_datetime:
            flash("Please select a doctor and preferred date/time.", "warning")
            return redirect(url_for("laboratory.patient_book_lab_tests"))

        try:
            test_datetime = datetime.strptime(
                preferred_datetime, "%Y-%m-%dT%H:%M"
            )
        except ValueError:
            flash("Please provide a valid preferred date and time.", "danger")
            return redirect(url_for("laboratory.patient_book_lab_tests"))

        if test_datetime <= datetime.now():
            flash("Please select a future date and time.", "warning")
            return redirect(url_for("laboratory.patient_book_lab_tests"))

        try:
            catalogue_ids = list(dict.fromkeys(int(x) for x in selected_ids))
        except (ValueError, TypeError):
            flash("Invalid test selection.", "danger")
            return redirect(url_for("laboratory.patient_book_lab_tests"))

        placeholders = ",".join(["%s"] * len(catalogue_ids))
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        try:
            cursor.execute(
                f"""
                SELECT catalogue_id, test_name, test_category, description,
                       normal_range, default_charge, preparation_instructions
                FROM lab_test_catalogue
                WHERE is_active = 1
                  AND catalogue_id IN ({placeholders})
                """,
                tuple(catalogue_ids)
            )
            tests = cursor.fetchall()

            if len(tests) != len(catalogue_ids):
                raise ValueError("One or more selected tests are unavailable.")

            cursor.execute(
                """
                SELECT d.doctor_id
                FROM doctors d
                LEFT JOIN doctor_availability a
                    ON a.doctor_id = d.doctor_id
                WHERE d.doctor_id = %s
                  AND d.availability_status = 'available'
                  AND (
                    a.availability_id IS NULL
                    OR (
                        a.availability_status = 'Available'
                        AND (a.expires_at IS NULL OR a.expires_at > NOW())
                    )
                  )
                LIMIT 1
                """,
                (doctor_id,)
            )

            if not cursor.fetchone():
                raise ValueError(
                    "The selected doctor is currently unavailable."
                )

            created_ids = []

            mysql.connection.begin()

            for test in tests:
                cursor.execute(
                    """
                    INSERT INTO lab_tests (
                        patient_id,
                        doctor_id,
                        test_name,
                        test_date,
                        normal_range,
                        test_status,
                        test_charge
                    )
                    VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                    """,
                    (
                        patient_id,
                        doctor_id,
                        test["test_name"][:100],
                        test_datetime,
                        test["normal_range"],
                        test["default_charge"]
                    )
                )

                created_ids.append(cursor.lastrowid)

            mysql.connection.commit()

            total = sum(float(t["default_charge"]) for t in tests)

            return render_template(
                "laboratory/patient_booking_confirmation.html",
                tests=tests,
                test_ids=created_ids,
                doctor_id=doctor_id,
                test_datetime=test_datetime,
                total=total
            )

        except Exception as exc:
            mysql.connection.rollback()
            current_app.logger.exception("Laboratory booking failed")

            if isinstance(exc, ValueError):
                flash(str(exc), "warning")
            else:
                flash("Unable to complete your booking. Please try again.", "danger")

            return redirect(url_for("laboratory.patient_book_lab_tests"))

        finally:
            cursor.close()

    catalogue = lab_fetch_all(
        """
        SELECT catalogue_id, test_name, description, test_category,
               normal_range, default_charge,
               preparation_instructions
        FROM lab_test_catalogue
        WHERE is_active = 1
        ORDER BY test_name
        """
    )

    doctors = lab_fetch_all(
        """
        SELECT d.doctor_id, d.doctor_name, d.specialization
        FROM doctors d
        LEFT JOIN doctor_availability a
            ON a.doctor_id = d.doctor_id
        WHERE d.availability_status = 'available'
          AND (
            a.availability_id IS NULL
            OR (
                a.availability_status = 'Available'
                AND (a.expires_at IS NULL OR a.expires_at > NOW())
            )
          )
        ORDER BY d.doctor_name
        """
    )

    return render_template(
        "laboratory/patient_book_test.html",
        catalogue=catalogue,
        doctors=doctors
    )
