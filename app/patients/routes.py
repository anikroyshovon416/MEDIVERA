
from datetime import date, datetime, time, timedelta
import os
import uuid

import MySQLdb
from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename
from app.patients import patient_bp

from app.extensions import mysql
from app.auth.decorators import login_required, role_required, get_current_user


MAX_DOCUMENT_SIZE = 4 * 1024 * 1024

ALLOWED_DOCUMENTS = {
    "pdf": ("application/pdf", b"%PDF-"),
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "jpg": ("image/jpeg", b"\xff\xd8\xff"),
    "jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    "webp": ("image/webp", b"RIFF"),
}


def _patient_id():
    """Return the logged-in patient's own patient ID."""
    user = get_current_user()

    if not user or not user.get("patient_id"):
        abort(403)

    return user["patient_id"]


def _fetch_one(query, params=()):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def _fetch_all(query, params=()):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()


@patient_bp.route("/dashboard")
@login_required
@role_required("patient")
def dashboard():
    patient_id = _patient_id()

    patient = _fetch_one(
        """
        SELECT patient_id, first_name, last_name, phone, email,
               blood_group, gender, date_of_birth
        FROM patients
        WHERE patient_id = %s
        """,
        (patient_id,),
    )

    if not patient:
        abort(404)

    statistics = _fetch_one(
        """
        SELECT
            COUNT(*) AS total_appointments,
            SUM(status IN ('scheduled', 'pending_approval', 'confirmed'))
                AS active_appointments,
            SUM(status = 'completed') AS completed_appointments,
            SUM(status = 'cancelled') AS cancelled_appointments
        FROM appointments
        WHERE patient_id = %s
        """,
        (patient_id,),
    )

    upcoming_appointments = _fetch_all(
        """
        SELECT
            a.appointment_id,
            DATE_FORMAT(a.appointment_date, '%%d %%b %%Y')
                AS appointment_date,
          TIME_FORMAT(a.appointment_time, '%%h:%%i %%p')
                AS appointment_time,
            a.status,
            a.reason,
            d.doctor_name,
            d.specialization
        FROM appointments a
        JOIN doctors d ON d.doctor_id = a.doctor_id
        WHERE a.patient_id = %s
          AND a.appointment_date >= CURDATE()
          AND a.status IN ('scheduled', 'pending_approval', 'confirmed')
        ORDER BY a.appointment_date, a.appointment_time
        LIMIT 5
        """,
        (patient_id,),
    )

    document_count = _fetch_one(
        """
        SELECT COUNT(*) AS total_documents
        FROM patient_documents
        WHERE patient_id = %s
        """,
        (patient_id,),
    )

    # These summaries use existing MEDIVERA tables. Each query is kept
    # patient-scoped; no patient can see another patient's activity.
    active_admissions = _fetch_one(
        """SELECT COUNT(*) AS total FROM admissions
           WHERE patient_id = %s AND admission_status = 'admitted'""",
        (patient_id,),
    )
    pending_lab_tests = _fetch_one(
        """SELECT COUNT(*) AS total FROM lab_tests
           WHERE patient_id = %s AND test_status = 'pending'""",
        (patient_id,),
    )
    active_pharmacy_orders = _fetch_one(
        """SELECT COUNT(*) AS total FROM pharmacy_orders
           WHERE patient_id = %s
             AND order_status NOT IN ('Delivered', 'Rejected', 'Cancelled')""",
        (patient_id,),
    )
    active_meal_requests = _fetch_one(
        """SELECT COUNT(*) AS total FROM meal_requests
           WHERE patient_id = %s
             AND meal_date >= CURDATE()
             AND status NOT IN ('Delivered', 'Rejected', 'Cancelled')""",
        (patient_id,),
    )
    unread_notifications = _fetch_one(
        """SELECT COUNT(*) AS total FROM notifications
           WHERE user_id = %s AND is_read = 0""",
        (get_current_user()["user_id"],),
    )

    available_beds = _fetch_all("""
        SELECT
            r.room_id, r.room_number, r.room_type,
            r.floor_number, r.daily_charge,
            b.bed_id, b.bed_number,
            b.description AS bed_description
        FROM rooms r
        INNER JOIN beds b ON b.room_id = r.room_id
        WHERE r.room_status <> 'maintenance'
          AND b.bed_status = 'available'
          AND NOT EXISTS (
              SELECT 1
              FROM admissions a
              WHERE a.bed_id = b.bed_id
                AND a.admission_status = 'admitted'
          )
        ORDER BY r.room_number, b.bed_number
    """)

    return render_template(
        "patient/dashboard.html",
        patient=patient,
        available_beds=available_beds,
        today_date=date.today().isoformat(),
        statistics=statistics,
        upcoming_appointments=upcoming_appointments,
        document_count=document_count["total_documents"],
        active_admissions=active_admissions["total"],
        pending_lab_tests=pending_lab_tests["total"],
        active_pharmacy_orders=active_pharmacy_orders["total"],
        active_meal_requests=active_meal_requests["total"],
        unread_notifications=unread_notifications["total"],
    )


@patient_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("patient")
def profile():
    patient_id = _patient_id()
    user = get_current_user()

    if not user or not user.get("user_id"):
        abort(403)

    patient = _fetch_one(
        """
        SELECT patient_id, first_name, last_name, date_of_birth,
               gender, blood_group, phone, email, address,
               emergency_contact_name, emergency_contact_phone,
               created_at
        FROM patients
        WHERE patient_id = %s
        """,
        (patient_id,),
    )

    if not patient:
        abort(404)

    # Profile photo uploads use the existing users.profile_photo column.
    max_photo_size = 5 * 1024 * 1024
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp"}

    if request.method == "POST":
        uploaded_file = request.files.get("profile_photo")

        if not uploaded_file or not uploaded_file.filename:
            flash("Please select a profile photo first.", "danger")
            return redirect(url_for("patient.profile"))

        safe_original_name = secure_filename(uploaded_file.filename)
        extension = os.path.splitext(safe_original_name)[1].lower()

        if extension not in allowed_extensions:
            flash("Only PNG, JPG, JPEG and WEBP images are allowed.", "danger")
            return redirect(url_for("patient.profile"))

        image_data = uploaded_file.read(max_photo_size + 1)

        if not image_data:
            flash("The selected image is empty.", "danger")
            return redirect(url_for("patient.profile"))

        if len(image_data) > max_photo_size:
            flash("Profile photo must not exceed 5 MB.", "danger")
            return redirect(url_for("patient.profile"))

        # Validate the actual image signature, not just the filename.
        valid_image = False

        if extension == ".png":
            valid_image = image_data.startswith(b"\x89PNG\r\n\x1a\n")

        elif extension in {".jpg", ".jpeg"}:
            valid_image = image_data.startswith(b"\xff\xd8\xff")

        elif extension == ".webp":
            valid_image = (
                len(image_data) >= 12
                and image_data[0:4] == b"RIFF"
                and image_data[8:12] == b"WEBP"
            )

        if not valid_image:
            flash("The selected file is not a valid image of that format.", "danger")
            return redirect(url_for("patient.profile"))

        upload_directory = os.path.join(
            current_app.static_folder,
            "uploads",
            "profile_photos",
        )

        os.makedirs(upload_directory, exist_ok=True)

        generated_filename = uuid.uuid4().hex + extension
        destination = os.path.join(upload_directory, generated_filename)

        # This relative path is stored in users.profile_photo.
        database_photo_path = (
            "uploads/profile_photos/" + generated_filename
        )

        saved_file = False
        cursor = None

        try:
            with open(destination, "xb") as photo_file:
                photo_file.write(image_data)

            saved_file = True

            cursor = mysql.connection.cursor()

            cursor.execute(
                """
                UPDATE users
                SET profile_photo = %s
                WHERE user_id = %s
                """,
                (database_photo_path, user["user_id"]),
            )

            if cursor.rowcount != 1:
                mysql.connection.rollback()
                raise RuntimeError(
                    "The profile photo could not be linked to the logged-in user."
                )

            mysql.connection.commit()

            flash("Your profile photo has been updated successfully.", "success")

        except Exception:
            try:
                mysql.connection.rollback()
            except Exception:
                pass

            if saved_file and os.path.isfile(destination):
                try:
                    os.remove(destination)
                except OSError:
                    current_app.logger.exception(
                        "Could not remove an unlinked profile photo."
                    )

            current_app.logger.exception("Patient profile photo upload failed.")
            flash("The profile photo could not be saved. Please try again.", "danger")

        finally:
            if cursor is not None:
                cursor.close()

        return redirect(url_for("patient.profile"))

    # Retrieve the currently saved photo for this authenticated user.
    photo_row = _fetch_one(
        """
        SELECT profile_photo
        FROM users
        WHERE user_id = %s
        """,
        (user["user_id"],),
    )

    stored_photo = (photo_row or {}).get("profile_photo") or ""
    stored_photo = stored_photo.replace("\\", "/").strip()

    # Support older paths that may include the static/ prefix.
    if stored_photo.startswith("/static/"):
        stored_photo = stored_photo[len("/static/"):]
    elif stored_photo.startswith("static/"):
        stored_photo = stored_photo[len("static/"):]

    profile_photo_url = None

    # Do not allow absolute paths or directory traversal in stored paths.
    if (
        stored_photo
        and not stored_photo.startswith("/")
        and ".." not in stored_photo.split("/")
    ):
        profile_photo_url = url_for(
            "static",
            filename=stored_photo,
        )

    return render_template(
        "patient/profile.html",
        patient=patient,
        profile_photo_url=profile_photo_url,
    )

@patient_bp.route("/appointments")
@login_required
@role_required("patient")
def appointments():
    patient_id = _patient_id()

    appointment_list = _fetch_all(
        """
       SELECT
    a.appointment_id,
    a.appointment_date,
    a.appointment_time AS appointment_time,
    a.reason,
    a.status,
    d.doctor_name,
    d.specialization
        FROM appointments a
        JOIN doctors d ON d.doctor_id = a.doctor_id
        WHERE a.patient_id = %s
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """,
        (patient_id,),
    )

    return render_template(
        "patient/appointments.html",
        appointments=appointment_list,
    )


@patient_bp.route("/documents")
@login_required
@role_required("patient")
def documents():
    patient_id = _patient_id()

    document_list = _fetch_all(
        """
        SELECT document_id, document_type, original_filename,
               mime_type, file_size, created_at
        FROM patient_documents
        WHERE patient_id = %s
        ORDER BY created_at DESC
        """,
        (patient_id,),
    )

    return render_template(
        "patient/documents.html",
        documents=document_list,
    )


@patient_bp.route("/documents/upload", methods=["POST"])
@login_required
@role_required("patient")
def upload_document():
    patient_id = _patient_id()
    user = get_current_user()

    uploaded_file = request.files.get("document")
    document_type = request.form.get("document_type", "").strip()

    if document_type not in {"medical_report", "prescription", "other"}:
        flash("Please select a valid document category.", "error")
        return redirect(url_for("patient.documents"))

    if not uploaded_file or not uploaded_file.filename:
        flash("Please choose a document to upload.", "error")
        return redirect(url_for("patient.documents"))

    original_filename = secure_filename(uploaded_file.filename)

    if not original_filename or "." not in original_filename:
        flash("The selected filename is not valid.", "error")
        return redirect(url_for("patient.documents"))

    extension = original_filename.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_DOCUMENTS:
        flash("Only PDF, PNG, JPG and WEBP files are allowed.", "error")
        return redirect(url_for("patient.documents"))

    file_data = uploaded_file.stream.read(MAX_DOCUMENT_SIZE + 1)

    if not file_data:
        flash("The selected document is empty.", "error")
        return redirect(url_for("patient.documents"))

    if len(file_data) > MAX_DOCUMENT_SIZE:
        flash("The maximum document size is 4 MB.", "error")
        return redirect(url_for("patient.documents"))

    mime_type, signature = ALLOWED_DOCUMENTS[extension]

    if not file_data.startswith(signature):
        flash("The file content does not match its extension.", "error")
        return redirect(url_for("patient.documents"))

    # WEBP files must contain the WEBP marker after RIFF.
    if extension == "webp" and file_data[8:12] != b"WEBP":
        flash("The selected WEBP file is invalid.", "error")
        return redirect(url_for("patient.documents"))

    upload_folder = current_app.config["PATIENT_UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)

    stored_filename = f"{uuid.uuid4().hex}.{extension}"
    file_path = os.path.join(upload_folder, stored_filename)

    try:
        with open(file_path, "wb") as document_file:
            document_file.write(file_data)

        cursor = mysql.connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO patient_documents (
                    patient_id,
                    uploaded_by_user_id,
                    document_type,
                    original_filename,
                    stored_filename,
                    mime_type,
                    file_size
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    patient_id,
                    user["user_id"],
                    document_type,
                    original_filename[:255],
                    stored_filename,
                    mime_type,
                    len(file_data),
                ),
            )
            mysql.connection.commit()
        finally:
            cursor.close()

        flash("Your document was uploaded successfully.", "success")

    except Exception:
        mysql.connection.rollback()

        if os.path.exists(file_path):
            os.remove(file_path)

        current_app.logger.exception("Patient document upload failed.")
        flash("The document could not be uploaded. Please try again.", "error")

    return redirect(url_for("patient.documents"))


@patient_bp.route("/documents/<int:document_id>/download")
@login_required
@role_required("patient")
def download_document(document_id):
    patient_id = _patient_id()

    document = _fetch_one(
        """
        SELECT stored_filename, original_filename, mime_type
        FROM patient_documents
        WHERE document_id = %s AND patient_id = %s
        """,
        (document_id, patient_id),
    )

    if not document:
        abort(404)

    upload_folder = os.path.abspath(
        current_app.config["PATIENT_UPLOAD_FOLDER"]
    )

    stored_filename = os.path.basename(document["stored_filename"])
    file_path = os.path.abspath(
        os.path.join(upload_folder, stored_filename)
    )

    if os.path.commonpath([upload_folder, file_path]) != upload_folder:
        abort(404)

    if not os.path.isfile(file_path):
        abort(404)

    return send_file(
        file_path,
        mimetype=document["mime_type"],
        as_attachment=True,
        download_name=document["original_filename"],
    )

@patient_bp.route("/appointments/book", methods=["GET", "POST"])
@login_required
@role_required("patient")
def book_appointment():
    patient_id = _patient_id()

    doctors = _fetch_all(
        """
        SELECT
            d.doctor_id,
            d.doctor_name,
            d.specialization,
            d.consultation_fee,
            dep.department_name
        FROM doctors d
        JOIN departments dep
            ON dep.department_id = d.department_id
        WHERE d.availability_status = 'available'
        ORDER BY dep.department_name, d.doctor_name
        """
    )

    if request.method == "GET":
        return render_template(
    "patient/book_appointment.html",
    doctors=doctors,
    min_date=date.today().isoformat(),
)

    doctor_id = request.form.get("doctor_id", "").strip()
    appointment_date = request.form.get("appointment_date", "").strip()
    appointment_time = request.form.get("appointment_time", "").strip()
    reason = request.form.get("reason", "").strip()

    # Validate doctor selection.
    try:
        doctor_id = int(doctor_id)
    except (TypeError, ValueError):
        flash("Please select a valid doctor.", "error")
        return redirect(url_for("patient.book_appointment"))

    # Validate appointment date.
    try:
        selected_date = date.fromisoformat(appointment_date)
    except (TypeError, ValueError):
        flash("Please select a valid appointment date.", "error")
        return redirect(url_for("patient.book_appointment"))

    if selected_date < date.today():
        flash("You cannot book an appointment in the past.", "error")
        return redirect(url_for("patient.book_appointment"))

    # Validate appointment time.
    try:
        selected_time = time.fromisoformat(appointment_time)
    except (TypeError, ValueError):
        flash("Please select a valid appointment time.", "error")
        return redirect(url_for("patient.book_appointment"))

    if selected_date == date.today() and selected_time <= datetime.now().time():
        flash("Please select a future appointment time.", "error")
        return redirect(url_for("patient.book_appointment"))

    if len(reason) > 255:
        flash("The appointment reason must be 255 characters or fewer.", "error")
        return redirect(url_for("patient.book_appointment"))

    # Confirm that the selected doctor is currently available.
    doctor = _fetch_one(
        """
        SELECT doctor_id
        FROM doctors
        WHERE doctor_id = %s
          AND availability_status = 'available'
        """,
        (doctor_id,),
    )

    if not doctor:
        flash("The selected doctor is unavailable. Please choose another.", "error")
        return redirect(url_for("patient.book_appointment"))

    cursor = mysql.connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO appointments (
                patient_id,
                doctor_id,
                appointment_date,
                appointment_time,
                reason,
                status
            )
            VALUES (%s, %s, %s, %s, %s, 'pending_approval')
            """,
            (
                patient_id,
                doctor_id,
                selected_date,
                selected_time,
                reason or None,
            ),
        )

        mysql.connection.commit()

        flash(
            "Your appointment request was submitted successfully. "
            "It is waiting for approval.",
            "success",
        )

        return redirect(url_for("patient.appointments"))

    except MySQLdb.IntegrityError as error:
        mysql.connection.rollback()

        if error.args and error.args[0] == 1062:
            flash(
                "This doctor's selected time slot is already booked. "
                "Please choose another time.",
                "error",
            )
        else:
            current_app.logger.exception(
                "Appointment booking database error."
            )
            flash(
                "Your appointment could not be booked. Please try again.",
                "error",
            )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Appointment booking failed.")

        flash(
            "Something went wrong while booking your appointment.",
            "error",
        )

    finally:
        cursor.close()

    return redirect(url_for("patient.book_appointment"))

@patient_bp.route("/appointments/<int:appointment_id>/cancel", methods=["POST"])
@login_required
@role_required("patient")
def cancel_appointment(appointment_id):
    """Allow a patient to cancel only their own not-yet-completed appointment."""
    patient_id = _patient_id()
    cursor = mysql.connection.cursor()
    try:
        cursor.execute(
            """UPDATE appointments
               SET status = 'cancelled'
               WHERE appointment_id = %s
                 AND patient_id = %s
                 AND status IN ('scheduled', 'pending_approval', 'confirmed')""",
            (appointment_id, patient_id),
        )
        changed = cursor.rowcount
        mysql.connection.commit()
        if changed:
            flash("Your appointment has been cancelled.", "success")
        else:
            flash("That appointment cannot be cancelled or was not found.", "error")
    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Patient appointment cancellation failed.")
        flash("The appointment could not be cancelled. Please try again.", "error")
    finally:
        cursor.close()
    return redirect(url_for("patient.appointments"))


@patient_bp.route("/medical-records")
@login_required
@role_required("patient")
def medical_records():
    """Show only medical records belonging to the logged-in patient."""
    patient_id = _patient_id()
    records = _fetch_all(
        """SELECT mr.record_id, mr.diagnosis, mr.symptoms, mr.treatment,
                  mr.prescription, mr.record_date,
                  d.doctor_name, d.specialization
           FROM medical_records mr
           JOIN doctors d ON d.doctor_id = mr.doctor_id
           WHERE mr.patient_id = %s
           ORDER BY mr.record_date DESC, mr.record_id DESC""",
        (patient_id,),
    )
    return render_template("patient/medical_records.html", records=records)




# ============================================================
# PATIENT NOTIFICATION CENTRE
# ============================================================

@patient_bp.route("/notifications", methods=["GET"])
@login_required
@role_required("patient")
def notifications():
    """
    Display notifications belonging only to the signed-in patient.
    """
    user = get_current_user()

    if not user or not user.get("user_id"):
        abort(403)

    user_id = user["user_id"]

    items = _fetch_all(
        """
        SELECT
            notification_id,
            user_id,
            announcement_id,
            title,
            message,
            notification_type,
            is_read,
            created_at,
            read_at
        FROM notifications
        WHERE user_id = %s
        ORDER BY
            is_read ASC,
            created_at DESC,
            notification_id DESC
        """,
        (user_id,)
    )

    return render_template(
        "patient/notifications.html",
        notifications=items
    )


# ============================================================
# MARK ONE NOTIFICATION AS READ
# ============================================================

@patient_bp.route(
    "/notifications/<int:notification_id>/read",
    methods=["POST"]
)
@login_required
@role_required("patient")
def notification_mark_read(notification_id):
    """
    Mark one notification as read.
    The user_id condition prevents patients from changing
    another user's notification.
    """
    user = get_current_user()

    if not user or not user.get("user_id"):
        abort(403)

    user_id = user["user_id"]
    cursor = mysql.connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE notifications
            SET
                is_read = 1,
                read_at = NOW()
            WHERE
                notification_id = %s
                AND user_id = %s
                AND is_read = 0
            """,
            (notification_id, user_id)
        )

        mysql.connection.commit()

        if cursor.rowcount:
            flash("Notification marked as read.", "success")
        else:
            flash(
                "Notification was not found or was already read.",
                "info"
            )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Unable to mark patient notification as read."
        )
        flash("Unable to update the notification. Please try again.", "danger")

    finally:
        cursor.close()

    return redirect(url_for("patient.notifications"))


# ============================================================
# MARK ALL NOTIFICATIONS AS READ
# ============================================================

@patient_bp.route(
    "/notifications/mark-all-read",
    methods=["POST"]
)
@login_required
@role_required("patient")
def notifications_mark_all_read():
    """
    Mark every unread notification belonging to the signed-in
    user as read.
    """
    user = get_current_user()

    if not user or not user.get("user_id"):
        abort(403)

    user_id = user["user_id"]
    cursor = mysql.connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE notifications
            SET
                is_read = 1,
                read_at = NOW()
            WHERE
                user_id = %s
                AND is_read = 0
            """,
            (user_id,)
        )

        updated_count = cursor.rowcount
        mysql.connection.commit()

        if updated_count:
            flash(
                f"{updated_count} notification(s) marked as read.",
                "success"
            )
        else:
            flash("You have no unread notifications.", "info")

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Unable to mark all patient notifications as read."
        )
        flash(
            "Unable to update notifications. Please try again.",
            "danger"
        )

    finally:
        cursor.close()

    return redirect(url_for("patient.notifications"))


# ============================================================
# WARD & BED REQUESTS
# Add this section at the END of app/patients/routes.py
# ============================================================

@patient_bp.route("/ward-bed-requests", methods=["GET", "POST"])
@login_required
@role_required("patient")
def ward_bed_requests():
    patient_id = _patient_id()
    user = get_current_user()

    if not user or not user.get("user_id"):
        abort(403)

    if request.method == "POST":
        bed_id = request.form.get("bed_id", type=int)
        reason = (request.form.get("reason") or "").strip()
        admission_date = (request.form.get("admission_date") or "").strip()
        medical_notes = (request.form.get("medical_notes") or "").strip()

        if not admission_date:
            flash("Please select your preferred admission date.", "danger")
            return redirect(url_for("patient.ward_bed_requests"))

        try:
            requested_date = datetime.strptime(
                admission_date, "%Y-%m-%d"
            ).date()
            if requested_date < date.today():
                raise ValueError("Admission date cannot be in the past.")
        except ValueError:
            flash("Please select a valid admission date (today or later).", "danger")
            return redirect(url_for("patient.ward_bed_requests"))

        if len(medical_notes) > 2000:
            flash("Medical notes cannot exceed 2000 characters.", "danger")
            return redirect(url_for("patient.ward_bed_requests"))

        if not bed_id:
            flash("Please select an available bed.", "danger")
            return redirect(url_for("patient.ward_bed_requests"))

        if not reason:
            flash("Please provide a reason for your request.", "danger")
            return redirect(url_for("patient.ward_bed_requests"))

        if len(reason) > 255:
            flash("Please keep the reason within 255 characters.", "danger")
            return redirect(url_for("patient.ward_bed_requests"))

        cursor = mysql.connection.cursor()

        try:
            # Confirm that the selected bed is still available.
            cursor.execute(
                """
                SELECT
                    b.bed_id,
                    b.bed_number,
                    r.room_id,
                    r.room_number,
                    r.room_type,
                    r.floor_number,
                    r.daily_charge
                FROM beds b
                INNER JOIN rooms r
                    ON r.room_id = b.room_id
                WHERE b.bed_id = %s
                  AND b.bed_status = 'available'
                  AND r.room_status <> 'maintenance'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM admissions a
                      WHERE a.bed_id = b.bed_id
                        AND a.admission_status = 'admitted'
                  )
                FOR UPDATE
                """,
                (bed_id,),
            )

            selected_bed = cursor.fetchone()

            if not selected_bed:
                mysql.connection.rollback()
                flash(
                    "That bed is no longer available. Please select another.",
                    "warning",
                )
                return redirect(url_for("patient.ward_bed_requests"))

            # Avoid duplicate pending requests for the same bed.
            cursor.execute(
                """
                SELECT request_id
                FROM approval_requests
                WHERE requester_user_id = %s
                  AND request_type = 'Room/Bed Request'
                  AND reference_table = 'beds'
                  AND reference_id = %s
                  AND status IN (
                      'Pending',
                      'Under Review',
                      'Changes Requested'
                  )
                LIMIT 1
                """,
                (user["user_id"], bed_id),
            )

            existing_request = cursor.fetchone()

            if existing_request:
                mysql.connection.rollback()
                flash(
                    "You already have a pending request for this bed.",
                    "info",
                )
                return redirect(url_for("patient.ward_bed_requests"))

            description = (
                f"Patient ID: {patient_id}.\n"
                f"Room: {selected_bed['room_number']} "
                f"({selected_bed['room_type']}).\n"
                f"Bed: {selected_bed['bed_number']}.\n"
                f"Floor: {selected_bed['floor_number']}.\n"
                f"Daily charge: INR {selected_bed['daily_charge']}.\n"
                f"Admission date: {admission_date}\n"
                f"Reason: {reason}\n"
                f"Medical notes: {medical_notes or 'Not provided'}"
            )

            cursor.execute(
                """
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
                    'Room/Bed Request',
                    %s,
                    %s,
                    'Patient Services',
                    'beds',
                    %s,
                    'Pending'
                )
                """,
                (
                    user["user_id"],
                    f"Bed request - Room {selected_bed['room_number']}",
                    description,
                    bed_id,
                ),
            )

            request_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO approval_history (
                    request_id,
                    action_by,
                    action,
                    previous_status,
                    new_status,
                    comments
                )
                VALUES (%s, %s, 'Submitted', NULL, 'Pending', %s)
                """,
                (
                    request_id,
                    user["user_id"],
                    "Patient submitted a room/bed accommodation request.",
                ),
            )

            mysql.connection.commit()

            flash(
                "Your bed request has been submitted for administrative approval.",
                "success",
            )

        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception(
                "Unable to submit patient ward/bed request."
            )
            flash(
                "We could not submit your request. Please try again.",
                "danger",
            )

        finally:
            cursor.close()

        if request.form.get("return_to") == "dashboard":
            return redirect(url_for("patient.dashboard"))
        return redirect(url_for("patient.ward_bed_requests"))

    # Available beds and rooms.
    available_beds = _fetch_all("""
    SELECT
        r.room_id,
        r.room_number,
        r.room_type,
        r.floor_number,
        r.daily_charge,
        r.room_status,
        b.bed_id,
        b.bed_number,
        b.description AS bed_description,
        b.bed_status
    FROM rooms AS r
    INNER JOIN beds AS b
        ON b.room_id = r.room_id
    WHERE
        r.room_status <> 'maintenance'
        AND b.bed_status = 'available'
        AND NOT EXISTS (
            SELECT 1
            FROM admissions AS a
            WHERE a.bed_id = b.bed_id
              AND a.admission_status = 'admitted'
        )
    ORDER BY
        r.room_number,
        b.bed_number
""")

    # Only requests submitted by this patient are displayed.
    my_requests = _fetch_all(
        """
        SELECT
            request_id,
            request_title,
            request_description,
            reference_id,
            status,
            admin_suggestion,
            admin_remarks,
            submitted_at,
            reviewed_at
        FROM approval_requests
        WHERE requester_user_id = %s
          AND request_type = 'Room/Bed Request'
        ORDER BY submitted_at DESC, request_id DESC
        """,
        (user["user_id"],),
    )

    return render_template(
        "patient/ward_bed_requests.html",
        available_beds=available_beds,
        my_requests=my_requests,
    )

# ============================================================
# MEDICINE SCHEDULE Ã¢â‚¬â€ PATIENT PORTAL
# Uses existing prescriptions, medicine_schedules,
# and medicine_dose_logs tables.
# ============================================================

import json


def _parse_medicine_times(raw_times):
    """
    Accept common JSON formats, including:
        ["08:00", "14:00", "20:00"]
        [{"time": "08:00", "label": "Morning"}]
        {"times": ["08:00", "20:00"]}
        {"morning": "08:00", "night": "20:00"}

    Returns a list of dictionaries containing Python time objects.
    Does not invent times when the stored value is invalid.
    """
    if raw_times is None:
        return []

    value = raw_times

    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [part.strip() for part in value.split(",") if part.strip()]

    if isinstance(value, dict):
        nested = (
            value.get("times")
            or value.get("administration_times")
            or value.get("schedule")
        )

        if nested is not None:
            value = nested
        else:
            value = [
                {"label": str(label).replace("_", " ").title(), "time": item}
                for label, item in value.items()
            ]

    if not isinstance(value, list):
        value = [value]

    parsed = []

    for item in value:
        label = None
        time_value = item

        if isinstance(item, dict):
            time_value = (
                item.get("time")
                or item.get("at")
                or item.get("scheduled_time")
                or item.get("value")
            )
            label = item.get("label") or item.get("name")

        if time_value is None:
            continue

        if isinstance(time_value, time):
            parsed_time = time_value
        else:
            text_value = str(time_value).strip()

            parsed_time = None
            for time_format in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
                try:
                    parsed_time = datetime.strptime(
                        text_value, time_format
                    ).time()
                    break
                except ValueError:
                    continue

        if parsed_time is None:
            continue

        parsed.append({
            "time": parsed_time,
            "label": str(label).strip() if label else
                     parsed_time.strftime("%I:%M %p"),
            "sort_value": parsed_time.strftime("%H:%M:%S"),
        })

    parsed.sort(key=lambda entry: entry["sort_value"])
    return parsed


@patient_bp.route("/medicine-schedule")
@login_required
@role_required("patient")
def medicine_schedule():
    patient_id = _patient_id()
    today = date.today()
    now = datetime.now()

    # Only schedules belonging to the authenticated patient.
    schedules = _fetch_all(
        """
        SELECT
            ms.schedule_id,
            ms.prescription_id,
            ms.start_date,
            ms.end_date,
            ms.administration_times,
            ms.meal_instruction,
            ms.additional_instructions,
            ms.is_active,
            ms.created_at,

            p.medicine_name,
            p.dosage,
            p.frequency,
            p.duration,
            p.instructions AS prescription_instructions,
            p.prescription_date,

            mr.diagnosis,
            mr.record_date,

            d.doctor_name,
            d.specialization

        FROM medicine_schedules ms
        INNER JOIN prescriptions p
            ON p.prescription_id = ms.prescription_id
        INNER JOIN medical_records mr
            ON mr.record_id = p.record_id
        INNER JOIN doctors d
            ON d.doctor_id = mr.doctor_id

        WHERE ms.patient_id = %s

        ORDER BY
            ms.is_active DESC,
            ms.start_date DESC,
            p.medicine_name ASC
        """,
        (patient_id,),
    )

    # Generate today's scheduled dose records.
    # INSERT IGNORE respects the existing unique constraint:
    # (schedule_id, scheduled_for).
    cursor = mysql.connection.cursor()

    try:
        for schedule in schedules:
            start_date = schedule["start_date"]
            end_date = schedule["end_date"]

            if not schedule["is_active"]:
                continue

            if start_date and start_date > today:
                continue

            if end_date and end_date < today:
                continue

            dose_times = _parse_medicine_times(
                schedule["administration_times"]
            )

            schedule["parsed_times"] = dose_times

            for dose_time in dose_times:
                scheduled_for = datetime.combine(
                    today, dose_time["time"]
                )

                cursor.execute(
                    """
                    INSERT IGNORE INTO medicine_dose_logs
                        (schedule_id, scheduled_for, dose_status)
                    VALUES (%s, %s, 'Scheduled')
                    """,
                    (
                        schedule["schedule_id"],
                        scheduled_for,
                    ),
                )

        mysql.connection.commit()

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Could not generate today's medicine dose records."
        )
        raise
    finally:
        cursor.close()

    # Today's doses for this patient only.
    today_doses = _fetch_all(
        """
        SELECT
            l.dose_log_id,
            l.schedule_id,
            l.scheduled_for,
            l.dose_status,
            l.marked_at,
            l.patient_notes,

            p.medicine_name,
            p.dosage,
            p.frequency,

            ms.meal_instruction,
            ms.additional_instructions

        FROM medicine_dose_logs l
        INNER JOIN medicine_schedules ms
            ON ms.schedule_id = l.schedule_id
        INNER JOIN prescriptions p
            ON p.prescription_id = ms.prescription_id

        WHERE ms.patient_id = %s
          AND l.scheduled_for >= %s
          AND l.scheduled_for < %s

        ORDER BY l.scheduled_for ASC
        """,
        (
            patient_id,
            datetime.combine(today, time.min),
            datetime.combine(today + timedelta(days=1), time.min),
        ),
    )

    # Recent dose history, excluding today's timetable.
    dose_history = _fetch_all(
        """
        SELECT
            l.dose_log_id,
            l.scheduled_for,
            l.dose_status,
            l.marked_at,
            l.patient_notes,
            p.medicine_name,
            p.dosage,
            ms.meal_instruction

        FROM medicine_dose_logs l
        INNER JOIN medicine_schedules ms
            ON ms.schedule_id = l.schedule_id
        INNER JOIN prescriptions p
            ON p.prescription_id = ms.prescription_id

        WHERE ms.patient_id = %s
          AND l.scheduled_for < %s

        ORDER BY l.scheduled_for DESC
        LIMIT 30
        """,
        (
            patient_id,
            datetime.combine(today, time.min),
        ),
    )

    active_schedules = [
        item for item in schedules
        if item["is_active"]
        and (not item["end_date"] or item["end_date"] >= today)
        and (not item["start_date"] or item["start_date"] <= today)
    ]

    taken_count = sum(
        1 for dose in today_doses
        if dose["dose_status"] == "Taken"
    )

    pending_count = sum(
        1 for dose in today_doses
        if dose["dose_status"] == "Scheduled"
    )

    missed_count = sum(
        1 for dose in today_doses
        if dose["dose_status"] == "Missed"
    )

    return render_template(
        "patient/medicine_schedule.html",
        now=now,
        schedules=schedules,
        active_schedules=active_schedules,
        today_doses=today_doses,
        dose_history=dose_history,
        today=today,
        taken_count=taken_count,
        pending_count=pending_count,
        missed_count=missed_count,
        total_today=len(today_doses),
    )


@patient_bp.route(
    "/medicine-schedule/dose/<int:dose_log_id>/taken",
    methods=["POST"],
)
@login_required
@role_required("patient")
def mark_medicine_dose_taken(dose_log_id):
    patient_id = _patient_id()
    now = datetime.now()

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        cursor.execute(
            """
            SELECT
                l.dose_log_id,
                l.scheduled_for,
                l.dose_status
            FROM medicine_dose_logs l
            INNER JOIN medicine_schedules ms
                ON ms.schedule_id = l.schedule_id
            WHERE l.dose_log_id = %s
              AND ms.patient_id = %s
            FOR UPDATE
            """,
            (dose_log_id, patient_id),
        )

        dose = cursor.fetchone()

        if not dose:
            mysql.connection.rollback()
            abort(404)

        if dose["dose_status"] == "Taken":
            mysql.connection.rollback()
            flash("This dose has already been marked as taken.", "info")
            return redirect(url_for("patient.medicine_schedule"))

        if dose["dose_status"] != "Scheduled":
            mysql.connection.rollback()
            flash("This dose cannot be updated.", "warning")
            return redirect(url_for("patient.medicine_schedule"))

        if dose["scheduled_for"] > now:
            mysql.connection.rollback()
            flash(
                "You cannot mark a future dose as taken.",
                "warning",
            )
            return redirect(url_for("patient.medicine_schedule"))

        cursor.execute(
            """
            UPDATE medicine_dose_logs
            SET dose_status = 'Taken',
                marked_at = %s
            WHERE dose_log_id = %s
              AND dose_status = 'Scheduled'
            """,
            (now, dose_log_id),
        )

        if cursor.rowcount != 1:
            mysql.connection.rollback()
            flash("The dose could not be updated. Please refresh.", "warning")
            return redirect(url_for("patient.medicine_schedule"))

        mysql.connection.commit()
        flash("Medicine dose marked as taken.", "success")

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Error marking patient medicine dose as taken."
        )
        raise
    finally:
        cursor.close()

    return redirect(url_for("patient.medicine_schedule"))


# ==========================================================
# MEDIVERA PATIENT MEALS AND DINING
# Uses existing meal_types, meal_menu, meal_requests tables
# ==========================================================

@patient_bp.route("/meals", methods=["GET"])
@login_required
@role_required("patient")
def patient_meals():
    patient_id = _patient_id()

    selected_date_text = request.args.get("date", date.today().isoformat())

    try:
        selected_date = datetime.strptime(
            selected_date_text, "%Y-%m-%d"
        ).date()
    except (TypeError, ValueError):
        selected_date = date.today()

    today = date.today()

    # Always load the active meal schedule, even if no menu has
    # been published for the selected date.
    meal_schedule = _fetch_all(
        """
        SELECT
            meal_type_id,
            meal_name,
            serving_start,
            serving_end,
            sort_order
        FROM meal_types
        WHERE is_active = 1
        ORDER BY sort_order, serving_start
        """
    )

    # Load available food items for the selected date.
    menu = _fetch_all(
        """
        SELECT
            mm.menu_id,
            mm.menu_date,
            mm.meal_type_id,
            mm.item_name,
            mm.item_description,
            mm.dietary_type,
            mm.is_available,
            mm.image_path,
            mt.meal_name,
            mt.serving_start,
            mt.serving_end,
            mt.sort_order
        FROM meal_menu mm
        INNER JOIN meal_types mt
            ON mt.meal_type_id = mm.meal_type_id
        WHERE mm.menu_date = %s
          AND mm.is_available = 1
          AND mt.is_active = 1
        ORDER BY mt.sort_order, mm.item_name
        """,
        (selected_date,)
    )

    # The database unique key is patient_id + meal_date + meal_type_id.
    # Therefore, any existing request occupies that meal slot,
    # including a cancelled or rejected request.
    requested_slots = _fetch_all(
        """
        SELECT meal_type_id
        FROM meal_requests
        WHERE patient_id = %s
          AND meal_date = %s
        """,
        (patient_id, selected_date)
    )

    requested_slot_ids = {
        row["meal_type_id"] for row in requested_slots
    }

    my_requests = _fetch_all(
        """
        SELECT
            mr.meal_request_id,
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
        WHERE mr.patient_id = %s
        ORDER BY mr.meal_date DESC, mr.requested_at DESC
        LIMIT 50
        """,
        (patient_id,)
    )

    active_count = _fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM meal_requests
        WHERE patient_id = %s
          AND meal_date >= CURDATE()
          AND status NOT IN ('Delivered', 'Rejected', 'Cancelled')
        """,
        (patient_id,)
    )

    return render_template(
        "patient/meals.html",
        meal_schedule=meal_schedule,
        menu=menu,
        my_requests=my_requests,
        requested_slot_ids=requested_slot_ids,
        selected_date=selected_date,
        today=today,
        active_count=active_count["total"] if active_count else 0
    )

@patient_bp.route("/meals/request", methods=["POST"])
@login_required
@role_required("patient")
def patient_meal_request():
    patient_id = _patient_id()

    try:
        menu_id = int(request.form.get("menu_id", ""))
        quantity = int(request.form.get("quantity", "1"))
    except (TypeError, ValueError):
        flash("Please select a valid menu item and quantity.", "error")
        return redirect(url_for("patient.patient_meals"))

    instructions = request.form.get(
        "special_instructions", ""
    ).strip()

    if quantity < 1 or quantity > 10:
        flash("Quantity must be between 1 and 10.", "error")
        return redirect(url_for("patient.patient_meals"))

    if len(instructions) > 255:
        flash("Special instructions cannot exceed 255 characters.", "error")
        return redirect(url_for("patient.patient_meals"))

    selected_menu = _fetch_one(
        """
        SELECT
            mm.menu_id,
            mm.meal_type_id,
            mm.menu_date,
            mm.item_name,
            mt.meal_name
        FROM meal_menu mm
        INNER JOIN meal_types mt
            ON mt.meal_type_id = mm.meal_type_id
        WHERE mm.menu_id = %s
          AND mm.is_available = 1
          AND mt.is_active = 1
          AND mm.menu_date >= CURDATE()
        """,
        (menu_id,)
    )

    if not selected_menu:
        flash("This menu item is unavailable or its date has passed.", "error")
        return redirect(url_for("patient.patient_meals"))

    existing_request = _fetch_one(
        """
        SELECT meal_request_id
        FROM meal_requests
        WHERE patient_id = %s
          AND meal_date = %s
          AND meal_type_id = %s
        LIMIT 1
        """,
        (
            patient_id,
            selected_menu["menu_date"],
            selected_menu["meal_type_id"]
        )
    )

    if existing_request:
        flash(
            "You already have a request for this meal slot on that date. "
            "Please check your meal request history.",
            "warning"
        )
        return redirect(url_for(
            "patient.patient_meals",
            date=selected_menu["menu_date"].isoformat()
        ))

    cursor = mysql.connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO meal_requests (
                patient_id,
                menu_id,
                meal_type_id,
                meal_date,
                quantity,
                special_instructions,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'Requested')
            """,
            (
                patient_id,
                selected_menu["menu_id"],
                selected_menu["meal_type_id"],
                selected_menu["menu_date"],
                quantity,
                instructions or None
            )
        )

        mysql.connection.commit()

        flash(
            f"Your {selected_menu['meal_name']} request for "
            f"{selected_menu['item_name']} has been submitted.",
            "success"
        )

    except MySQLdb.IntegrityError:
        mysql.connection.rollback()
        flash(
            "A request already exists for this meal slot and date.",
            "warning"
        )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Patient meal request failed")
        flash(
            "We could not submit your meal request. Please try again.",
            "error"
        )

    finally:
        cursor.close()

    return redirect(url_for(
        "patient.patient_meals",
        date=selected_menu["menu_date"].isoformat()
    ))

@patient_bp.route(
    "/meals/<int:meal_request_id>/cancel",
    methods=["POST"]
)
@login_required
@role_required("patient")
def patient_cancel_meal_request(meal_request_id):
    patient_id = _patient_id()
    cursor = mysql.connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE meal_requests
            SET status = 'Cancelled'
            WHERE meal_request_id = %s
              AND patient_id = %s
              AND meal_date >= CURDATE()
              AND status = 'Requested'
            """,
            (meal_request_id, patient_id)
        )

        changed = cursor.rowcount
        mysql.connection.commit()

        if changed:
            flash("Your meal request has been cancelled.", "success")
        else:
            flash(
                "This request cannot be cancelled. It may already be "
                "being prepared, delivered, or cancelled.",
                "warning"
            )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Patient meal cancellation failed"
        )
        flash("We could not cancel this request. Please try again.", "error")

    finally:
        cursor.close()

    return redirect(url_for("patient.patient_meals"))



# ============================================================
# PATIENT BILLING, OPERATION THEATRE & EMERGENCY SERVICES
# Append this section to the END of app/patients/routes.py
# Reuses existing MEDIVERA tables; creates no database tables.
# ============================================================

@patient_bp.route("/billing", methods=["GET"])
@login_required
@role_required("patient")
def billing():
    patient_id = _patient_id()

    bills = _fetch_all(
        """
        SELECT
            bill_id,
            appointment_id,
            total_amount,
            payment_method,
            payment_status,
            payment_date,
            created_at
        FROM billing
        WHERE patient_id = %s
        ORDER BY
            COALESCE(payment_date, DATE(created_at)) DESC,
            bill_id DESC
        """,
        (patient_id,)
    )

    summary = _fetch_one(
        """
        SELECT
            COUNT(*) AS total_bills,
            COALESCE(SUM(total_amount), 0) AS total_amount,
            COALESCE(SUM(
                CASE WHEN payment_status = 'paid'
                     THEN total_amount ELSE 0 END
            ), 0) AS paid_amount,
            COALESCE(SUM(
                CASE WHEN payment_status = 'pending'
                     THEN total_amount ELSE 0 END
            ), 0) AS pending_amount
        FROM billing
        WHERE patient_id = %s
        """,
        (patient_id,)
    ) or {}

    return render_template(
        "patient/billing.html",
        bills=bills,
        summary=summary
    )


# Replace the existing operation_theatre() route function with this function.
# Keep the existing imports and other patient routes unchanged.

@patient_bp.route("/operation-theatre", methods=["GET", "POST"])
@login_required
@role_required("patient")
def operation_theatre():
    user = get_current_user()
    patient_id = _patient_id()

    if not user or not user.get("user_id"):
        abort(403)

    if request.method == "POST":
        request_kind = (request.form.get("request_kind") or "").strip()
        specialty = (request.form.get("specialty") or "").strip()
        procedure_name = (request.form.get("procedure_name") or "").strip()
        urgency = (request.form.get("urgency") or "").strip()
        referring_doctor = (request.form.get("referring_doctor") or "").strip()
        preferred_date = (request.form.get("preferred_date") or "").strip()
        diagnosis = (request.form.get("diagnosis") or "").strip()
        clinical_notes = (request.form.get("clinical_notes") or "").strip()
        contact_phone = (request.form.get("contact_phone") or "").strip()
        additional_notes = (request.form.get("additional_notes") or "").strip()
        confirmation = request.form.get("confirmation")

        allowed_kinds = {
            "New surgery / procedure consultation", "Pre-operative assessment",
            "Surgery scheduling request", "Second opinion for a proposed surgery",
            "Post-operative follow-up", "Operation theatre related assistance",
        }
        allowed_specialties = {
            "General & Laparoscopic Surgery", "Orthopaedics & Joint Replacement",
            "Neurosurgery", "Cardiothoracic & Vascular Surgery", "Cardiac Surgery",
            "Urology", "Obstetrics & Gynaecology", "ENT / Head & Neck Surgery",
            "Ophthalmology", "Plastic & Reconstructive Surgery", "Paediatric Surgery",
            "Gastrointestinal / GI Surgery", "Onco-Surgery",
            "Dental & Maxillofacial Surgery", "Other / Not sure",
        }
        allowed_urgencies = {
            "Routine / Elective", "Time-sensitive (doctor advised)",
            "Priority review requested",
        }

        if request_kind not in allowed_kinds or specialty not in allowed_specialties:
            flash("Select a valid request type and surgical specialty.", "error")
            return redirect(url_for("patient.operation_theatre"))
        if urgency not in allowed_urgencies:
            flash("Select a valid request priority.", "error")
            return redirect(url_for("patient.operation_theatre"))
        if not diagnosis or len(diagnosis) > 1500:
            flash("Please describe the diagnosis or reason (maximum 1,500 characters).", "error")
            return redirect(url_for("patient.operation_theatre"))
        if confirmation != "yes":
            flash("Please confirm that this is a request for review, not a confirmed booking.", "error")
            return redirect(url_for("patient.operation_theatre"))
        if (len(procedure_name) > 180 or len(referring_doctor) > 150
                or len(clinical_notes) > 2000 or len(contact_phone) > 20
                or len(additional_notes) > 1500):
            flash("One or more fields exceed the permitted length.", "error")
            return redirect(url_for("patient.operation_theatre"))

        if preferred_date:
            try:
                parsed_date = date.fromisoformat(preferred_date)
                if parsed_date < date.today():
                    raise ValueError
            except ValueError:
                flash("Choose a valid preferred date (today or later).", "error")
                return redirect(url_for("patient.operation_theatre"))

        title_suffix = procedure_name or request_kind
        title = f"{specialty}: {title_suffix}"[:150]
        description = (
            f"Patient ID: {patient_id}\n"
            f"Request type: {request_kind}\n"
            f"Surgical specialty: {specialty}\n"
            f"Procedure / operation: {procedure_name or 'Not specified'}\n"
            f"Requested priority: {urgency}\n"
            f"Referring / treating doctor: {referring_doctor or 'Not provided'}\n"
            f"Preferred review date: {preferred_date or 'Not specified'}\n"
            f"Diagnosis / reason: {diagnosis}\n"
            f"Relevant medical details: {clinical_notes or 'Not provided'}\n"
            f"Preferred contact: {contact_phone or 'Not provided'}\n"
            f"Additional notes: {additional_notes or 'None'}\n"
            "Patient acknowledgement: Request only; no surgery or theatre booking confirmed."
        )

        cursor = mysql.connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO approval_requests (
                       requester_user_id, request_type, request_title,
                       request_description, responsibility, reference_table,
                       reference_id, status
                   ) VALUES (
                       %s, 'Operating Theatre Request', %s, %s,
                       'Patient Services', 'patients', %s, 'Pending'
                   )""",
                (user["user_id"], title, description, patient_id),
            )
            request_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO approval_history (
                       request_id, action_by, action, previous_status,
                       new_status, comments
                   ) VALUES (%s, %s, 'Submitted', NULL, 'Pending', %s)""",
                (request_id, user["user_id"],
                 "Patient submitted a structured surgical / operation theatre request."),
            )
            mysql.connection.commit()
            flash("Your surgical request has been submitted to the hospital team for review.", "success")
        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Patient operation theatre request submission failed.")
            flash("The request could not be submitted. Please try again.", "error")
        finally:
            cursor.close()

        return redirect(url_for("patient.operation_theatre"))

    requests_list = _fetch_all(
        """SELECT request_id, request_title, request_description, status,
                  admin_suggestion, admin_remarks, submitted_at, reviewed_at
           FROM approval_requests
           WHERE requester_user_id = %s
             AND request_type = 'Operating Theatre Request'
           ORDER BY submitted_at DESC, request_id DESC
           LIMIT 100""",
        (user["user_id"],),
    )
    return render_template(
        "patient/operation_theatre.html",
        requests_list=requests_list,
        min_date=date.today().isoformat(),
    )

@patient_bp.route("/emergency-assistance", methods=["GET", "POST"])
@login_required
@role_required("patient")
def emergency_assistance():
    user = get_current_user()
    patient_id = _patient_id()

    if request.method == "POST":
        emergency_type = (request.form.get("emergency_type") or "").strip()
        severity = (request.form.get("severity") or "Medium").strip()
        description = (request.form.get("case_description") or "").strip()
        location = (request.form.get("emergency_location") or "").strip()
        contact_person = (request.form.get("contact_person") or "").strip()
        contact_phone = (request.form.get("contact_phone") or "").strip()

        if not emergency_type or len(emergency_type) > 100:
            flash("Enter an emergency type (maximum 100 characters).", "error")
            return redirect(url_for("patient.emergency_assistance"))

        if severity not in {"Low", "Medium", "High", "Critical"}:
            flash("Select a valid severity.", "error")
            return redirect(url_for("patient.emergency_assistance"))

        if not description or len(description) > 5000:
            flash("Describe the emergency (maximum 5,000 characters).", "error")
            return redirect(url_for("patient.emergency_assistance"))

        if len(location) > 255 or len(contact_person) > 100 or len(contact_phone) > 20:
            flash("Please check the length of the contact and location fields.", "error")
            return redirect(url_for("patient.emergency_assistance"))

        case_number = (
            "MED-ER-"
            + datetime.now().strftime("%Y%m%d%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6].upper()
        )

        cursor = mysql.connection.cursor()
        try:
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
                    reported_by_user_id,
                    case_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Reported')
                """,
                (
                    case_number,
                    patient_id,
                    emergency_type,
                    severity,
                    description,
                    location or None,
                    contact_person or None,
                    contact_phone or None,
                    user["user_id"]
                )
            )
            mysql.connection.commit()
            flash(
                f"Emergency case {case_number} was recorded. "
                "Please contact hospital emergency services immediately if urgent.",
                "success"
            )
        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Patient emergency case submission failed.")
            flash("The emergency report could not be saved. Contact hospital staff directly.", "error")
        finally:
            cursor.close()

        return redirect(url_for("patient.emergency_assistance"))

    cases = _fetch_all(
        """
        SELECT
            emergency_case_id,
            case_number,
            emergency_type,
            severity,
            case_description,
            emergency_location,
            case_status,
            reported_at,
            resolved_at,
            resolution_notes
        FROM emergency_cases
        WHERE patient_id = %s
        ORDER BY reported_at DESC, emergency_case_id DESC
        LIMIT 100
        """,
        (patient_id,)
    )

    return render_template(
        "patient/emergency_assistance.html",
        cases=cases
    )

