
from flask import Blueprint

# Create the patient blueprint exactly once.
patient_bp = Blueprint(
    "patient",
    __name__,
    url_prefix="/patient"
)

# Import routes after creating the blueprint.
from app.patients import routes  # noqa: E402, F401
