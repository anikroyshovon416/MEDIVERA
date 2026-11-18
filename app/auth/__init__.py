
from flask import Blueprint

# Create the authentication blueprint exactly once.
auth_bp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth"
)

# Import routes after creating the blueprint.
# routes.py attaches its endpoints to this shared object.
from app.auth import routes  # noqa: E402, F401
