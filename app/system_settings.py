
import json
import os
import re
import tempfile
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SETTINGS_FILENAME = "medivera_settings.json"

# Only these settings can be changed from the Super Admin dashboard.
# Database credentials, SECRET_KEY, CSRF, and security cookie flags
# are deliberately excluded.
ALLOWED_TIMEZONES = {
    "UTC",
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Dhaka",
    "Asia/Kathmandu",
    "Europe/London",
    "Europe/Paris",
    "America/New_York",
    "America/Los_Angeles",
    "Australia/Sydney",
}

UPLOAD_SIZE_OPTIONS = {5, 10, 20, 50, 100}


def _default_settings(config):
    """Build safe editable defaults from the active Flask configuration."""

    return {
        "APP_NAME": config.get("APP_NAME", "MEDIVERA"),
        "APP_FULL_NAME": config.get(
            "APP_FULL_NAME",
            "Smart Healthcare Management System",
        ),
        "APP_TAGLINE": config.get(
            "APP_TAGLINE",
            "Intelligent Healthcare. Connected Care. Better Tomorrow.",
        ),
        "TIMEZONE": config.get("TIMEZONE", "Asia/Kolkata"),
        "HOSPITAL_NAME": config.get("HOSPITAL_NAME", "MEDIVERA Hospital"),
        "HOSPITAL_ADDRESS": config.get("HOSPITAL_ADDRESS", ""),
        "HOSPITAL_PHONE": config.get("HOSPITAL_PHONE", ""),
        "HOSPITAL_EMAIL": config.get("HOSPITAL_EMAIL", ""),
        "MAX_UPLOAD_MB": int(
            config.get("MAX_CONTENT_LENGTH", 5 * 1024 * 1024)
            / (1024 * 1024)
        ),
        "ITEMS_PER_PAGE": int(config.get("ITEMS_PER_PAGE", 10)),
        "MAX_ITEMS_PER_PAGE": int(
            config.get("MAX_ITEMS_PER_PAGE", 100)
        ),
        "SESSION_DURATION_HOURS": int(
            config.get("SESSION_DURATION_HOURS", 8)
        ),
    }
def get_current_settings(app):
    """Return the current editable settings from Flask configuration."""
    return _default_settings(app.config)


def _clean_text(value, label, maximum, required=False):
    value = str(value or "").strip()

    if required and not value:
        raise ValueError(f"{label} is required.")

    if len(value) > maximum:
        raise ValueError(
            f"{label} cannot exceed {maximum} characters."
        )

    return value


def validate_settings(values, current_config):
    """
    Validate submitted settings and return normalized values.

    Raises ValueError with a user-readable message when invalid.
    """

    defaults = _default_settings(current_config)

    app_name = _clean_text(
        values.get("APP_NAME", defaults["APP_NAME"]),
        "Application name",
        80,
        required=True,
    )

    full_name = _clean_text(
        values.get("APP_FULL_NAME", defaults["APP_FULL_NAME"]),
        "Full application name",
        150,
        required=True,
    )

    tagline = _clean_text(
        values.get("APP_TAGLINE", defaults["APP_TAGLINE"]),
        "Application tagline",
        255,
    )

    timezone_name = _clean_text(
        values.get("TIMEZONE", defaults["TIMEZONE"]),
        "Timezone",
        80,
        required=True,
    )

    if timezone_name not in ALLOWED_TIMEZONES:
        # Permit the currently configured timezone as well.
        if timezone_name != str(defaults["TIMEZONE"]):
            raise ValueError("Please select a supported timezone.")

    # If the system has timezone data, verify the selected timezone.
    # On Windows installations without tzdata, the explicit allowlist
    # above remains the validation source.
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name not in ALLOWED_TIMEZONES:
            raise ValueError("The selected timezone is not available.")

    hospital_name = _clean_text(
        values.get("HOSPITAL_NAME", defaults["HOSPITAL_NAME"]),
        "Hospital name",
        150,
        required=True,
    )

    hospital_address = _clean_text(
        values.get(
            "HOSPITAL_ADDRESS",
            defaults["HOSPITAL_ADDRESS"],
        ),
        "Hospital address",
        300,
    )

    hospital_phone = _clean_text(
        values.get("HOSPITAL_PHONE", defaults["HOSPITAL_PHONE"]),
        "Hospital phone",
        30,
    )

    if hospital_phone and not re.fullmatch(
        r"[0-9+\-() ]{7,30}",
        hospital_phone,
    ):
        raise ValueError("Please enter a valid hospital phone number.")

    hospital_email = _clean_text(
        values.get("HOSPITAL_EMAIL", defaults["HOSPITAL_EMAIL"]),
        "Hospital email",
        150,
    )

    if hospital_email and not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        hospital_email,
    ):
        raise ValueError("Please enter a valid hospital email address.")

    try:
        upload_mb = int(
            values.get("MAX_UPLOAD_MB", defaults["MAX_UPLOAD_MB"])
        )
        items_per_page = int(
            values.get("ITEMS_PER_PAGE", defaults["ITEMS_PER_PAGE"])
        )
        max_items_per_page = int(
            values.get(
                "MAX_ITEMS_PER_PAGE",
                defaults["MAX_ITEMS_PER_PAGE"],
            )
        )
        session_hours = int(
            values.get(
                "SESSION_DURATION_HOURS",
                defaults["SESSION_DURATION_HOURS"],
            )
        )
    except (TypeError, ValueError):
        raise ValueError("Numeric settings must contain valid numbers.")

    if upload_mb not in UPLOAD_SIZE_OPTIONS:
        raise ValueError(
            "Maximum upload size must be 5, 10, 20, 50, or 100 MB."
        )

    if not 5 <= items_per_page <= 100:
        raise ValueError("Items per page must be between 5 and 100.")

    if not 10 <= max_items_per_page <= 500:
        raise ValueError(
            "Maximum pagination limit must be between 10 and 500."
        )

    if max_items_per_page < items_per_page:
        raise ValueError(
            "Maximum pagination limit cannot be smaller than "
            "items per page."
        )

    if not 1 <= session_hours <= 24:
        raise ValueError(
            "Session duration must be between 1 and 24 hours."
        )

    return {
        "APP_NAME": app_name,
        "APP_FULL_NAME": full_name,
        "APP_TAGLINE": tagline,
        "TIMEZONE": timezone_name,
        "HOSPITAL_NAME": hospital_name,
        "HOSPITAL_ADDRESS": hospital_address,
        "HOSPITAL_PHONE": hospital_phone,
        "HOSPITAL_EMAIL": hospital_email,
        "MAX_UPLOAD_MB": upload_mb,
        "ITEMS_PER_PAGE": items_per_page,
        "MAX_ITEMS_PER_PAGE": max_items_per_page,
        "SESSION_DURATION_HOURS": session_hours,
    }


def apply_settings_to_app(app, settings):
    """Apply validated settings to the running Flask application."""

    app.config["APP_NAME"] = settings["APP_NAME"]
    app.config["APP_FULL_NAME"] = settings["APP_FULL_NAME"]
    app.config["APP_TAGLINE"] = settings["APP_TAGLINE"]
    app.config["TIMEZONE"] = settings["TIMEZONE"]

    app.config["HOSPITAL_NAME"] = settings["HOSPITAL_NAME"]
    app.config["HOSPITAL_ADDRESS"] = settings["HOSPITAL_ADDRESS"]
    app.config["HOSPITAL_PHONE"] = settings["HOSPITAL_PHONE"]
    app.config["HOSPITAL_EMAIL"] = settings["HOSPITAL_EMAIL"]

    app.config["MAX_CONTENT_LENGTH"] = (
        settings["MAX_UPLOAD_MB"] * 1024 * 1024
    )

    app.config["ITEMS_PER_PAGE"] = settings["ITEMS_PER_PAGE"]
    app.config["MAX_ITEMS_PER_PAGE"] = (
        settings["MAX_ITEMS_PER_PAGE"]
    )

    app.config["SESSION_DURATION_HOURS"] = (
        settings["SESSION_DURATION_HOURS"]
    )

    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        hours=settings["SESSION_DURATION_HOURS"]
    )


def load_persistent_settings(app):
    """
    Load settings from the private instance directory.

    Missing or invalid files do not prevent the application from starting.
    """

    os.makedirs(app.instance_path, exist_ok=True)

    path = os.path.join(app.instance_path, SETTINGS_FILENAME)
    settings = _default_settings(app.config)

    if not os.path.isfile(path):
        apply_settings_to_app(app, settings)
        return

    try:
        with open(path, "r", encoding="utf-8") as file:
            stored = json.load(file)

        if not isinstance(stored, dict):
            raise ValueError("Settings file must contain a JSON object.")

        stored_values = stored.get("settings", {})

        if not isinstance(stored_values, dict):
            raise ValueError("Settings data is invalid.")

        merged = {**settings, **stored_values}
        validated = validate_settings(merged, app.config)
        apply_settings_to_app(app, validated)

    except Exception:
        app.logger.exception(
            "Persistent MEDIVERA settings could not be loaded. "
            "Using application defaults."
        )
        apply_settings_to_app(app, settings)


def save_persistent_settings(app, settings):
    """
    Save validated settings atomically and apply them to Flask.

    The temporary file is created in the same directory so os.replace()
    can safely replace the previous file on the same filesystem.
    """

    os.makedirs(app.instance_path, exist_ok=True)

    destination = os.path.join(
        app.instance_path,
        SETTINGS_FILENAME,
    )

    payload = {
        "version": 1,
        "settings": settings,
    }

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            prefix="medivera_settings_",
            dir=app.instance_path,
            delete=False,
        ) as temporary_file:
            json.dump(
                payload,
                temporary_file,
                indent=4,
                ensure_ascii=False,
            )
            temporary_file.write("\n")
            temporary_path = temporary_file.name

        os.replace(temporary_path, destination)
        temporary_path = None

        apply_settings_to_app(app, settings)

    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
