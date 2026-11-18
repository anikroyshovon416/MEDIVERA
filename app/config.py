import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Environment variables supplied by the hosting platform take precedence.
load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    IS_PRODUCTION = False
    SECRET_KEY = os.getenv("SECRET_KEY")
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY is missing. Create a local .env file using .env.example "
            "and set a long random secret."
        )

    DEBUG = env_bool("FLASK_DEBUG", False)
    TESTING = False

    SESSION_COOKIE_NAME = "medivera_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_PATH = "/"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = True

    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DB = os.getenv("MYSQL_DB", "smart_healthcare")
    MYSQL_CURSORCLASS = "DictCursor"
    MYSQL_CHARSET = "utf8mb4"
    MYSQL_CONNECT_TIMEOUT = 10

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    # MEDIVERA EMAIL VERIFICATION
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER") or MAIL_USERNAME

    EMAIL_VERIFICATION_TOKEN_MINUTES = int(
        os.getenv("EMAIL_VERIFICATION_TOKEN_MINUTES", "30")
    )
    EMAIL_VERIFICATION_RESEND_SECONDS = int(
        os.getenv("EMAIL_VERIFICATION_RESEND_SECONDS", "60")
    )

    # Keep disabled until Super Admin email verification is tested.
    EMAIL_VERIFICATION_ENFORCED = False

    APP_NAME = "MEDIVERA"
    APP_FULL_NAME = "Smart Healthcare Management System"
    APP_TAGLINE = "Intelligent Healthcare. Connected Care. Better Tomorrow."
    TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")

    ITEMS_PER_PAGE = 10
    MAX_ITEMS_PER_PAGE = 100
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    PATIENT_UPLOAD_FOLDER = os.path.join(
    BASE_DIR,
    "instance",
    "private_uploads",
    "patient_documents"
)


class DevelopmentConfig(Config):
    DEBUG = env_bool("FLASK_DEBUG", True)
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    IS_PRODUCTION = True
    DEBUG = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
