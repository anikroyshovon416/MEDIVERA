
import os
import re
import uuid
from datetime import date, datetime

import MySQLdb
from flask import current_app
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from app.extensions import mysql


class RegistrationError(Exception):
    """Safe, user-friendly patient registration error."""


ALLOWED_GENDERS = {
    "Male",
    "Female",
    "Other",
    "Prefer not to say",
}

ALLOWED_BLOOD_GROUPS = {
    "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-",
}

MAX_FILE_SIZE = 4 * 1024 * 1024  # 4 MB per file
MAX_FILES_PER_CATEGORY = 2
MAX_TOTAL_UPLOAD_SIZE = 4 * 1024 * 1024


def _clean(value):
    return (value or "").strip()

def _validate_email(value):
    """Validate email syntax and normalize its domain."""
    value = _clean(value).lower()

    if not value or len(value) > 100:
        raise RegistrationError(
            "Please enter an email address of up to 100 characters."
        )

    if value.count("@") != 1:
        raise RegistrationError("Please enter a valid email address.")

    local_part, domain = value.rsplit("@", 1)

    if (
        not local_part
        or len(local_part) > 64
        or local_part.startswith(".")
        or local_part.endswith(".")
        or ".." in local_part
    ):
        raise RegistrationError("Please enter a valid email address.")

    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        raise RegistrationError("Please enter a valid email domain.")

    if len(domain) > 253 or "." not in domain:
        raise RegistrationError("Please enter a valid email domain.")

    labels = domain.split(".")

    for label in labels:
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[a-z0-9-]+", label)
        ):
            raise RegistrationError("Please enter a valid email domain.")

    if len(labels[-1]) < 2:
        raise RegistrationError("Please enter a valid email domain.")

    if not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",
        local_part,
    ):
        raise RegistrationError("Please enter a valid email address.")

    return f"{local_part}@{domain}"


def _validate_phone(value, field_name):
    value = _clean(value)

    if not re.fullmatch(r"\+?[0-9\s\-()]{7,20}", value):
        raise RegistrationError(
            f"Please enter a valid {field_name}."
        )

    digits = re.sub(r"\D", "", value)

    if not 7 <= len(digits) <= 15:
        raise RegistrationError(
            f"{field_name.capitalize()} must contain 7â€“15 digits."
        )

    return value


def _validate_form(form):
    first_name = _clean(form.get("first_name"))
    last_name = _clean(form.get("last_name"))
    date_of_birth = _clean(form.get("date_of_birth"))
    gender = _clean(form.get("gender"))
    blood_group = _clean(form.get("blood_group"))
    phone = _validate_phone(form.get("phone"), "phone number")
    email = _validate_email(form.get("email"))
    address = _clean(form.get("address"))
    emergency_name = _clean(form.get("emergency_contact_name"))
    emergency_phone = _clean(form.get("emergency_contact_phone"))
    username = _clean(form.get("username"))
    password = form.get("password", "")
    confirm_password = form.get("confirm_password", "")

    if not first_name or not last_name:
        raise RegistrationError("Please enter your first and last name.")

    if len(first_name) > 100 or len(last_name) > 100:
        raise RegistrationError("Names must not exceed 100 characters.")

    try:
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
    except ValueError:
        raise RegistrationError("Please enter a valid date of birth.")

    if dob > date.today():
        raise RegistrationError("Date of birth cannot be in the future.")

    if gender not in ALLOWED_GENDERS:
        raise RegistrationError("Please select a valid gender.")

    if blood_group and blood_group not in ALLOWED_BLOOD_GROUPS:
        raise RegistrationError("Please select a valid blood group.")

    # Empty or "I don't know" is stored as SQL NULL.
    if blood_group.lower() in {"i don't know", "unknown", "not known"}:
        blood_group = None

    if len(address) > 500:
        raise RegistrationError("Address must not exceed 500 characters.")

    if len(emergency_name) > 150:
        raise RegistrationError("Emergency contact name is too long.")

    if emergency_phone:
        emergency_phone = _validate_phone(
            emergency_phone, "emergency contact phone number"
        )
    elif emergency_name:
        raise RegistrationError(
            "Please enter the emergency contact phone number."
        )

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}", username):
        raise RegistrationError(
            "Username must be 3â€“50 characters and contain only "
            "letters, numbers, dots, underscores or hyphens."
        )

    if len(password) < 8:
        raise RegistrationError(
            "Password must contain at least 8 characters."
        )

    if len(password) > 128:
        raise RegistrationError("Password is too long.")

    if password != confirm_password:
        raise RegistrationError("Passwords do not match.")

    return {
        "first_name": first_name,
        "last_name": last_name,
        "date_of_birth": dob,
        "gender": gender,
        "blood_group": blood_group,
        "phone": phone,
        "email": email,
        "address": address or None,
        "emergency_contact_name": emergency_name or None,
        "emergency_contact_phone": emergency_phone or None,
        "username": username,
        "password_hash": generate_password_hash(password),
    }


def _inspect_upload(file_storage):
    """Read and validate file contents instead of trusting browser MIME."""

    original_name = secure_filename(file_storage.filename or "")

    if not original_name:
        raise RegistrationError("An uploaded file has an invalid filename.")

    data = file_storage.read(MAX_FILE_SIZE + 1)

    if not data:
        raise RegistrationError(f"{original_name} is empty.")

    if len(data) > MAX_FILE_SIZE:
        raise RegistrationError(
            f"{original_name} exceeds the 4 MB file limit."
        )

    if data.startswith(b"%PDF-"):
        mime_type = "application/pdf"
        extension = ".pdf"

    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime_type = "image/png"
        extension = ".png"

    elif data.startswith(b"\xff\xd8\xff"):
        mime_type = "image/jpeg"
        extension = ".jpg"

    elif (
        len(data) >= 12
        and data[0:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        mime_type = "image/webp"
        extension = ".webp"

    else:
        raise RegistrationError(
            f"{original_name} is not a supported PDF, JPEG, PNG or WEBP file."
        )

    return {
        "original_filename": original_name[:255],
        "data": data,
        "mime_type": mime_type,
        "extension": extension,
        "file_size": len(data),
    }


def _prepare_uploads(files):
    prepared = []
    total_size = 0

    categories = (
        ("medical_reports", "medical_report"),
        ("prescriptions", "prescription"),
    )

    for field_name, document_type in categories:
        selected_files = [
            item for item in files.getlist(field_name)
            if item and item.filename
        ]

        if len(selected_files) > MAX_FILES_PER_CATEGORY:
            raise RegistrationError(
                "You can upload a maximum of two files per document category."
            )

        for uploaded_file in selected_files:
            item = _inspect_upload(uploaded_file)
            total_size += item["file_size"]

            if total_size > MAX_TOTAL_UPLOAD_SIZE:
                raise RegistrationError(
                    "The combined upload size must not exceed 4 MB."
                )

            item["document_type"] = document_type
            item["stored_filename"] = (
                uuid.uuid4().hex + item["extension"]
            )
            prepared.append(item)

    return prepared


def create_patient_account(form, files):
    """
    Create a patient, login account, patient role and optional documents.

    Returns the new username after a successful database commit.
    Raises RegistrationError for safe, displayable errors.
    """

    patient = _validate_form(form)
    uploads = _prepare_uploads(files)

    upload_folder = current_app.config["PATIENT_UPLOAD_FOLDER"]
    os.makedirs(upload_folder, mode=0o700, exist_ok=True)

    connection = mysql.connection
    cursor = None
    saved_paths = []

    try:
        connection.begin()
        cursor = connection.cursor()

        # Check username, phone and optional email before inserting.
        cursor.execute(
            """
            SELECT user_id
            FROM users
            WHERE username = %s
            LIMIT 1
            """,
            (patient["username"],),
        )

        if cursor.fetchone():
            raise RegistrationError(
                "That username is already in use. Please choose another."
            )

        cursor.execute(
            """
            SELECT patient_id
            FROM patients
            WHERE phone = %s
            LIMIT 1
            """,
            (patient["phone"],),
        )

        if cursor.fetchone():
            raise RegistrationError(
                "That phone number is already registered."
            )

        if patient["email"]:
            cursor.execute(
                """
                SELECT patient_id
                FROM patients
                WHERE email = %s
                LIMIT 1
                """,
                (patient["email"],),
            )

            if cursor.fetchone():
                raise RegistrationError(
                    "That email address is already registered."
                )

            cursor.execute(
                """
                SELECT user_id
                FROM users
                WHERE email = %s
                LIMIT 1
                """,
                (patient["email"],),
            )

            if cursor.fetchone():
                raise RegistrationError(
                    "That email address is already linked to an account."
                )

        # Use the existing active patient role.
        cursor.execute(
            """
            SELECT role_id
            FROM roles
            WHERE role_name = 'patient'
              AND is_active = 1
            LIMIT 1
            """
        )

        role = cursor.fetchone()

        if not role:
            raise RegistrationError(
                "Patient registration is temporarily unavailable."
            )

        role_id = role["role_id"]

        # 1. Create the patient record.
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                patient["first_name"],
                patient["last_name"],
                patient["date_of_birth"],
                patient["gender"],
                patient["blood_group"],
                patient["phone"],
                patient["email"],
                patient["address"],
                patient["emergency_contact_name"],
                patient["emergency_contact_phone"],
            ),
        )

        patient_id = cursor.lastrowid

        # 2. Create the login account.
        cursor.execute(
            """
            INSERT INTO users (
                username,
                password_hash,
                email,
                role,
                is_active,
                patient_id
            )
            VALUES (%s, %s, %s, 'patient', 1, %s)
            """,
            (
                patient["username"],
                patient["password_hash"],
                patient["email"],
                patient_id,
            ),
        )

        user_id = cursor.lastrowid

        # 3. Assign the existing patient RBAC role.
        cursor.execute(
            """
            INSERT INTO user_roles (
                user_id,
                role_id,
                assigned_by
            )
            VALUES (%s, %s, NULL)
            """,
            (user_id, role_id),
        )

        # 4. Save optional documents privately and store metadata.
        for item in uploads:
            stored_filename = item["stored_filename"]
            destination = os.path.join(
                upload_folder,
                stored_filename,
            )

            with open(destination, "xb") as document_file:
                document_file.write(item["data"])

            saved_paths.append(destination)

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
                    user_id,
                    item["document_type"],
                    item["original_filename"],
                    stored_filename,
                    item["mime_type"],
                    item["file_size"],
                ),
            )

        # All database operations succeed together.
        connection.commit()

        return {
            "user_id": user_id,
            "username": patient["username"],
            "email": patient["email"],
        }

    except RegistrationError:
        connection.rollback()

        for path in saved_paths:
            try:
                os.remove(path)
            except OSError:
                current_app.logger.exception(
                    "Could not remove an incomplete patient upload."
                )

        raise

    except MySQLdb.IntegrityError:
        connection.rollback()

        for path in saved_paths:
            try:
                os.remove(path)
            except OSError:
                current_app.logger.exception(
                    "Could not remove an incomplete patient upload."
                )

        raise RegistrationError(
            "Some information is already registered. "
            "Please check the username, phone number and email."
        )

    except Exception:
        connection.rollback()

        for path in saved_paths:
            try:
                os.remove(path)
            except OSError:
                current_app.logger.exception(
                    "Could not remove an incomplete patient upload."
                )

        current_app.logger.exception(
            "Patient registration failed."
        )

        raise RegistrationError(
            "Registration could not be completed. Please try again."
        )

    finally:
        if cursor is not None:
            cursor.close()
