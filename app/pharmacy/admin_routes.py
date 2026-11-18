import os
import uuid
import mimetypes

from flask import send_file

from werkzeug.utils import secure_filename
from datetime import date
from werkzeug.exceptions import HTTPException
from datetime import date, timedelta
from decimal import Decimal

from flask import (
    render_template,
    request,
    abort,
    current_app,
)
from flask import flash, redirect, url_for

from app.extensions import mysql
from app.auth.decorators import (
    login_required,
    get_current_user,
    get_user_roles,
)

from app.pharmacy import pharmacy_bp


# ============================================================
# MEDIVERA PHARMACY ADMINISTRATION
# Premium Hospital Pharmacy Management
# ============================================================


# ============================================================
# PHARMACY ADMIN ACCESS CONTROL
# ============================================================

PHARMACY_ACCESS_ROLES = {
    "super_admin",
    "pharmacy_admin",
}

PHARMACY_PERMISSION_KEYS = {
    "inventory.view",
    "inventory.manage",
    "prescriptions.view",
    "prescriptions.manage",
    "pharmacy.dispense",
}

ORDER_STATUSES = [
    "Requested",
    "Approved",
    "Processing",
    "Ready",
    "Delivered",
    "Rejected",
    "Cancelled",
]


def pharmacy_admin_required():
    """
    Authorize the MEDIVERA Pharmacy Admin workspace.

    Super Admin:
        Full access to pharmacy administration.

    Pharmacy Admin:
        Access to the dedicated pharmacy workspace.

    Pharmacist:
        Operational role; does not automatically receive
        Pharmacy Admin dashboard access.
    """

    user = get_current_user()

    if not user:
        abort(401)

    if not user.get("is_active"):
        abort(403)

    roles = set(get_user_roles() or [])

    if "super_admin" in roles:
        return user, roles

    if "pharmacy_admin" not in roles:
        abort(403)

    return user, roles


def pharmacy_fetch_all(query, params=None):
    """
    Execute a SELECT query and return dictionary rows.
    """

    cursor = mysql.connection.cursor()

    try:
        cursor.execute(query, params or ())
        return cursor.fetchall()

    finally:
        cursor.close()


def pharmacy_fetch_one(query, params=None):
    """
    Execute a SELECT query and return one dictionary row.
    """

    cursor = mysql.connection.cursor()

    try:
        cursor.execute(query, params or ())
        return cursor.fetchone()

    finally:
        cursor.close()


def pharmacy_scalar(query, params=None, default=0):
    """
    Return the first value from a query.
    """

    row = pharmacy_fetch_one(query, params)

    if not row:
        return default

    return next(iter(row.values()))

def pharmacy_admin_navigation():
    return [
        {
            "label": "Pharmacy Overview",
            "icon": "layout-dashboard",
            "endpoint": "pharmacy.admin_dashboard",
        },
        {
            "label": "Order Management",
            "icon": "shopping-cart",
            "endpoint": "pharmacy.admin_orders",
        },
        {
    "label": "Prescription Review",
    "icon": "file-check",
    "endpoint": "pharmacy.admin_prescriptions",
},
        {
            "label": "Medicine Catalogue",
            "icon": "pill",
            "endpoint": "pharmacy.admin_medicines",
        },
        {
            "label": "Inventory Management",
            "icon": "boxes",
            "endpoint": "pharmacy.admin_inventory",
        },
        {
            "label": "Stock Transactions",
            "icon": "history",
            "endpoint": "pharmacy.admin_transactions",
        },
    ]


# MODULE 3 — MEDICINE CATALOGUE
# ============================================================

PHARMACY_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


def pharmacy_save_medicine_image(file):
    """Save an optional medicine image and return its relative path."""
    if not file or not file.filename:
        return None

    original_name = secure_filename(file.filename)
    if not original_name or "." not in original_name:
        raise ValueError("Please select a valid image file.")

    extension = original_name.rsplit(".", 1)[1].lower()
    if extension not in PHARMACY_IMAGE_EXTENSIONS:
        raise ValueError("Image must be PNG, JPG, JPEG, or WEBP.")

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)

    if size > 5 * 1024 * 1024:
        raise ValueError("Medicine image must not exceed 5 MB.")

    upload_dir = os.path.join(
        current_app.static_folder, "uploads", "pharmacy"
    )
    os.makedirs(upload_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{extension}"
    file.save(os.path.join(upload_dir, filename))

    return f"uploads/pharmacy/{filename}"


def pharmacy_medicine_form_data():
    """Validate and normalize medicine catalogue form fields."""
    item_name = request.form.get("item_name", "").strip()
    generic_name = request.form.get("generic_name", "").strip()
    brand_name = request.form.get("brand_name", "").strip()
    strength = request.form.get("strength", "").strip()
    dosage_form = request.form.get("dosage_form", "").strip()
    manufacturer = request.form.get("manufacturer", "").strip()
    description = request.form.get("description", "").strip()
    storage_instructions = request.form.get(
        "storage_instructions", ""
    ).strip()

    if not item_name:
        raise ValueError("Medicine name is required.")

    if len(item_name) > 150:
        raise ValueError("Medicine name must be 150 characters or fewer.")

    try:
        unit_price = Decimal(request.form.get("unit_price", "0").strip())
        purchase_price = Decimal(
            request.form.get("purchase_price", "0").strip() or "0"
        )
    except Exception:
        raise ValueError("Enter valid medicine prices.")

    if not unit_price.is_finite() or not purchase_price.is_finite():
        raise ValueError("Medicine prices must be valid numbers.")

    if unit_price < 0 or purchase_price < 0:
        raise ValueError("Medicine prices cannot be negative.")

    expiry_raw = request.form.get("expiry_date", "").strip()
    expiry_date = None

    if expiry_raw:
        try:
            expiry_date = date.fromisoformat(expiry_raw)
        except ValueError:
            raise ValueError("Enter a valid expiry date.")

    return {
        "item_name": item_name,
        "generic_name": generic_name or None,
        "brand_name": brand_name or None,
        "strength": strength or None,
        "dosage_form": dosage_form or None,
        "manufacturer": manufacturer or None,
        "description": description or None,
        "storage_instructions": storage_instructions or None,
        "unit_price": unit_price,
        "purchase_price": purchase_price,
        "expiry_date": expiry_date,
        "requires_prescription": (
            1 if request.form.get("requires_prescription") == "1" else 0
        ),
    }


@pharmacy_bp.get("/admin/medicines")
@login_required
def admin_medicines():
    """Searchable, filterable and paginated medicine catalogue."""
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "active").strip().lower()
    dosage_form = request.args.get("dosage_form", "").strip()

    if status not in {"active", "inactive", "all"}:
        status = "active"

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    per_page = 10
    conditions = ["LOWER(item_category) = 'medicine'"]
    params = []

    if status == "active":
        conditions.append("is_active = 1")
    elif status == "inactive":
        conditions.append("is_active = 0")

    if search:
        conditions.append("""
            (
                item_name LIKE %s
                OR generic_name LIKE %s
                OR brand_name LIKE %s
                OR manufacturer LIKE %s
            )
        """)
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern, pattern])

    if dosage_form:
        conditions.append("dosage_form = %s")
        params.append(dosage_form)

    where_sql = " AND ".join(conditions)

    total = pharmacy_scalar(
        f"SELECT COUNT(*) AS total FROM inventory WHERE {where_sql}",
        params,
        default=0,
    )
    total = int(total or 0)

    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    medicines = pharmacy_fetch_all(
        f"""
        SELECT
            inventory_id,
            item_name,
            generic_name,
            brand_name,
            strength,
            dosage_form,
            manufacturer,
            quantity,
            unit,
            reorder_level,
            unit_price,
            purchase_price,
            expiry_date,
            stock_status,
            medicine_image,
            requires_prescription,
            is_active
        FROM inventory
        WHERE {where_sql}
        ORDER BY item_name ASC, inventory_id DESC
        LIMIT %s OFFSET %s
        """,
        params + [per_page, offset],
    )

    dosage_forms = pharmacy_fetch_all(
        """
        SELECT DISTINCT dosage_form
        FROM inventory
        WHERE LOWER(item_category) = 'medicine'
          AND dosage_form IS NOT NULL
          AND dosage_form <> ''
        ORDER BY dosage_form
        """
    )

    return render_template(
        "pharmacy/admin_medicines.html",
        medicines=medicines,
        search=search,
        status=status,
        dosage_form=dosage_form,
        dosage_forms=dosage_forms,
        page=page,
        total_pages=total_pages,
        total=total,
        user=user,
        roles=roles,
      nav_items=pharmacy_admin_navigation(),
is_super_admin=("super_admin" in roles),
    )


@pharmacy_bp.route("/admin/medicines/add", methods=["GET", "POST"])
@login_required
def add_medicine():
    """Create a medicine catalogue entry without altering stock."""
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])
    if request.method == "POST":
        cursor = None
        try:
            data = pharmacy_medicine_form_data()

            image_path = pharmacy_save_medicine_image(
                request.files.get("medicine_image")
            )

            cursor = mysql.connection.cursor()
            cursor.execute(
                """
                INSERT INTO inventory (
                    item_name,
                    item_category,
                    quantity,
                    unit,
                    reorder_level,
                    unit_price,
                    expiry_date,
                    stock_status,
                    medicine_image,
                    generic_name,
                    brand_name,
                    strength,
                    dosage_form,
                    manufacturer,
                    description,
                    purchase_price,
                    requires_prescription,
                    storage_instructions,
                    is_active
                )
                VALUES (
                    %s, 'Medicine', 0, 'unit', 10, %s, %s,
                    'out_of_stock', %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, 1
                )
                """,
                (
                    data["item_name"],
                    data["unit_price"],
                    data["expiry_date"],
                    image_path,
                    data["generic_name"],
                    data["brand_name"],
                    data["strength"],
                    data["dosage_form"],
                    data["manufacturer"],
                    data["description"],
                    data["purchase_price"],
                    data["requires_prescription"],
                    data["storage_instructions"],
                ),
            )
            mysql.connection.commit()
            flash(
                "Medicine added to the catalogue. Initial stock remains zero.",
                "success",
            )
            return redirect(url_for("pharmacy.admin_medicines"))

        except ValueError as exc:
            mysql.connection.rollback()
            flash(str(exc), "danger")
        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Medicine creation failed.")
            flash("Could not add the medicine. Please check the server log.", "danger")
        finally:
            if cursor:
                cursor.close()

    return render_template(
        "pharmacy/admin_medicine_form.html",
        medicine=None,
        page_title="Add Medicine",
        user=user,
        roles=roles,
       nav_items=pharmacy_admin_navigation(),
is_super_admin=("super_admin" in roles),
    )


@pharmacy_bp.route(
    "/admin/medicines/<int:inventory_id>/edit",
    methods=["GET", "POST"],
)
@login_required
def edit_medicine(inventory_id):
    """Edit catalogue details without changing stock quantities."""
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])
    medicine = pharmacy_fetch_one(
        """
        SELECT *
        FROM inventory
        WHERE inventory_id = %s
          AND LOWER(item_category) = 'medicine'
        """,
        (inventory_id,),
    )

    if not medicine:
        abort(404)

    if request.method == "POST":
        cursor = None
        try:
            data = pharmacy_medicine_form_data()

            uploaded_image = pharmacy_save_medicine_image(
                request.files.get("medicine_image")
            )
            image_path = uploaded_image or medicine.get("medicine_image")

            cursor = mysql.connection.cursor()
            cursor.execute(
                """
                UPDATE inventory
                SET
                    item_name = %s,
                    generic_name = %s,
                    brand_name = %s,
                    strength = %s,
                    dosage_form = %s,
                    manufacturer = %s,
                    description = %s,
                    storage_instructions = %s,
                    unit_price = %s,
                    purchase_price = %s,
                    expiry_date = %s,
                    requires_prescription = %s,
                    medicine_image = %s
                WHERE inventory_id = %s
                  AND LOWER(item_category) = 'medicine'
                """,
                (
                    data["item_name"],
                    data["generic_name"],
                    data["brand_name"],
                    data["strength"],
                    data["dosage_form"],
                    data["manufacturer"],
                    data["description"],
                    data["storage_instructions"],
                    data["unit_price"],
                    data["purchase_price"],
                    data["expiry_date"],
                    data["requires_prescription"],
                    image_path,
                    inventory_id,
                ),
            )
            mysql.connection.commit()
            flash("Medicine details updated successfully.", "success")
            return redirect(url_for("pharmacy.admin_medicines"))

        except ValueError as exc:
            mysql.connection.rollback()
            flash(str(exc), "danger")
        except Exception:
            mysql.connection.rollback()
            current_app.logger.exception("Medicine update failed.")
            flash("Could not update the medicine. Please check the server log.", "danger")
        finally:
            if cursor:
                cursor.close()

        medicine = pharmacy_fetch_one(
            "SELECT * FROM inventory WHERE inventory_id = %s",
            (inventory_id,),
        )

    return render_template(
        "pharmacy/admin_medicine_form.html",
        medicine=medicine,
        page_title="Edit Medicine",
        user=user,
        roles=roles,
       nav_items=pharmacy_admin_navigation(),
is_super_admin=("super_admin" in roles),
    )


@pharmacy_bp.post("/admin/medicines/<int:inventory_id>/toggle")
@login_required
def toggle_medicine_status(inventory_id):
    """Activate/deactivate a medicine without deleting its history."""
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])
    cursor = mysql.connection.cursor()
    try:
        cursor.execute(
            """
            SELECT is_active
            FROM inventory
            WHERE inventory_id = %s
              AND LOWER(item_category) = 'medicine'
            FOR UPDATE
            """,
            (inventory_id,),
        )
        medicine = cursor.fetchone()

        if not medicine:
            abort(404)

        current_status = medicine["is_active"]
        new_status = 0 if current_status else 1

        cursor.execute(
            """
            UPDATE inventory
            SET is_active = %s
            WHERE inventory_id = %s
              AND LOWER(item_category) = 'medicine'
            """,
            (new_status, inventory_id),
        )
        mysql.connection.commit()

        flash(
            "Medicine activated." if new_status else "Medicine deactivated.",
            "success",
        )

    except HTTPException:
        mysql.connection.rollback()
        raise
    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Medicine status update failed.")
        flash("Could not update medicine status.", "danger")
    finally:
        cursor.close()

    return redirect(url_for("pharmacy.admin_medicines"))
# ============================================================
# PHARMACY ADMIN DASHBOARD
# ============================================================

@pharmacy_bp.get("/admin")
@login_required
def admin_dashboard():
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])
    today = date.today()
    expiry_limit = today + timedelta(days=90)

    try:
        # ----------------------------------------------------
        # ORDER STATISTICS
        # ----------------------------------------------------

        order_stats = pharmacy_fetch_one("""
            SELECT
                COUNT(*) AS total_orders,

                COALESCE(SUM(
                    CASE WHEN DATE(ordered_at) = CURDATE()
                    THEN 1 ELSE 0 END
                ), 0) AS todays_orders,

                COALESCE(SUM(
                    CASE WHEN order_status = 'Requested'
                    THEN 1 ELSE 0 END
                ), 0) AS pending_orders,

                COALESCE(SUM(
                    CASE WHEN order_status = 'Approved'
                    THEN 1 ELSE 0 END
                ), 0) AS approved_orders,

                COALESCE(SUM(
                    CASE WHEN order_status = 'Processing'
                    THEN 1 ELSE 0 END
                ), 0) AS processing_orders,

                COALESCE(SUM(
                    CASE WHEN order_status = 'Ready'
                    THEN 1 ELSE 0 END
                ), 0) AS ready_orders,

                COALESCE(SUM(
                    CASE WHEN order_status = 'Delivered'
                    THEN 1 ELSE 0 END
                ), 0) AS delivered_orders,

                COALESCE(SUM(
                    CASE WHEN order_status = 'Rejected'
                    THEN 1 ELSE 0 END
                ), 0) AS rejected_orders

            FROM pharmacy_orders
        """)

        # ----------------------------------------------------
        # INVENTORY STATISTICS
        #
        # Catalogue count includes active + inactive records.
        # Operational stock metrics include active medicines.
        # ----------------------------------------------------

        inventory_stats = pharmacy_fetch_one("""
            SELECT

                COUNT(*) AS total_medicines,

                COALESCE(SUM(
                    CASE WHEN is_active = 1
                    THEN 1 ELSE 0 END
                ), 0) AS active_medicines,

                COALESCE(SUM(
                    CASE WHEN is_active = 0
                    THEN 1 ELSE 0 END
                ), 0) AS inactive_medicines,

                COALESCE(SUM(
                    CASE WHEN is_active = 1
                    THEN quantity ELSE 0 END
                ), 0) AS total_units,

                COALESCE(SUM(
                    CASE
                        WHEN is_active = 1 AND quantity <= 0
                        THEN 1 ELSE 0
                    END
                ), 0) AS out_of_stock,

                COALESCE(SUM(
                    CASE
                        WHEN is_active = 1
                         AND quantity > 0
                         AND quantity <= COALESCE(reorder_level, 10)
                        THEN 1 ELSE 0
                    END
                ), 0) AS low_stock,

                COALESCE(SUM(
                    CASE
                        WHEN is_active = 1
                         AND expiry_date IS NOT NULL
                         AND expiry_date < CURDATE()
                        THEN 1 ELSE 0
                    END
                ), 0) AS expired_medicines,

                COALESCE(SUM(
                    CASE
                        WHEN is_active = 1
                         AND expiry_date BETWEEN CURDATE()
                         AND DATE_ADD(CURDATE(), INTERVAL 90 DAY)
                        THEN 1 ELSE 0
                    END
                ), 0) AS expiring_soon,

                COALESCE(SUM(
                    CASE WHEN is_active = 1
                    THEN quantity * unit_price ELSE 0 END
                ), 0) AS stock_value

            FROM inventory

            WHERE LOWER(item_category) = 'medicine'
        """)

        # ----------------------------------------------------
        # DELIVERED PHARMACY ORDER VALUE
        # ----------------------------------------------------

        revenue_stats = pharmacy_fetch_one("""
            SELECT

                COALESCE(SUM(
                    CASE
                        WHEN order_status = 'Delivered'
                        THEN subtotal ELSE 0
                    END
                ), 0) AS delivered_order_value,

                COALESCE(SUM(
                    CASE
                        WHEN order_status = 'Delivered'
                         AND DATE(ordered_at) = CURDATE()
                        THEN subtotal ELSE 0
                    END
                ), 0) AS todays_delivered_value

            FROM pharmacy_orders
        """)

        # ----------------------------------------------------
        # RECENT ORDERS
        # ----------------------------------------------------

        recent_orders = pharmacy_fetch_all("""
            SELECT
                po.pharmacy_order_id,
                po.patient_id,
                po.order_status,
                po.delivery_method,
                po.delivery_location,
                po.subtotal,
                po.ordered_at,
                po.updated_at,

                p.first_name,
                p.last_name,
                p.phone,

                u.username AS ordered_by

            FROM pharmacy_orders po

            INNER JOIN patients p
                ON p.patient_id = po.patient_id

            LEFT JOIN users u
                ON u.user_id = po.ordered_by_user_id

            ORDER BY po.ordered_at DESC
            LIMIT 10
        """)

        # ----------------------------------------------------
        # ACTIVE LOW-STOCK MEDICINES
        # ----------------------------------------------------

        low_stock_items = pharmacy_fetch_all("""
            SELECT
                inventory_id,
                item_name,
                item_category,
                quantity,
                unit,
                reorder_level,
                unit_price,
                expiry_date,
                stock_status

            FROM inventory

            WHERE LOWER(item_category) = 'medicine'
              AND is_active = 1
              AND quantity > 0
              AND quantity <= COALESCE(reorder_level, 10)

            ORDER BY quantity ASC, item_name ASC
            LIMIT 10
        """)

        # ----------------------------------------------------
        # ACTIVE OUT-OF-STOCK MEDICINES
        # ----------------------------------------------------

        out_of_stock_items = pharmacy_fetch_all("""
            SELECT
                inventory_id,
                item_name,
                quantity,
                unit,
                unit_price,
                expiry_date

            FROM inventory

            WHERE LOWER(item_category) = 'medicine'
              AND is_active = 1
              AND quantity <= 0

            ORDER BY item_name ASC
            LIMIT 10
        """)

        # ----------------------------------------------------
        # ACTIVE MEDICINES EXPIRING WITHIN 90 DAYS
        # ----------------------------------------------------

        expiring_items = pharmacy_fetch_all("""
            SELECT
                inventory_id,
                item_name,
                quantity,
                unit,
                expiry_date,
                unit_price

            FROM inventory

            WHERE LOWER(item_category) = 'medicine'
              AND is_active = 1
              AND expiry_date BETWEEN CURDATE()
                  AND DATE_ADD(CURDATE(), INTERVAL 90 DAY)

            ORDER BY expiry_date ASC
            LIMIT 10
        """)

        # ----------------------------------------------------
        # ACTIVE EXPIRED MEDICINES
        # ----------------------------------------------------

        expired_items = pharmacy_fetch_all("""
            SELECT
                inventory_id,
                item_name,
                quantity,
                unit,
                expiry_date,
                unit_price

            FROM inventory

            WHERE LOWER(item_category) = 'medicine'
              AND is_active = 1
              AND expiry_date < CURDATE()

            ORDER BY expiry_date ASC
            LIMIT 10
        """)

        # ----------------------------------------------------
        # DELIVERY BREAKDOWN
        # ----------------------------------------------------

        delivery_breakdown = pharmacy_fetch_all("""
            SELECT
                delivery_method,
                COUNT(*) AS order_count,
                COALESCE(SUM(subtotal), 0) AS order_value

            FROM pharmacy_orders

            GROUP BY delivery_method
            ORDER BY order_count DESC
        """)

        # ----------------------------------------------------
        # ORDER STATUS BREAKDOWN
        # ----------------------------------------------------

        status_breakdown = pharmacy_fetch_all("""
            SELECT
                order_status,
                COUNT(*) AS order_count

            FROM pharmacy_orders

            GROUP BY order_status
            ORDER BY order_count DESC
        """)

        # ----------------------------------------------------
        # LAST SEVEN DAYS OF ORDER ACTIVITY
        # ----------------------------------------------------

        daily_activity = pharmacy_fetch_all("""
            SELECT
                DATE(ordered_at) AS order_date,
                COUNT(*) AS order_count,
                COALESCE(SUM(subtotal), 0) AS order_value

            FROM pharmacy_orders

            WHERE ordered_at >=
                DATE_SUB(CURDATE(), INTERVAL 6 DAY)

            GROUP BY DATE(ordered_at)
            ORDER BY order_date ASC
        """)

        # The current patient_documents schema has no
        # prescription verification/review status column.
        prescription_review_count = None

        # ----------------------------------------------------
        # DASHBOARD CONTEXT
        # ----------------------------------------------------

        dashboard_data = {
            "order_stats": order_stats or {},
            "inventory_stats": inventory_stats or {},
            "revenue_stats": revenue_stats or {},
            "recent_orders": recent_orders,
            "low_stock_items": low_stock_items,
            "out_of_stock_items": out_of_stock_items,
            "expiring_items": expiring_items,
            "expired_items": expired_items,
            "delivery_breakdown": delivery_breakdown,
            "status_breakdown": status_breakdown,
            "daily_activity": daily_activity,
            "prescription_review_count": prescription_review_count,
            "today": today,
            "expiry_limit": expiry_limit,
        }

        return render_template(
            "pharmacy/admin_dashboard.html",
            user=user,
            roles=roles,
            is_super_admin="super_admin" in roles,
            nav_items=pharmacy_admin_navigation(),
            dashboard=dashboard_data,
        )

    except Exception:
        current_app.logger.exception(
            "MEDIVERA Pharmacy Admin Dashboard error"
        )

        flash(
            "The Pharmacy dashboard could not load. "
            "Please try again or contact the system administrator.",
            "danger",
        )

        abort(503)



# ============================================================
# PHARMACY ORDER MANAGEMENT
# ============================================================

@pharmacy_bp.get("/admin/orders")
@login_required
def admin_orders():

    user = pharmacy_admin_required()

    roles = get_user_roles(user[0])
    search = request.args.get(
        "search",
        "",
        type=str
    ).strip()

    status = request.args.get(
        "status",
        "",
        type=str
    ).strip()

    delivery_method = request.args.get(
        "delivery",
        "",
        type=str
    ).strip()

    page = request.args.get(
        "page",
        1,
        type=int
    )

    page = max(page, 1)

    per_page = 20

    conditions = []
    params = []

    if search:

        conditions.append("""
            (
                CAST(po.pharmacy_order_id AS CHAR) LIKE %s

                OR p.first_name LIKE %s

                OR p.last_name LIKE %s

                OR p.phone LIKE %s

                OR po.delivery_location LIKE %s
            )
        """)

        term = f"%{search}%"

        params.extend([
            term,
            term,
            term,
            term,
            term,
        ])

    if status in ORDER_STATUSES:

        conditions.append(
            "po.order_status = %s"
        )

        params.append(status)

    if delivery_method in (
        "Bedside",
        "Pharmacy Collection"
    ):

        conditions.append(
            "po.delivery_method = %s"
        )

        params.append(delivery_method)

    where_sql = ""

    if conditions:

        where_sql = (
            "WHERE " + " AND ".join(conditions)
        )

    count_row = pharmacy_fetch_one(
        f"""
        SELECT COUNT(*) AS total

        FROM pharmacy_orders po

        INNER JOIN patients p
            ON p.patient_id = po.patient_id

        {where_sql}
        """,
        params,
    )

    total_orders = (
        count_row["total"]
        if count_row
        else 0
    )

    offset = (page - 1) * per_page

    orders = pharmacy_fetch_all(
        f"""
        SELECT

            po.pharmacy_order_id,
            po.patient_id,
            po.order_status,
            po.delivery_method,
            po.delivery_location,
            po.order_notes,
            po.subtotal,
            po.ordered_at,
            po.updated_at,

            p.first_name,
            p.last_name,
            p.phone,

            u.username AS ordered_by,

            (
                SELECT COUNT(*)

                FROM pharmacy_order_items poi

                WHERE poi.pharmacy_order_id =
                    po.pharmacy_order_id

            ) AS item_count

        FROM pharmacy_orders po

        INNER JOIN patients p
            ON p.patient_id = po.patient_id

        LEFT JOIN users u
            ON u.user_id = po.ordered_by_user_id

        {where_sql}

        ORDER BY po.ordered_at DESC

        LIMIT %s OFFSET %s
        """,
        params + [per_page, offset],
    )

    total_pages = (
        (total_orders + per_page - 1) // per_page
    )

    return render_template(
        "pharmacy/admin_orders.html",
        user=user,
        roles=roles,
        is_super_admin="super_admin" in roles,
        nav_items=pharmacy_admin_navigation(),
        orders=orders,
        search=search,
        status=status,
        delivery_method=delivery_method,
        statuses=ORDER_STATUSES,
        page=page,
        total_pages=total_pages,
        total_orders=total_orders,
    )


# ============================================================
# PHARMACY ORDER DETAIL
# ============================================================

@pharmacy_bp.get("/admin/orders/<int:order_id>")
@login_required
def admin_order_detail(order_id):

    user = pharmacy_admin_required()

    roles = get_user_roles(user[0])
    order = pharmacy_fetch_one(
        """
        SELECT

            po.pharmacy_order_id,
            po.patient_id,
            po.ordered_by_user_id,
            po.prescription_document_id,
            po.order_status,
            po.delivery_method,
            po.delivery_location,
            po.order_notes,
            po.subtotal,
            po.ordered_at,
            po.updated_at,

            p.first_name,
            p.last_name,
            p.phone,
            p.email,
            p.address,
            p.blood_group,

            u.username AS ordered_by

        FROM pharmacy_orders po

        INNER JOIN patients p
            ON p.patient_id = po.patient_id

        LEFT JOIN users u
            ON u.user_id = po.ordered_by_user_id

        WHERE po.pharmacy_order_id = %s

        LIMIT 1
        """,
        (order_id,),
    )

    if not order:

        abort(404)

    items = pharmacy_fetch_all(
        """
        SELECT

            poi.order_item_id,
            poi.inventory_id,
            poi.medicine_name,
            poi.quantity,
            poi.unit_price,
            poi.line_total,

            i.quantity AS current_stock,
            i.unit AS inventory_unit,
            i.expiry_date,
            i.stock_status

        FROM pharmacy_order_items poi

        LEFT JOIN inventory i
            ON i.inventory_id = poi.inventory_id

        WHERE poi.pharmacy_order_id = %s

        ORDER BY poi.order_item_id ASC
        """,
        (order_id,),
    )

    prescription = None

    if order.get("prescription_document_id"):

        prescription = pharmacy_fetch_one(
            """
            SELECT

                document_id,
                patient_id,
                document_type,
                original_filename,
                stored_filename,
                mime_type,
                file_size,
                created_at

            FROM patient_documents

            WHERE document_id = %s

              AND patient_id = %s

              AND document_type = 'prescription'

            LIMIT 1
            """,
            (
                order["prescription_document_id"],
                order["patient_id"],
            ),
        )

    return render_template(
        "pharmacy/admin_order_detail.html",
        user=user,
        roles=roles,
        is_super_admin="super_admin" in roles,
        nav_items=pharmacy_admin_navigation(),
        order=order,
        items=items,
        prescription=prescription,
        prescription_document=prescription,
        statuses=ORDER_STATUSES,
    )


# ============================================================
# MEDIVERA PHARMACY ADMIN MEMBER MANAGEMENT
# Super Admin only
# ============================================================

from werkzeug.security import generate_password_hash


def pharmacy_super_admin_required():
    """Restrict Pharmacy Admin membership management to Super Admin."""

    user = get_current_user()

    if not user:
        abort(401)

    if not user.get("is_active"):
        abort(403)

    roles = set(get_user_roles() or [])

    if "super_admin" not in roles:
        abort(403)

    return user


# MEDIVERA PHARMACY ADMIN MEMBER MANAGEMENT


@pharmacy_bp.get("/admin/members")
@login_required
def admin_members():
    """Display Pharmacy Admin members. Super Admin only."""

    user = pharmacy_super_admin_required()

    members = pharmacy_fetch_all("""
        SELECT
            u.user_id,
            u.username,
            u.email,
            u.is_active,
            u.created_at,
            s.staff_id,
            s.staff_name,
            s.phone,
            s.designation,
            s.employment_status,
            assigned.username AS assigned_by_username
        FROM users u
        INNER JOIN user_roles ur
            ON ur.user_id = u.user_id
        INNER JOIN roles r
            ON r.role_id = ur.role_id
        LEFT JOIN staff s
            ON s.user_id = u.user_id
        LEFT JOIN users assigned
            ON assigned.user_id = ur.assigned_by
        WHERE r.role_name = %s
        ORDER BY u.created_at DESC, u.user_id DESC
    """, ("pharmacy_admin",))

    active_count = sum(1 for member in members if member.get("is_active"))
    inactive_count = len(members) - active_count

    navigation = pharmacy_admin_navigation()
    navigation.append({
        "label": "Pharmacy Admin Members",
        "icon": "users",
        "endpoint": "pharmacy.admin_members",
    })

    return render_template(
        "pharmacy/admin_members.html",
        user=user,
        is_super_admin=True,
        nav_items=navigation,
        members=members,
        active_count=active_count,
        inactive_count=inactive_count,
        total_members=len(members),
    )


@pharmacy_bp.post("/admin/members/create")
@login_required
def create_admin_member():
    """Create a Pharmacy Admin login and linked staff record atomically."""

    actor = pharmacy_super_admin_required()

    staff_name = request.form.get("staff_name", "").strip()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip() or None
    gender = request.form.get("gender", "").strip() or None
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    errors = []

    if not staff_name or len(staff_name) > 100:
        errors.append("Enter a valid staff name (maximum 100 characters).")

    if not username or len(username) > 50:
        errors.append("Username is required and must be at most 50 characters.")

    if not email or len(email) > 100 or "@" not in email:
        errors.append("Enter a valid email address.")

    if phone and (len(phone) > 15 or not phone.replace("+", "").replace("-", "").replace(" ", "").isdigit()):
        errors.append("Enter a valid phone number.")

    if gender not in (None, "male", "female", "other"):
        errors.append("Select a valid gender.")

    if len(password) < 12:
        errors.append("Password must contain at least 12 characters.")

    if password != confirm_password:
        errors.append("Password confirmation does not match.")

    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(url_for("pharmacy.admin_members"))

    cursor = mysql.connection.cursor()

    try:
        # Lock and verify the dedicated role before creating the account.
        cursor.execute("""
            SELECT role_id
            FROM roles
            WHERE role_name = %s AND is_active = 1
            FOR UPDATE
        """, ("pharmacy_admin",))

        role_row = cursor.fetchone()

        if not role_row:
            mysql.connection.rollback()
            flash("The Pharmacy Admin role is missing or inactive.", "danger")
            return redirect(url_for("pharmacy.admin_members"))

        role_id = role_row["role_id"]

        # Explicit duplicate checks provide understandable feedback.
        cursor.execute("""
            SELECT user_id
            FROM users
            WHERE username = %s OR email = %s
            LIMIT 1
        """, (username, email))

        if cursor.fetchone():
            mysql.connection.rollback()
            flash("That username or email already belongs to an account.", "danger")
            return redirect(url_for("pharmacy.admin_members"))

        if phone:
            cursor.execute("""
                SELECT staff_id
                FROM staff
                WHERE phone = %s
                LIMIT 1
            """, (phone,))

            if cursor.fetchone():
                mysql.connection.rollback()
                flash("That phone number is already assigned to a staff member.", "danger")
                return redirect(url_for("pharmacy.admin_members"))

        password_hash = generate_password_hash(password)

        # Legacy enum compatibility:
        # Actual privileges are controlled by user_roles, not this field.
        cursor.execute("""
            INSERT INTO users (
                username,
                password_hash,
                role,
                email,
                is_active
            )
            VALUES (%s, %s, %s, %s, 1)
        """, (
            username,
            password_hash,
            "receptionist",
            email,
        ))

        new_user_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO staff (
                staff_name,
                gender,
                phone,
                email,
                designation,
                employment_status,
                user_id
            )
            VALUES (%s, %s, %s, %s, %s, 'active', %s)
        """, (
            staff_name,
            gender,
            phone,
            email,
            "Pharmacy Administrator",
            new_user_id,
        ))

        cursor.execute("""
            INSERT INTO user_roles (
                user_id,
                role_id,
                assigned_by
            )
            VALUES (%s, %s, %s)
        """, (
            new_user_id,
            role_id,
            actor["user_id"],
        ))

        mysql.connection.commit()

        current_app.logger.info(
            "Pharmacy Admin account created: user_id=%s, assigned_by=%s",
            new_user_id,
            actor["user_id"],
        )

        flash(
            "Pharmacy Admin account created successfully. "
            "The member can now sign in using the assigned credentials.",
            "success",
        )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Pharmacy Admin account creation failed.")
        flash(
            "Account creation failed. Check the application log for details. "
            "No partial account creation was committed.",
            "danger",
        )

    finally:
        cursor.close()

    return redirect(url_for("pharmacy.admin_members"))


@pharmacy_bp.post("/admin/members/<int:user_id>/toggle")
@login_required
def toggle_admin_member(user_id):
    """Activate or deactivate a Pharmacy Admin without deleting records."""

    actor = pharmacy_super_admin_required()

    if user_id == actor["user_id"]:
        flash("You cannot deactivate your own account here.", "danger")
        return redirect(url_for("pharmacy.admin_members"))

    cursor = mysql.connection.cursor()

    try:
        cursor.execute("""
            SELECT u.user_id, u.is_active
            FROM users u
            INNER JOIN user_roles ur
                ON ur.user_id = u.user_id
            INNER JOIN roles r
                ON r.role_id = ur.role_id
            WHERE u.user_id = %s
              AND r.role_name = %s
            FOR UPDATE
        """, (user_id, "pharmacy_admin"))

        member = cursor.fetchone()

        if not member:
            mysql.connection.rollback()
            flash("Pharmacy Admin member was not found.", "danger")
            return redirect(url_for("pharmacy.admin_members"))

        # Never use this Pharmacy Admin control to deactivate a Super Admin.
        cursor.execute("""
            SELECT 1
            FROM user_roles ur
            INNER JOIN roles r ON r.role_id = ur.role_id
            WHERE ur.user_id = %s
              AND r.role_name = %s
            LIMIT 1
        """, (user_id, "super_admin"))

        if cursor.fetchone():
            mysql.connection.rollback()
            flash("Super Admin accounts cannot be changed from this page.", "danger")
            return redirect(url_for("pharmacy.admin_members"))

        new_status = 0 if member["is_active"] else 1
        staff_status = "active" if new_status else "inactive"

        cursor.execute("""
            UPDATE users
            SET is_active = %s
            WHERE user_id = %s
        """, (new_status, user_id))

        cursor.execute("""
            UPDATE staff
            SET employment_status = %s
            WHERE user_id = %s
        """, (staff_status, user_id))

        mysql.connection.commit()

        flash(
            "Pharmacy Admin account " +
            ("activated." if new_status else "deactivated."),
            "success",
        )

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Pharmacy Admin status update failed.")
        flash("Could not update the member account status.", "danger")

    finally:
        cursor.close()

    return redirect(url_for("pharmacy.admin_members"))



# ============================================================
# PHARMACY ORDER STATUS WORKFLOW
# ============================================================

@pharmacy_bp.post("/admin/orders/<int:order_id>/status")
@login_required
def update_pharmacy_order_status(order_id):
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])
    target = (request.form.get("target_status") or "").strip()

    allowed = {
        "Requested": {"Approved", "Rejected", "Cancelled"},
        "Approved": {"Processing", "Cancelled"},
        "Processing": set(),
        "Ready": {"Delivered"},
        "Delivered": set(),
        "Rejected": set(),
        "Cancelled": set(),
    }

    cursor = mysql.connection.cursor()

    try:
        cursor.execute("START TRANSACTION")

        cursor.execute(
            """
            SELECT order_status
            FROM pharmacy_orders
            WHERE pharmacy_order_id = %s
            FOR UPDATE
            """,
            (order_id,),
        )
        order = cursor.fetchone()

        if not order:
            mysql.connection.rollback()
            abort(404)

        current_status = order["order_status"]

        if target not in allowed.get(current_status, set()):
            mysql.connection.rollback()
            flash("This order status transition is not permitted.", "warning")
            return redirect(url_for("pharmacy.admin_order_detail", order_id=order_id))

        cursor.execute(
            """
            UPDATE pharmacy_orders
            SET order_status = %s
            WHERE pharmacy_order_id = %s
              AND order_status = %s
            """,
            (target, order_id, current_status),
        )

        if cursor.rowcount != 1:
            raise RuntimeError("The order changed while it was being updated.")

        mysql.connection.commit()
        flash(f"Order #{order_id} updated to {target}.", "success")

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Pharmacy order status update failed for order %s.", order_id
        )
        flash("Unable to update this order. Please try again.", "danger")

    finally:
        cursor.close()

    return redirect(url_for("pharmacy.admin_order_detail", order_id=order_id))


# ============================================================
# PHARMACY DISPENSING
# Stock deduction and audit logging are one atomic operation.
# ============================================================

@pharmacy_bp.post("/admin/orders/<int:order_id>/dispense")
@login_required
def dispense_pharmacy_order(order_id):
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])
    if "pharmacy_admin" not in roles:
        abort(403)

    cursor = mysql.connection.cursor()

    try:
        cursor.execute("START TRANSACTION")

        cursor.execute(
            """
            SELECT staff_id
            FROM staff
            WHERE user_id = %s
              AND employment_status = 'active'
            LIMIT 1
            FOR UPDATE
            """,
            (user[0],),
        )
        staff = cursor.fetchone()

        if not staff:
            raise ValueError(
                "Dispensing requires an active staff account linked to your user."
            )

        staff_id = staff["staff_id"]

        cursor.execute(
            """
            SELECT pharmacy_order_id, patient_id,
                   prescription_document_id, order_status
            FROM pharmacy_orders
            WHERE pharmacy_order_id = %s
            FOR UPDATE
            """,
            (order_id,),
        )
        order = cursor.fetchone()

        if not order:
            abort(404)

        if order["order_status"] != "Processing":
            raise ValueError(
                "Only Processing orders can be dispensed. "
                "This order may already have been processed."
            )

        cursor.execute(
            """
            SELECT inventory_id, medicine_name, quantity
            FROM pharmacy_order_items
            WHERE pharmacy_order_id = %s
            ORDER BY inventory_id ASC, order_item_id ASC
            FOR UPDATE
            """,
            (order_id,),
        )
        order_items = cursor.fetchall()

        if not order_items:
            raise ValueError("This order contains no medicine items.")

        required = {}

        for item in order_items:
            inventory_id = item["inventory_id"]
            quantity = int(item["quantity"])

            if inventory_id is None:
                raise ValueError(
                    f"Medicine '{item['medicine_name']}' has no inventory link."
                )

            if quantity <= 0:
                raise ValueError(
                    f"Invalid quantity for '{item['medicine_name']}'."
                )

            required[inventory_id] = required.get(inventory_id, 0) + quantity

        today = date.today()
        inventory_rows = {}
        prescription_required = False

        # Lock and validate every medicine before changing any stock.
        for inventory_id in sorted(required):
            cursor.execute(
                """
                SELECT inventory_id, item_name, item_category,
                       quantity, unit, expiry_date, is_active,
                       reorder_level, requires_prescription
                FROM inventory
                WHERE inventory_id = %s
                FOR UPDATE
                """,
                (inventory_id,),
            )
            medicine = cursor.fetchone()

            if not medicine:
                raise ValueError(
                    f"Inventory item #{inventory_id} no longer exists."
                )

            if medicine["item_category"] != "Medicine":
                raise ValueError(
                    f"{medicine['item_name']} is not a medicine inventory item."
                )

            if not medicine["is_active"]:
                raise ValueError(f"{medicine['item_name']} is inactive.")

            expiry = medicine["expiry_date"]
            if expiry and expiry < today:
                raise ValueError(
                    f"{medicine['item_name']} has expired. Dispensing stopped."
                )

            available = int(medicine["quantity"])
            needed = required[inventory_id]

            if available < needed:
                raise ValueError(
                    f"Insufficient stock for {medicine['item_name']}. "
                    f"Required: {needed}; available: {available}."
                )

            if medicine["requires_prescription"]:
                prescription_required = True

            inventory_rows[inventory_id] = medicine

        # Validate the prescription against this order's patient.
        prescription_id = order["prescription_document_id"]
        prescription = None

        if prescription_id:
            cursor.execute(
                """
                SELECT document_id, patient_id, document_type
                FROM patient_documents
                WHERE document_id = %s
                  AND patient_id = %s
                  AND document_type = 'prescription'
                LIMIT 1
                FOR UPDATE
                """,
                (prescription_id, order["patient_id"]),
            )
            prescription = cursor.fetchone()

            if not prescription:
                raise ValueError(
                    "The linked prescription is missing, invalid, "
                    "or does not belong to this patient."
                )

        if prescription_required and not prescription:
            raise ValueError(
                "This order contains prescription-required medicine, "
                "but no valid patient prescription is attached."
            )

        reviewed = request.form.get("confirm_prescription_review") == "1"

        if (prescription_required or prescription) and not reviewed:
            raise ValueError(
                "Confirm that the prescription has been reviewed before dispensing."
            )

        # All checks passed. Deduct stock and write audit records atomically.
        for inventory_id in sorted(required):
            medicine = inventory_rows[inventory_id]
            needed = required[inventory_id]
            remaining = int(medicine["quantity"]) - needed

            cursor.execute(
                """
                UPDATE inventory
                SET quantity = %s,
                    stock_status = CASE
                        WHEN %s = 0 THEN 'out_of_stock'
                        WHEN reorder_level IS NOT NULL
                             AND %s <= reorder_level THEN 'low_stock'
                        ELSE 'available'
                    END
                WHERE inventory_id = %s
                """,
                (remaining, remaining, remaining, inventory_id),
            )

            remarks = (
                f"Pharmacy order #{order_id}; "
                f"dispensed by user #{user['user_id']}"
            )

            if prescription_required or prescription:
                remarks += "; prescription reviewed by dispenser"

            cursor.execute(
                """
                INSERT INTO inventory_transactions
                    (inventory_id, staff_id, transaction_type,
                     quantity, remarks)
                VALUES (%s, %s, 'stock_out', %s, %s)
                """,
                (inventory_id, staff_id, needed, remarks),
            )

        cursor.execute(
            """
            UPDATE pharmacy_orders
            SET order_status = 'Ready'
            WHERE pharmacy_order_id = %s
              AND order_status = 'Processing'
            """,
            (order_id,),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "The order status changed during dispensing."
            )

        mysql.connection.commit()

        flash(
            f"Order #{order_id} dispensed successfully. "
            "Stock and audit records were updated.",
            "success",
        )

    except HTTPException:
        mysql.connection.rollback()
        raise

    except ValueError as exc:
        mysql.connection.rollback()
        flash(str(exc), "danger")

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception(
            "Pharmacy dispensing failed for order %s.", order_id
        )
        flash(
            "Dispensing failed. No stock changes were committed. "
            "Check the application log.",
            "danger",
        )

    finally:
        cursor.close()

    return redirect(
        url_for("pharmacy.admin_order_detail", order_id=order_id)
    )

# ============================================================
# MODULE 4 — PHARMACY INVENTORY MANAGEMENT
# ============================================================

from decimal import Decimal, InvalidOperation


def pharmacy_inventory_status(quantity, reorder_level):
    quantity = int(quantity or 0)
    reorder_level = int(reorder_level or 0)

    if quantity <= 0:
        return "out_of_stock"
    if quantity <= reorder_level:
        return "low_stock"
    return "available"


@pharmacy_bp.get("/admin/inventory")
@login_required
def admin_inventory():
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])

    search = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 10

    where = ["item_category = 'Medicine'"]
    params = []

    if search:
        where.append("""
            (item_name LIKE %s OR generic_name LIKE %s
             OR brand_name LIKE %s OR manufacturer LIKE %s)
        """)
        term = f"%{search}%"
        params.extend([term, term, term, term])

    if status in ("available", "low_stock", "out_of_stock"):
        where.append("stock_status = %s")
        params.append(status)

    where_sql = " AND ".join(where)

    total = pharmacy_scalar(
        f"SELECT COUNT(*) FROM inventory WHERE {where_sql}",
        tuple(params)
    ) or 0

    total_pages = max((int(total) + per_page - 1) // per_page, 1)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    medicines = pharmacy_fetch_all(
        f"""
        SELECT inventory_id, item_name, generic_name, brand_name,
               strength, dosage_form, quantity, unit, reorder_level,
               unit_price, purchase_price, supplier_name, expiry_date,
               stock_status, is_active, medicine_image
        FROM inventory
        WHERE {where_sql}
        ORDER BY
            CASE stock_status
                WHEN 'out_of_stock' THEN 1
                WHEN 'low_stock' THEN 2
                ELSE 3
            END,
            item_name ASC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [per_page, offset])
    )

    summary = pharmacy_fetch_one("""
        SELECT
            COUNT(*) AS medicine_count,
            COALESCE(SUM(quantity), 0) AS total_units,
            COALESCE(SUM(quantity * purchase_price), 0) AS stock_value,
            SUM(CASE WHEN stock_status = 'low_stock'
                     AND is_active = 1 THEN 1 ELSE 0 END) AS low_stock,
            SUM(CASE WHEN stock_status = 'out_of_stock'
                     AND is_active = 1 THEN 1 ELSE 0 END) AS out_of_stock
        FROM inventory
        WHERE item_category = 'Medicine'
    """) or {}

    return render_template(
        "pharmacy/admin_inventory.html",
        user=user,
        roles=roles,
        nav_items=pharmacy_admin_navigation(),
        is_super_admin=("super_admin" in roles),
        medicines=medicines,
        summary=summary,
        search=search,
        status=status,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@pharmacy_bp.route(
    "/admin/inventory/<int:inventory_id>/stock",
    methods=["GET", "POST"]
)
@login_required
def manage_medicine_stock(inventory_id):
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])

    medicine = pharmacy_fetch_one("""
        SELECT inventory_id, item_name, generic_name, brand_name,
               quantity, unit, reorder_level, purchase_price,
               supplier_name, expiry_date, stock_status, is_active
        FROM inventory
        WHERE inventory_id = %s AND item_category = 'Medicine'
    """, (inventory_id,))

    if not medicine:
        abort(404)

    if request.method == "POST":
        operation = (request.form.get("operation") or "").strip()
        remarks = (request.form.get("remarks") or "").strip()[:255]

        if operation not in ("stock_in", "adjustment"):
            flash("Choose a valid inventory operation.", "danger")
            return redirect(url_for(
                "pharmacy.manage_medicine_stock",
                inventory_id=inventory_id
            ))

        try:
            amount = int(request.form.get("quantity", ""))
            if amount <= 0:
                raise ValueError
        except (TypeError, ValueError):
            flash("Enter a valid quantity greater than zero.", "danger")
            return redirect(url_for(
                "pharmacy.manage_medicine_stock",
                inventory_id=inventory_id
            ))

        purchase_price = None
        if operation == "stock_in":
            raw_price = (request.form.get("purchase_price") or "").strip()
            if raw_price:
                try:
                    purchase_price = Decimal(raw_price)
                    if not purchase_price.is_finite() or purchase_price < 0:
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    flash("Enter a valid non-negative purchase price.", "danger")
                    return redirect(url_for(
                        "pharmacy.manage_medicine_stock",
                        inventory_id=inventory_id
                    ))

        conn = mysql.connection
        cursor = conn.cursor()

        try:
            conn.begin()

            # Lock the medicine row to prevent simultaneous stock updates.
            cursor.execute("""
                SELECT quantity, reorder_level, is_active
                FROM inventory
                WHERE inventory_id = %s
                  AND item_category = 'Medicine'
                FOR UPDATE
            """, (inventory_id,))
            locked = cursor.fetchone()

            if not locked:
                raise ValueError("Medicine record no longer exists.")

            current_quantity = int(locked[0] or 0)
            reorder_level = int(locked[1] or 0)
            is_active = int(locked[2] or 0)

            if not is_active:
                raise ValueError(
                    "This medicine is inactive. Activate it before changing stock."
                )

            if operation == "stock_in":
                new_quantity = current_quantity + amount
                transaction_quantity = amount
                transaction_type = "stock_in"
            else:
                # Adjustment quantity is the new physical count.
                new_quantity = amount
                transaction_quantity = new_quantity - current_quantity
                transaction_type = "adjustment"

            if new_quantity < 0:
                raise ValueError("Stock cannot be negative.")

            new_status = pharmacy_inventory_status(
                new_quantity, reorder_level
            )

            if operation == "stock_in" and purchase_price is not None:
                cursor.execute("""
                    UPDATE inventory
                    SET quantity = %s,
                        purchase_price = %s,
                        supplier_name = COALESCE(NULLIF(%s, ''), supplier_name),
                        stock_status = %s
                    WHERE inventory_id = %s
                """, (
                    new_quantity,
                    purchase_price,
                    (request.form.get("supplier_name") or "").strip()[:150],
                    new_status,
                    inventory_id
                ))
            elif operation == "stock_in":
                cursor.execute("""
                    UPDATE inventory
                    SET quantity = %s,
                        supplier_name = COALESCE(NULLIF(%s, ''), supplier_name),
                        stock_status = %s
                    WHERE inventory_id = %s
                """, (
                    new_quantity,
                    (request.form.get("supplier_name") or "").strip()[:150],
                    new_status,
                    inventory_id
                ))
            else:
                cursor.execute("""
                    UPDATE inventory
                    SET quantity = %s, stock_status = %s
                    WHERE inventory_id = %s
                """, (new_quantity, new_status, inventory_id))

            # Record the change in the existing audit table.
            cursor.execute("""
                SELECT staff_id
                FROM staff
                WHERE user_id = %s
                  AND employment_status = 'active'
                LIMIT 1
                FOR UPDATE
            """, (user["user_id"],))
            staff = cursor.fetchone()

            if not staff:
                raise ValueError(
                    "Your account must be linked to an active staff record "
                    "before inventory operations can be performed."
                )

            audit_remarks = remarks or (
                "Stock received" if operation == "stock_in"
                else "Physical stock count adjustment"
            )

            cursor.execute("""
                INSERT INTO inventory_transactions
                    (inventory_id, staff_id, transaction_type,
                     quantity, transaction_date, remarks)
                VALUES (%s, %s, %s, %s, NOW(), %s)
            """, (
                inventory_id,
                staff[0],
                transaction_type,
                transaction_quantity,
                audit_remarks
            ))

            conn.commit()
            flash(
                f"Inventory updated successfully. New stock: {new_quantity}.",
                "success"
            )
            return redirect(url_for("pharmacy.admin_inventory"))

        except HTTPException:
            conn.rollback()
            raise
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "danger")
        except Exception:
            conn.rollback()
            current_app.logger.exception(
                "Pharmacy inventory update failed for item %s",
                inventory_id
            )
            flash("Inventory update failed. No stock changes were saved.", "danger")
        finally:
            cursor.close()

        return redirect(url_for(
            "pharmacy.manage_medicine_stock",
            inventory_id=inventory_id
        ))

    return render_template(
        "pharmacy/admin_stock_form.html",
        user=user,
        roles=roles,
        nav_items=pharmacy_admin_navigation(),
        is_super_admin=("super_admin" in roles),
        medicine=medicine,
    )

# ============================================================
# MODULE 5 — PHARMACY STOCK TRANSACTIONS
# ============================================================

@pharmacy_bp.get("/admin/transactions")
@login_required
def admin_transactions():
    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])

    search = (request.args.get("q") or "").strip()
    transaction_type = (
        request.args.get("type") or "all"
    ).strip().lower()

    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()

    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 15

    allowed_types = {"stock_in", "stock_out", "adjustment"}
    if transaction_type not in allowed_types:
        transaction_type = "all"

    # Validate date filters before using them in SQL.
    try:
        if start_date:
            date.fromisoformat(start_date)
        if end_date:
            date.fromisoformat(end_date)
        if start_date and end_date and start_date > end_date:
            raise ValueError
    except ValueError:
        flash("Please select a valid date range.", "warning")
        return redirect(url_for("pharmacy.admin_transactions"))

    conditions = ["i.item_category = 'Medicine'"]
    params = []

    if transaction_type != "all":
        conditions.append("t.transaction_type = %s")
        params.append(transaction_type)

    if start_date:
        conditions.append("t.transaction_date >= %s")
        params.append(f"{start_date} 00:00:00")

    if end_date:
        conditions.append("t.transaction_date < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(end_date)

    if search:
        conditions.append("""
            (
                i.item_name LIKE %s
                OR i.generic_name LIKE %s
                OR i.brand_name LIKE %s
                OR t.remarks LIKE %s
                OR CAST(t.transaction_id AS CHAR) = %s
                OR CAST(t.staff_id AS CHAR) = %s
            )
        """)
        term = f"%{search}%"
        params.extend([
            term, term, term, term,
            search, search
        ])

    where_sql = " AND ".join(conditions)

    total = pharmacy_scalar(
        f"""
        SELECT COUNT(*)
        FROM inventory_transactions t
        INNER JOIN inventory i
            ON i.inventory_id = t.inventory_id
        WHERE {where_sql}
        """,
        tuple(params)
    ) or 0

    total = int(total)
    total_pages = max((total + per_page - 1) // per_page, 1)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    transactions = pharmacy_fetch_all(
        f"""
        SELECT
            t.transaction_id,
            t.inventory_id,
            t.staff_id,
            t.transaction_type,
            t.quantity,
            t.transaction_date,
            t.remarks,
            i.item_name,
            i.generic_name,
            i.brand_name,
            i.strength,
            i.dosage_form
        FROM inventory_transactions t
        INNER JOIN inventory i
            ON i.inventory_id = t.inventory_id
        WHERE {where_sql}
        ORDER BY t.transaction_date DESC, t.transaction_id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [per_page, offset])
    )

    # Summary figures respect the selected date range.
    summary_conditions = ["i.item_category = 'Medicine'"]
    summary_params = []

    if start_date:
        summary_conditions.append("t.transaction_date >= %s")
        summary_params.append(f"{start_date} 00:00:00")

    if end_date:
        summary_conditions.append(
            "t.transaction_date < DATE_ADD(%s, INTERVAL 1 DAY)"
        )
        summary_params.append(end_date)

    summary_where = " AND ".join(summary_conditions)

    summary = pharmacy_fetch_one(
        f"""
        SELECT
            COUNT(*) AS total_transactions,
            COALESCE(SUM(
                CASE WHEN t.transaction_type = 'stock_in'
                     THEN 1 ELSE 0 END
            ), 0) AS stock_in_count,
            COALESCE(SUM(
                CASE WHEN t.transaction_type = 'stock_out'
                     THEN 1 ELSE 0 END
            ), 0) AS stock_out_count,
            COALESCE(SUM(
                CASE WHEN t.transaction_type = 'adjustment'
                     THEN 1 ELSE 0 END
            ), 0) AS adjustment_count
        FROM inventory_transactions t
        INNER JOIN inventory i
            ON i.inventory_id = t.inventory_id
        WHERE {summary_where}
        """,
        tuple(summary_params)
    ) or {}

    return render_template(
        "pharmacy/admin_transactions.html",
        user=user,
        roles=roles,
        nav_items=pharmacy_admin_navigation(),
        is_super_admin=("super_admin" in roles),
        transactions=transactions,
        summary=summary,
        search=search,
        transaction_type=transaction_type,
        start_date=start_date,
        end_date=end_date,
        page=page,
        total_pages=total_pages,
        total=total,
    )

# ============================================================
# MODULE 3 — PRESCRIPTION REVIEW
# Read-only review queue with secure document access.
# Existing database schema is preserved.
# ============================================================

PHARMACY_PRESCRIPTION_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "webp"
}


def get_pharmacy_prescription(document_id):
    """Return a prescription document with patient information."""

    return pharmacy_fetch_one(
        """
        SELECT
            pd.document_id,
            pd.patient_id,
            pd.uploaded_by_user_id,
            pd.document_type,
            pd.original_filename,
            pd.stored_filename,
            pd.mime_type,
            pd.file_size,
            pd.created_at,

            p.first_name,
            p.last_name,
            p.phone,
            p.email,
            p.address,
            p.blood_group,

            u.username AS uploaded_by

        FROM patient_documents pd

        INNER JOIN patients p
            ON p.patient_id = pd.patient_id

        LEFT JOIN users u
            ON u.user_id = pd.uploaded_by_user_id

        WHERE pd.document_id = %s
          AND pd.document_type = 'prescription'

        LIMIT 1
        """,
        (document_id,)
    )


def pharmacy_prescription_file_path(document):
    """
    Resolve a stored prescription file safely.

    Files are served only through authenticated routes.
    """

    upload_folder = current_app.config.get(
        "PATIENT_UPLOAD_FOLDER"
    )

    if not upload_folder:
        current_app.logger.error(
            "PATIENT_UPLOAD_FOLDER is not configured."
        )
        abort(503)

    stored_filename = document.get("stored_filename")

    if not stored_filename:
        abort(404)

    # Never accept directory traversal or arbitrary paths.
    if os.path.basename(stored_filename) != stored_filename:
        abort(404)

    if "/" in stored_filename or "\\" in stored_filename:
        abort(404)

    extension = (
        stored_filename.rsplit(".", 1)[-1].lower()
        if "." in stored_filename else ""
    )

    if extension not in PHARMACY_PRESCRIPTION_EXTENSIONS:
        abort(415)

    root = os.path.realpath(upload_folder)
    file_path = os.path.realpath(
        os.path.join(root, stored_filename)
    )

    try:
        if os.path.commonpath([root, file_path]) != root:
            abort(404)
    except ValueError:
        abort(404)

    if not os.path.isfile(file_path):
        abort(404)

    return file_path, extension


@pharmacy_bp.get("/admin/prescriptions")
@login_required
def admin_prescriptions():
    """Searchable Pharmacy Admin prescription review queue."""

    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])

    search = (
        request.args.get("q", "").strip()
    )

    linkage = (
        request.args.get("linkage", "all").strip().lower()
    )

    if linkage not in {"all", "linked", "unlinked"}:
        linkage = "all"

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    per_page = 15

    conditions = [
        "pd.document_type = 'prescription'"
    ]
    params = []

    if search:
        conditions.append("""
            (
                pd.original_filename LIKE %s
                OR CAST(pd.document_id AS CHAR) LIKE %s
                OR CAST(pd.patient_id AS CHAR) LIKE %s
                OR p.first_name LIKE %s
                OR p.last_name LIKE %s
                OR CONCAT_WS(' ', p.first_name, p.last_name) LIKE %s
                OR u.username LIKE %s
            )
        """)

        term = f"%{search}%"
        params.extend([term] * 7)

    if linkage == "linked":
        conditions.append("""
            EXISTS (
                SELECT 1
                FROM pharmacy_orders po
                WHERE po.prescription_document_id = pd.document_id
            )
        """)

    elif linkage == "unlinked":
        conditions.append("""
            NOT EXISTS (
                SELECT 1
                FROM pharmacy_orders po
                WHERE po.prescription_document_id = pd.document_id
            )
        """)

    where_sql = " AND ".join(conditions)

    joins = """
        FROM patient_documents pd

        INNER JOIN patients p
            ON p.patient_id = pd.patient_id

        LEFT JOIN users u
            ON u.user_id = pd.uploaded_by_user_id
    """

    total = pharmacy_scalar(
        f"""
        SELECT COUNT(*)
        {joins}
        WHERE {where_sql}
        """,
        tuple(params)
    ) or 0

    total = int(total)

    total_pages = max(
        1,
        (total + per_page - 1) // per_page
    )

    page = min(page, total_pages)
    offset = (page - 1) * per_page

    prescriptions = pharmacy_fetch_all(
        f"""
        SELECT
            pd.document_id,
            pd.patient_id,
            pd.original_filename,
            pd.mime_type,
            pd.file_size,
            pd.created_at,

            p.first_name,
            p.last_name,
            p.phone,

            u.username AS uploaded_by,

            (
                SELECT COUNT(*)
                FROM pharmacy_orders po
                WHERE po.prescription_document_id = pd.document_id
            ) AS linked_order_count,

            (
                SELECT po.pharmacy_order_id
                FROM pharmacy_orders po
                WHERE po.prescription_document_id = pd.document_id
                ORDER BY po.ordered_at DESC,
                         po.pharmacy_order_id DESC
                LIMIT 1
            ) AS latest_order_id,

            (
                SELECT po.order_status
                FROM pharmacy_orders po
                WHERE po.prescription_document_id = pd.document_id
                ORDER BY po.ordered_at DESC,
                         po.pharmacy_order_id DESC
                LIMIT 1
            ) AS latest_order_status

        {joins}

        WHERE {where_sql}

        ORDER BY pd.created_at DESC,
                 pd.document_id DESC

        LIMIT %s OFFSET %s
        """,
        tuple(params) + (per_page, offset)
    )

    summary = pharmacy_fetch_one("""
        SELECT
            COUNT(*) AS total_prescriptions,

            COALESCE(SUM(
                CASE WHEN DATE(created_at) = CURDATE()
                THEN 1 ELSE 0 END
            ), 0) AS uploaded_today,

            COALESCE(SUM(
                CASE WHEN created_at >=
                    DATE_SUB(NOW(), INTERVAL 7 DAY)
                THEN 1 ELSE 0 END
            ), 0) AS uploaded_last_seven_days

        FROM patient_documents

        WHERE document_type = 'prescription'
    """) or {}

    return render_template(
        "pharmacy/admin_prescriptions.html",
        user=user,
        roles=roles,
        is_super_admin="super_admin" in roles,
        nav_items=pharmacy_admin_navigation(),
        prescriptions=prescriptions,
        summary=summary,
        search=search,
        linkage=linkage,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@pharmacy_bp.get("/admin/prescriptions/<int:document_id>")
@login_required
def admin_prescription_detail(document_id):
    """Prescription examination workspace."""

    user = pharmacy_admin_required()
    roles = get_user_roles(user[0])

    prescription = get_pharmacy_prescription(document_id)

    if not prescription:
        abort(404)

    linked_orders = pharmacy_fetch_all(
        """
        SELECT
            pharmacy_order_id,
            order_status,
            delivery_method,
            delivery_location,
            subtotal,
            ordered_at,
            updated_at

        FROM pharmacy_orders

        WHERE prescription_document_id = %s

        ORDER BY ordered_at DESC,
                 pharmacy_order_id DESC
        """,
        (document_id,)
    )

    file_path, extension = pharmacy_prescription_file_path(
        prescription
    )

    file_size = os.path.getsize(file_path)

    preview_supported = extension in {
        "pdf", "png", "jpg", "jpeg", "webp"
    }

    return render_template(
        "pharmacy/admin_prescription_viewer.html",
        user=user,
        roles=roles,
        is_super_admin="super_admin" in roles,
        nav_items=pharmacy_admin_navigation(),
        prescription=prescription,
        linked_orders=linked_orders,
        file_size=file_size,
        extension=extension.upper(),
        preview_supported=preview_supported,
    )


def pharmacy_send_prescription(document_id, as_attachment):
    """Shared authorization and file-serving implementation."""

    pharmacy_admin_required()

    prescription = get_pharmacy_prescription(document_id)

    if not prescription:
        abort(404)

    file_path, extension = pharmacy_prescription_file_path(
        prescription
    )

    # Use the allowed extension to determine the response MIME type.
    mime_type = {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(extension)

    if not mime_type:
        abort(415)

    original_name = (
        prescription.get("original_filename")
        or f"prescription-{document_id}.{extension}"
    )

    response = send_file(
        file_path,
        mimetype=mime_type,
        as_attachment=as_attachment,
        download_name=os.path.basename(original_name),
        conditional=True,
        max_age=0,
    )

    response.headers["Cache-Control"] = (
        "private, no-store, no-cache, must-revalidate"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"

    if not as_attachment and extension == "pdf":
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "frame-ancestors 'self'; "
            "sandbox"
        )

    return response


@pharmacy_bp.get(
    "/admin/prescriptions/<int:document_id>/file"
)
@login_required
def admin_prescription_file(document_id):
    """Authenticated inline prescription preview."""

    return pharmacy_send_prescription(
        document_id,
        as_attachment=False
    )


@pharmacy_bp.get(
    "/admin/prescriptions/<int:document_id>/download"
)
@login_required
def admin_prescription_download(document_id):
    """Authenticated prescription download."""

    return pharmacy_send_prescription(
        document_id,
        as_attachment=True
    )

