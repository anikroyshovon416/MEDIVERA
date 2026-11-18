
"""
MEDIVERA Central Activity Logger

Writes important application events to the existing activity_logs table.
Logging failures must never interrupt the main application workflow.
"""

from flask import current_app, has_request_context, request

from app.extensions import mysql


def log_activity(
    action,
    module,
    description=None,
    user_id=None,
):
    """
    Record one activity in activity_logs.

    Parameters:
        action: Short event name, maximum 100 characters.
        module: MEDIVERA module name, maximum 100 characters.
        description: Optional explanation of the event.
        user_id: User ID; None represents an anonymous/system event.

    Returns:
        True if the record was inserted, otherwise False.
    """

    cursor = None

    try:
        # Validate required fields.
        action = str(action or "").strip()[:100]
        module = str(module or "").strip()[:100]

        if not action or not module:
            current_app.logger.warning(
                "Activity logging skipped: action or module is empty."
            )
            return False

        # Use the current request IP when available.
        ip_address = None

        if has_request_context():
            ip_address = request.remote_addr

        # Insert into the existing database table.
        cursor = mysql.connection.cursor()

        cursor.execute(
            """
            INSERT INTO activity_logs (
                user_id,
                action,
                module,
                description,
                ip_address
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                user_id,
                action,
                module,
                description,
                ip_address,
            ),
        )

        mysql.connection.commit()

        current_app.logger.debug(
            "Activity recorded: action=%s module=%s user_id=%s",
            action,
            module,
            user_id,
        )

        return True

    except Exception:
        # Logging must not crash the user's main action.
        try:
            mysql.connection.rollback()
        except Exception:
            current_app.logger.exception(
                "Rollback failed after activity logging error."
            )

        current_app.logger.exception(
            "Unable to record MEDIVERA activity."
        )

        return False

    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                current_app.logger.exception(
                    "Unable to close activity logger cursor."
                )
