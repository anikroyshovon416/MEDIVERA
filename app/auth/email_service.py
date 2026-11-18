
import hashlib
import html
import secrets
import smtplib
import ssl

from email.message import EmailMessage

from flask import current_app, url_for

from app.extensions import mysql


def _get_mail_settings():
    """Read and validate MEDIVERA's SMTP configuration."""
    config = current_app.config

    required = (
        "MAIL_SERVER",
        "MAIL_PORT",
        "MAIL_USERNAME",
        "MAIL_PASSWORD",
        "MAIL_DEFAULT_SENDER",
    )

    missing = [
        key for key in required
        if not config.get(key)
    ]

    if missing:
        raise RuntimeError(
            "Email service configuration is incomplete: "
            + ", ".join(missing)
        )

    return config


def send_verification_email(user_id, email, username):
    """
    Generate a one-time verification token, store only its SHA-256
    hash, and send the verification link to the user's email.
    """
    config = _get_mail_settings()

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    expiry_minutes = int(
        config.get("EMAIL_VERIFICATION_TOKEN_MINUTES", 30)
    )

    if expiry_minutes < 1:
        raise ValueError("Verification token expiry must be positive.")

    cursor = mysql.connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE users
            SET
                email_verified_at = NULL,
                email_verification_token_hash = %s,
                email_verification_expires_at =
                    DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s MINUTE),
                email_verification_sent_at = UTC_TIMESTAMP()
            WHERE user_id = %s
              AND email = %s
              AND is_active = 1
            """,
            (token_hash, expiry_minutes, user_id, email),
        )

        if cursor.rowcount != 1:
            mysql.connection.rollback()
            raise RuntimeError(
                "The account could not be prepared for email verification."
            )

        mysql.connection.commit()

    except Exception:
        mysql.connection.rollback()
        raise

    finally:
        cursor.close()

    verification_url = url_for(
        "auth.verify_email",
        token=token,
        _external=True,
    )

    safe_username = html.escape(username or "MEDIVERA user")
    safe_url = html.escape(verification_url, quote=True)

    message = EmailMessage()
    message["Subject"] = "Verify your MEDIVERA email address"
    message["From"] = config["MAIL_DEFAULT_SENDER"]
    message["To"] = email

    message.set_content(
        f"""Hello {username},

Thank you for registering with MEDIVERA.

Please verify your email address using this link:

{verification_url}

This link expires in {expiry_minutes} minutes and can only be used once.

If you did not request this account, you can ignore this email.

MEDIVERA
Intelligent Healthcare. Connected Care. Better Tomorrow.
"""
    )

    message.add_alternative(
        f"""\
<!DOCTYPE html>
<html lang="en">
<body style="font-family:Arial,sans-serif;color:#222;">
    <h2>Welcome to MEDIVERA</h2>
    <p>Hello {safe_username},</p>
    <p>Please verify your email address to activate your account.</p>
    <p>
        <a href="{safe_url}"
           style="display:inline-block;padding:12px 22px;
                  background:#f28c28;color:white;
                  text-decoration:none;border-radius:6px;">
            Verify Email Address
        </a>
    </p>
    <p>This link expires in {expiry_minutes} minutes and can only be used once.</p>
    <p>If you did not request this account, ignore this email.</p>
    <p><strong>MEDIVERA</strong><br>
       Intelligent Healthcare. Connected Care. Better Tomorrow.</p>
</body>
</html>
""",
        subtype="html",
    )

    try:
        context = ssl.create_default_context()

        with smtplib.SMTP(
            config["MAIL_SERVER"],
            int(config["MAIL_PORT"]),
            timeout=20,
        ) as server:

            if config.get("MAIL_USE_TLS", True):
                server.starttls(context=context)

            server.login(
                config["MAIL_USERNAME"],
                config["MAIL_PASSWORD"],
            )

            server.send_message(message)

    except Exception:
        current_app.logger.exception(
            "MEDIVERA verification email delivery failed for user_id=%s",
            user_id,
        )
        raise

    return True

def send_account_credentials(email, username, temporary_password, role_name):
    """
    Send newly created account credentials to the user's email.

    The password is sent only to the intended recipient.
    Only its Werkzeug hash is stored in the database.
    """
    config = _get_mail_settings()

    safe_username = html.escape(username or "MEDIVERA user")
    safe_role = html.escape(role_name or "MEDIVERA user")
    safe_email = html.escape(email, quote=True)

    message = EmailMessage()
    message["Subject"] = "Your MEDIVERA account is ready"
    message["From"] = config["MAIL_DEFAULT_SENDER"]
    message["To"] = email

    message.set_content(
        f"""Hello {username},

Your MEDIVERA account has been created.

Login details:
Username: {username}
Temporary password: {temporary_password}
Assigned role: {role_name}

Please sign in to MEDIVERA and change this temporary password
as soon as possible.

Keep these credentials private. Do not share them with anyone.

MEDIVERA
Intelligent Healthcare. Connected Care. Better Tomorrow.
"""
    )

    message.add_alternative(
        f"""\
<!DOCTYPE html>
<html lang="en">
<body style="font-family:Arial,sans-serif;color:#222;">
    <div style="max-width:600px;margin:auto;padding:24px;
                border:1px solid #ddd;border-radius:12px;">
        <h2 style="color:#e98224;">Welcome to MEDIVERA</h2>
        <p>Hello {safe_username},</p>
        <p>Your MEDIVERA account has been created.</p>

        <div style="background:#f5f5f5;padding:18px;border-radius:8px;">
            <p><strong>Username:</strong> {safe_username}</p>
            <p><strong>Email:</strong> {safe_email}</p>
            <p><strong>Assigned role:</strong> {safe_role}</p>
            <p><strong>Temporary password:</strong></p>
            <p style="font-size:18px;font-weight:bold;
                      letter-spacing:1px;color:#b85b0a;">
                {html.escape(temporary_password)}
            </p>
        </div>

        <p>Please sign in and change your temporary password
           as soon as possible.</p>
        <p>Keep these credentials private. Do not share them.</p>

        <p><strong>MEDIVERA</strong><br>
        Intelligent Healthcare. Connected Care. Better Tomorrow.</p>
    </div>
</body>
</html>
""",
        subtype="html",
    )

    try:
        context = ssl.create_default_context()

        with smtplib.SMTP(
            config["MAIL_SERVER"],
            int(config["MAIL_PORT"]),
            timeout=20,
        ) as server:
            if config.get("MAIL_USE_TLS", True):
                server.starttls(context=context)

            server.login(
                config["MAIL_USERNAME"],
                config["MAIL_PASSWORD"],
            )

            server.send_message(message)

    except Exception:
        current_app.logger.exception(
            "MEDIVERA account-credentials email failed."
        )
        raise

    return True
