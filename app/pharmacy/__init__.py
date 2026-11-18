from flask import Blueprint

# MEDIVERA Pharmacy Module
pharmacy_bp = Blueprint(
    "pharmacy",
    __name__,
    url_prefix="/pharmacy"
)

# Patient-facing pharmacy routes
from app.pharmacy import routes

# Pharmacy Admin routes
from app.pharmacy import admin_routes
