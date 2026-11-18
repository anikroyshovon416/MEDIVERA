from flask import Blueprint

admin_bp = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
)

# Register the main administration routes.
from app.admin import routes  # noqa: E402, F401

# Register emergency and ambulance management routes.
from app.admin import emergency_routes  # noqa: E402, F401